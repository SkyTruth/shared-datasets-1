"""Weekly audit health uses successful, authenticated completion age."""
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import operational_safety_check as safety
from test_operational_safety_check import NOW, OperationalAPI, stamp


def audit_api():
    api = OperationalAPI()
    api.definitions['scratch-cleanup-audit.yml'] = {'id': 18, 'path': '.github/workflows/scratch-cleanup-audit.yml'}
    for identifier, filename in enumerate(('bucket-hygiene-audit.yml', 'scratch-cleanup-audit.yml'), 101):
        api.add_source(api.source(identifier, filename, conclusion='success'))
    return api


@pytest.mark.parametrize('hours,stale', [(0, False), (215, False), (216, True), (230, True)])
def test_audits_use_last_successful_completion_with_two_day_grace(hours, stale):
    api = audit_api()
    api.sources[0]['updated_at'] = stamp(NOW - timedelta(hours=hours))
    expected = ['bucket-hygiene-audit.yml: no successful main completion within 9 days'] if stale else []
    assert safety.audit_freshness(api, now=NOW) == expected
    assert api.posts == []


def test_audit_discovery_reads_all_conclusions_and_verifies_the_recent_success():
    api = audit_api()
    api.add_source(api.source(103, 'scratch-cleanup-audit.yml', conclusion='failure'))
    api.sources.reverse()  # GitHub lists the newer failed run first.
    assert safety.audit_freshness(api, now=NOW) == []
    queries = [parse_qs(urlsplit(path).query) for method, path in api.calls if method == 'pages']
    expected_query = {'branch': ['main'], 'per_page': ['100'], 'created': ['>=' + stamp(NOW - timedelta(days=10))]}
    assert queries == [expected_query, expected_query]
    assert ('get', f'{safety.API_ROOT}/actions/runs/102') in api.calls
    assert ('get', f'{safety.API_ROOT}/actions/runs/103') not in api.calls


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', 'skipped', None])
def test_newer_unsuccessful_or_missing_audit_cannot_hide_a_missed_success(conclusion):
    api = audit_api()
    api.sources[0]['conclusion'] = conclusion
    assert safety.audit_freshness(api, now=NOW) == ['bucket-hygiene-audit.yml: no successful main completion within 9 days']
    api.sources = []
    assert len(safety.audit_freshness(api, now=NOW)) == 2


@pytest.mark.parametrize('defect', ['repository', 'revision', 'definition', 'read'])
def test_untrusted_or_unavailable_audit_evidence_fails_visibly(defect):
    api = audit_api()
    if defect == 'repository':
        api.sources[0]['head_repository']['full_name'] = 'someone/fork'
    elif defect == 'revision':
        api.runs[101] = {**api.sources[0], 'head_sha': 'b' * 40}
    elif defect == 'definition':
        api.definitions['bucket-hygiene-audit.yml']['path'] = '.github/workflows/ci.yml'
    else:
        api.fail_on = '/runs?'
    with pytest.raises((ValueError, RuntimeError)):
        safety.audit_freshness(api, now=NOW)


def test_retained_probe_failure_report_never_creates_a_second_incident_but_dispatch_failure_does():
    from scripts import workflow_failure_alert as alerts
    run = {'name': 'Operations / Safety check'}
    steps = [{'name': 'Check operational work', 'conclusion': 'failure'},
             {'name': 'Check freshness and stalled operations', 'conclusion': 'success'},
             {'name': 'Retain safety health report', 'conclusion': 'success'}]
    assert alerts.safety_health_only(run, [{'steps': steps}])
    assert not alerts.safety_health_only(run, [{'steps': steps[:-1]}])
    assert not alerts.safety_health_only(run, [{'steps': steps + [{'name': 'Request catalog refresh', 'conclusion': 'failure'}]}])


@pytest.mark.parametrize('fallback,late_failure,covered', [
    ('success', False, True), ('failure', False, False), ('cancelled', False, False),
    ('skipped', False, False), (None, False, False), ('success', True, False),
])
def test_timed_out_safety_has_one_reporter_only_after_direct_delivery(fallback, late_failure, covered):
    from scripts import slack_incidents as incidents
    from scripts import workflow_failure_alert as alerts
    api = OperationalAPI()
    run = api.source(101, 'operational-safety-check.yml', conclusion='cancelled')
    api.add_source(run)
    job = api.jobs[101][0]
    job['name'] = safety.JOB
    job['steps'] = [
        {'name': 'Check operational work', 'conclusion': 'success'},
        {'name': 'Check freshness and stalled operations', 'conclusion': 'cancelled'},
        {'name': 'Retain safety health report', 'conclusion': 'failure'},
    ]
    if fallback is not None:
        job['steps'].append({'name': 'Report safety failure directly', 'conclusion': fallback})
    if late_failure:
        job['steps'].append({'name': 'Post setup', 'conclusion': 'failure'})
    assert alerts.safety_health_only(run, [job]) is covered
    observations = incidents.source_observations(api, {
        'repository': {'full_name': safety.REPOSITORY}, 'workflow_run': run})
    assert bool(observations) is (not covered)
    assert api.posts == []
