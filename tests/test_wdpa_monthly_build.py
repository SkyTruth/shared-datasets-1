"""Automatic monthly ingestion retains the full-build validation boundary."""
import copy
import datetime as dt
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ingestion.common import publication as p
from ingestion.wdpa_monthly import automatic, build as producer, run as wdpa
from scripts import wdpa_processing_gate as gate
from test_wdpa_execution_observer import accepted_evidence


@pytest.mark.parametrize('blocked', [True, False])
@pytest.mark.parametrize('ambiguity_count', [1, 2])
def test_replay_preserves_typed_blocked_outcome_across_cli_report(monkeypatch, tmp_path, blocked, ambiguity_count):
    from ingestion.common.feature_metadata import IdentityDecisionRequired

    source = tmp_path / 'source.zip'
    source.write_bytes(b'frozen source')
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / 'translation-sources.json').write_text('{}')
    (inputs / 'pins.json').write_text('{}')
    directory = tmp_path / 'replay'
    # Exercise the real CLI in a child process and its real benchmark writer.
    # Replace only the native source boundary, before any cloud calls or builds.
    harness = '''
import sys
from scripts import local_wdpa_sample as cli
from ingestion.common.feature_metadata import IdentityDecisionRequired
cli.prepare_scratch = lambda: None
cli.wdpa.native_versions = lambda: {}
blocked = sys.argv[1] == 'blocked'
ambiguity_count = int(sys.argv[2])
def source_boundary(*args):
    if blocked:
        raise IdentityDecisionRequired('review needed', asset_slug='wdpa-marine',
            release='2026-11-01', ambiguity_count=ambiguity_count)
    raise RuntimeError('broken native source')
cli.wdpa.prepare_source_datasets = source_boundary
sys.argv = ['local_wdpa_sample.py', *sys.argv[3:]]
cli.main()
'''
    run_process = subprocess.run
    def command(argv, *, check):
        return run_process([argv[0], '-c', harness, 'blocked' if blocked else 'failed', str(ambiguity_count), *argv[2:]], check=check)
    monkeypatch.setattr(producer.subprocess, 'run', command)
    expected_error = IdentityDecisionRequired if blocked else subprocess.CalledProcessError
    with pytest.raises(expected_error) as caught:
        producer.replay(source, inputs, directory, '2026-11-01', sample=True)
    report = json.loads((directory / 'benchmark.json').read_text())
    if blocked:
        assert report['state'] == 'blocked'
        assert report['blocked_record'] == caught.value.blocked_record()
        assert caught.value.blocked_record()['awaiting_decisions'] == ambiguity_count
    else:
        assert report['state'] == 'failed'
        assert 'blocked_record' not in report


@pytest.mark.parametrize('change', [
    {'reason': 'download_failed'}, {'asset_slug': 'unmanaged'},
    {'release': '2026-10-01'}, {'awaiting_decisions': 0},
    {'awaiting_decisions': True},
])
def test_replay_rejects_invalid_serialized_blocked_outcome(monkeypatch, tmp_path, change):
    from ingestion.common.feature_metadata import IdentityDecisionRequired

    record = IdentityDecisionRequired('review needed', asset_slug='wdpa-marine',
        release='2026-11-01', ambiguity_count=2).blocked_record()
    record.update(change)
    directory = tmp_path / 'replay'
    def command(argv, *, check):
        directory.mkdir()
        (directory / 'benchmark.json').write_text(json.dumps({
            'state': 'blocked', 'blocked_record': record,
        }))
    monkeypatch.setattr(producer.subprocess, 'run', command)
    with pytest.raises(p.PublicationError, match='invalid identity-decision blocked report'):
        producer.replay(tmp_path / 'source.zip', tmp_path / 'inputs', directory,
                        '2026-11-01', sample=True)


def test_monthly_acceptance_uses_independent_counts_and_matching_input_month():
    evidence = accepted_evidence()
    report = evidence['build']
    report['run_date'] = evidence['compatibility_sample']['run_date'] = '2026-11-01'
    report['cloud_execution'] = 'wdpa-monthly-aaaaa'
    report['cloud_image'] = report['cloud_image'].replace('/wdpa-validation@', '/wdpa-monthly@')
    for asset in report['assets'].values():
        asset['rows'] += 20
        asset['source_counts']['rows'] += 20
    assert not gate.check_build(report, require_bundle=False)
    assert not gate.check_compatibility(evidence, [report])
    report['assets']['wdpa-marine']['rows'] -= 1
    assert any('independently counted' in error for error in gate.check_build(report, require_bundle=False))
    evidence['compatibility_sample']['run_date'] = '2026-10-01'
    assert any('run_date' in error for error in gate.check_compatibility(evidence, [report]))


@pytest.fixture
def scheduled(monkeypatch, tmp_path):
    monkeypatch.setenv('RUN_DATE', '2026-11-02')
    monkeypatch.setenv('CLOUD_RUN_EXECUTION', 'wdpa-monthly-aaaaa')
    monkeypatch.delenv('WDPA_PROMOTION_BUNDLE', raising=False)
    monkeypatch.setattr(wdpa, 'prepare_scratch', lambda: None)
    monkeypatch.setattr(wdpa, 'work_root', lambda: tmp_path)
    monkeypatch.setattr(wdpa, 'parse_sample_spec', lambda: None)
    client = Mock()
    monkeypatch.setattr(automatic.storage, 'Client', lambda **kwargs: client)
    publisher = Mock()
    publisher.resume.return_value = None
    publisher.state.return_value = SimpleNamespace(value={'active': None, 'current': {'release': '2026-10-01'}})
    publisher.load_successful_run_record.return_value = None
    publisher.update_latest_run_index.return_value = {}
    monkeypatch.setattr(wdpa.GcsPublisher, 'from_runtime', lambda *args, **kwargs: publisher)
    reference = {'uri': 'gs://skytruth-shared-datasets-1/_scratch/wdpa-builds/wdpa-monthly-aaaaa/build-bundle.json',
                 'generation': 1, 'size': 10, 'sha256': 'a' * 64}
    builder = Mock(return_value=reference)
    promotion = Mock(return_value=[{'status': 'success'}])
    monkeypatch.setattr(producer, 'build_month', builder)
    monkeypatch.setattr(automatic.artifact_bundle, 'promote', promotion)
    return SimpleNamespace(client=client, publisher=publisher, build=builder, promote=promotion, reference=reference)


def test_new_month_builds_then_publishes_without_acceptance_or_deployment(scheduled):
    assert automatic.run() == [{'status': 'success'}]
    assert scheduled.build.call_args.args[:2] == (scheduled.client, dt.date(2026, 11, 1))
    assert scheduled.promote.call_args.args[2] == scheduled.reference
    assert scheduled.promote.call_args.kwargs == {'producer_source': gate.source_digest(), 'requested_date': dt.date(2026, 11, 1)}
    assert scheduled.publisher.resume.call_count == 2


def test_calendar_rollover_needs_no_override(scheduled, monkeypatch):
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz):
            assert tz == dt.UTC
            return cls(2026, 12, 1, 9, tzinfo=tz)
    monkeypatch.delenv('RUN_DATE')
    monkeypatch.setattr(automatic.dt, 'datetime', Clock)
    automatic.run()
    assert scheduled.build.call_args.args[1] == dt.date(2026, 12, 1)


def test_completed_month_checks_in_without_rebuilding(scheduled):
    scheduled.publisher.load_successful_run_record.return_value = ({'status': 'success'}, {})
    records = automatic.run()
    assert len(records) == 2
    assert all(r['status'] == 'skipped' and r['target_release_date'] == '2026-11-01' and r['run_date'] == '2026-11-02' for r in records)
    scheduled.build.assert_not_called()
    scheduled.promote.assert_not_called()


def test_unavailable_source_checks_in_without_publication(scheduled):
    scheduled.build.side_effect = wdpa.SourceNotAvailableError('source not ready')
    records = automatic.run()
    assert all(r['reason'] == 'source not ready' for r in records)
    scheduled.promote.assert_not_called()


@pytest.mark.parametrize('error', [p.PublicationError('invalid artifact'), RuntimeError('source download failed')])
def test_failed_validation_or_download_never_reaches_publication(scheduled, error):
    scheduled.build.side_effect = error
    with pytest.raises(type(error), match=str(error)):
        automatic.run()
    scheduled.promote.assert_not_called()
    scheduled.publisher.update_latest_run_index.assert_not_called()


@pytest.mark.parametrize('defect', ['owner', 'future', 'foreign-bundle', 'pinned-bundle', 'sample'])
def test_invalid_state_or_override_cannot_bypass_automatic_boundary(scheduled, monkeypatch, defect):
    if defect == 'owner':
        scheduled.publisher.state.return_value.value['active'] = 'another-execution'
    elif defect == 'future':
        scheduled.publisher.state.return_value.value['current']['release'] = '2026-12-01'
    elif defect == 'foreign-bundle':
        scheduled.reference['uri'] = scheduled.reference['uri'].replace('aaaaa', 'bbbbb')
    elif defect == 'pinned-bundle':
        monkeypatch.setenv('WDPA_PROMOTION_BUNDLE', '{}')
    else:
        monkeypatch.setattr(wdpa, 'parse_sample_spec', lambda: object())
    with pytest.raises(p.PublicationError):
        automatic.run()
    scheduled.promote.assert_not_called()


def test_replay_passes_month_to_real_cli_and_reads_serialized_report(monkeypatch, tmp_path):
    directory = tmp_path / 'output'
    expected = {'state': 'succeeded', 'run_date': '2026-11-01'}
    def command(argv, *, check):
        assert check and argv[1] == 'scripts/local_wdpa_sample.py'
        assert argv[argv.index('--run-date') + 1] == expected['run_date']
        assert '--stage-build' in argv and '--genesis' not in argv
        directory.mkdir()
        (directory / 'benchmark.json').write_text(json.dumps(expected))
    monkeypatch.setattr(producer.subprocess, 'run', command)
    assert producer.replay(tmp_path / 'source.zip', tmp_path / 'inputs', directory, expected['run_date'], sample=False) == expected


@pytest.mark.parametrize('defect', [None, 'compatibility', 'counts', 'memory', 'scratch'])
def test_builder_requires_full_validation_before_committing_descriptor(monkeypatch, tmp_path, defect):
    evidence = accepted_evidence()
    report = copy.deepcopy(evidence['build'])
    sample = copy.deepcopy(evidence['compatibility_sample'])
    for item in (report, sample):
        item['run_date'] = '2026-11-01'
        item['phases'] = []
    if defect == 'compatibility':
        sample['compatibility_verified'] = False
    if defect == 'counts':
        report['assets']['wdpa-marine']['rows'] += 1
    monkeypatch.setenv('CLOUD_RUN_EXECUTION', 'wdpa-monthly-aaaaa')
    monkeypatch.setenv('WDPA_BUILD_IMAGE', report['cloud_image'].replace('/wdpa-validation@', '/wdpa-monthly@'))
    monkeypatch.setenv('WDPA_BUILD_IMAGE_CONFIG_DIGEST', report['image_digest'])
    monkeypatch.setattr(producer, 'prepare_scratch', lambda: None)
    monkeypatch.setattr(producer, 'cgroup_limits', lambda: (4, 8 * 1024**3))
    monkeypatch.setattr(producer, 'cgroup_memory', lambda: ({}, (9 if defect == 'memory' else 5) * 1024**3))
    monkeypatch.setattr(producer, 'freeze_inputs', lambda *args: None)
    monkeypatch.setattr(wdpa, 'native_versions', lambda: {})
    monkeypatch.setattr(wdpa, 'download_file', lambda *args: None)
    monkeypatch.setattr(producer, 'replay', lambda *args, **kwargs: sample if kwargs['sample'] else report)
    from contextlib import nullcontext
    profile = SimpleNamespace(records=[{'scratch_peak_bytes': (81 if defect == 'scratch' else 10) * 1024**3}], phase=lambda _: nullcontext())
    monkeypatch.setattr(producer, 'PhaseProfiler', lambda *args, **kwargs: profile)
    committed = []
    class Stager:
        def __init__(self, *args):
            pass
        def commit(self, result, directory):
            p.require(not gate.check_build(result, require_bundle=False), 'complete build did not pass acceptance')
            committed.append(result)
            return {'validated': True}
    monkeypatch.setattr(producer, 'BuildStager', Stager)
    if defect:
        with pytest.raises(p.PublicationError):
            producer.build_month(Mock(), dt.date(2026, 11, 1), tmp_path / 'build')
        assert committed == []
    else:
        assert producer.build_month(Mock(), dt.date(2026, 11, 1), tmp_path / 'build') == {'validated': True}
        assert committed[0]['compatibility_sample']['compatibility_verified'] is True
        assert committed[0]['compatibility_sample']['cloud_execution'] == 'wdpa-monthly-aaaaa'


def test_completed_execution_resumes_without_rebuilding_or_new_writes(scheduled):
    records = [{'status': 'success'}, {'status': 'skipped'}]
    scheduled.publisher.resume.side_effect = records
    assert automatic.run() == records
    scheduled.build.assert_not_called()
    scheduled.promote.assert_not_called()
    scheduled.publisher.update_latest_run_index.assert_not_called()
