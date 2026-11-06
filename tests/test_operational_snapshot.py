"""Catalog generation snapshots and authenticated health-report artifacts."""
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from scripts import operational_safety_check as safety, catalog_drift_guard as catalog
from test_operational_safety_check import NOW, START, OperationalAPI, acked_job_state, signed_transport, stamp
from test_operational_safety_check import probe_inputs  # noqa: F401 - pytest fixture
from test_operational_safety_check import receipt_workspace  # noqa: F401 - autouse fixture
from test_slack_incidents import IDENTITY


def test_catalog_change_during_comparison_requests_refresh(probe_inputs):  # noqa: F811
    api, client, directory, _collect, checks = probe_inputs
    def update(*_args, **_kwargs):
        client.objects[catalog.REMOTE_WEB_CATALOG_OBJECT] = "4"
        return [SimpleNamespace(ok=True)]
    checks.side_effect = update
    result = safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)
    assert result["catalog"] is True



@pytest.mark.parametrize('defect', [None, 'digest', 'attempt', 'upload', 'metadata-size', 'archive-size',
    'member-size', 'member', 'extra-member', 'noncanonical', 'producer', 'sha', 'branch', 'created-before', 'created-after'])
def test_health_history_authenticates_exact_attempt_and_actual_archive_bytes(defect, monkeypatch):
    import hashlib
    import io
    import zipfile
    from scripts.incident_verification_cache import canonical, ArtifactError
    api = OperationalAPI()
    producer = api.source(101, 'operational-safety-check.yml')
    api.add_source(producer)
    job = api.jobs[101][0]
    job.update(name=safety.JOB, started_at=producer['created_at'], completed_at=producer['updated_at'],
               steps=[{'name': 'Retain safety health report', 'status': 'completed', 'conclusion': 'failure' if defect == 'upload' else 'success'}])
    report = {'problems': ['paused'], 'warnings': [], 'notified_at': NOW.isoformat()}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('cache.json', canonical(report))
    raw = archive.getvalue()
    artifact = {'id': 9, 'name': 'safety-health-101-1', 'expired': False, 'size_in_bytes': len(raw),
                'created_at': producer['updated_at'], 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                'workflow_run': {'id': 101, 'head_sha': api.head, 'head_branch': 'main'}}
    if defect == 'digest':
        artifact['digest'] = 'sha256:' + '0' * 64
    if defect == 'attempt':
        api.runs[101] = {**producer, 'run_attempt': 2}
    if defect == 'metadata-size':
        artifact['size_in_bytes'] = safety.native.MAX_BYTES + 1
    if defect == 'archive-size':
        monkeypatch.setattr(safety.native, 'MAX_BYTES', len(raw) - 1)
        artifact['size_in_bytes'] = 1
    if defect in {'member-size', 'member', 'extra-member', 'noncanonical'}:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
            stream.writestr('../cache.json' if defect == 'member' else 'cache.json',
                b'x' * 4096 if defect == 'member-size' else json.dumps(report).encode() if defect == 'noncanonical' else canonical(report))
            if defect == 'extra-member':
                stream.writestr('extra', b'{}')
        raw = archive.getvalue()
        artifact.update(size_in_bytes=len(raw), digest='sha256:' + hashlib.sha256(raw).hexdigest())
        if defect == 'member-size':
            monkeypatch.setattr(safety.native, 'MAX_BYTES', 2048)
    if defect in {'producer', 'sha', 'branch'}:
        key, value = {'producer': ('id', 999), 'sha': ('head_sha', 'b' * 40), 'branch': ('head_branch', 'feature')}[defect]
        artifact['workflow_run'][key] = value
    if defect in {'created-before', 'created-after'}:
        artifact['created_at'] = stamp(NOW - timedelta(days=1) if defect == 'created-before' else NOW)
    pages = api.pages
    api.pages = lambda path, field=None: [artifact] if '/artifacts?' in path else pages(path, field=field)
    api.archive = lambda *_: raw
    if defect:
        with pytest.raises((RuntimeError, ArtifactError)):
            safety.previous_health(api, api.current)
    else:
        assert safety.previous_health(api, api.current) == report



def test_each_probe_verifies_signed_ledger_and_compares_catalog(probe_inputs):  # noqa: F811
    api, client, directory, collect, checks = probe_inputs
    api.install_state(acked_job_state(api))
    with signed_transport(api) as crypto:
        for _ in range(2):
            assert safety.probe(api, api.current, IDENTITY, stamp(START), client, directory) == {
                'catalog': False, 'reconcile': False, 'verify': False}
        assert crypto.call_count == 2
    assert collect.call_count == checks.call_count == 2
    assert not (directory / 'cache.json').exists()
    assert api.posts == []
