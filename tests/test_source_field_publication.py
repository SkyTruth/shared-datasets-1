"""Real source-field recipe/executor recovery across durable write boundaries."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ingestion.common import publication as p, source_field_publication as source, source_quality
from ingestion.mpatlas_monthly import run as job
from tests.test_mpatlas_monthly import feature, sidecar, fixture_outputs, TOOLS, DAY
from tests.test_shared_hold_flow import seed
from tests.test_source_loss import Client
from tests.test_publication import MemoryStore, LostResponse


@pytest.fixture
def published(tmp_path, monkeypatch):
    publisher, _ = seed(Client(), tmp_path / 'baseline')
    store = MemoryStore()
    for blob in publisher.bucket.blobs.values():
        if not blob.exists:
            continue
        uri = f'gs://{publisher.bucket.name}/{blob.name}'
        data = blob.data
        version = p.ObjectVersion(uri, blob.generation, p.digest(data), len(data),
            blob.content_type or '', blob.cache_control or '', tuple(sorted((blob.metadata or {}).items())))
        store.objects[uri] = version
        store.history[(uri, version.generation)] = version, data
        store.serial = max(store.serial, version.generation)
    monkeypatch.setattr('ingestion.common.publication_bundle.vector_asset.validate_metadata_lookup_bundle',
                        lambda *args: SimpleNamespace(valid=True, errors=[]))
    return publisher, store, source_quality.MPATLAS.baseline(publisher.bucket)


def attempt(publisher, store, baseline, directory):
    directory.mkdir()
    features = [feature(n) for n in range(1, 41)]
    features[0]['properties']['zone_name'] = 'Updated name'
    records = sidecar(features, DAY.isoformat())
    outputs = fixture_outputs(directory, records)
    publication = source.SourceFieldPublication(publisher, job.ASSET, execution_id='mpatlas-fixture',
        executor_sha='c' * 40, configuration={}, store=store)
    record = {'schema_version': 1, 'asset_slug': job.ASSET.slug, 'status': 'success',
        'run_date': DAY.isoformat(), 'release_date': DAY.isoformat(), 'source': job.SOURCE_URL,
        'source_version': 'new', 'row_count': len(records), 'tools': TOOLS,
        'release_path': f'gs://{publisher.bucket.name}/{job.ASSET.release_prefix(DAY)}/',
        **job.source_fingerprint(records).record_fields()}
    args = dict(run_date=DAY, outputs=outputs, identity=job.model.build_identity_metadata(strategy='source_field', source_fields=['zone_id']),
        object_metadata={'asset_slug': job.ASSET.slug}, source_inputs=[{'uri': job.SOURCE_URL, 'sha256': 'd' * 64}],
        record=record, baseline=baseline)
    return publication, args


def test_source_field_resume_after_every_write_needs_no_upstream_or_local_bytes(published, tmp_path):
    publisher, seeded, baseline = published
    store = deepcopy(seeded)
    publication, args = attempt(publisher, store, baseline, tmp_path / 'complete')
    completed = publication.publish(**args)
    assert completed['status'] == 'success' and completed['row_count'] == 40
    writes = len(store.events)
    for fail_after in range(1, writes + 1):
        interrupted = deepcopy(seeded)
        publication, args = attempt(publisher, interrupted, baseline, tmp_path / f'crash-{fail_after}')
        interrupted.fail_after = fail_after
        with pytest.raises(LostResponse):
            publication.publish(**args)
        interrupted.fail_after = None
        captured = interrupted.read_json(publication.context.receipt_uri)
        if captured is None:
            # No data was published before complete durable input staging.
            assert all('/publications/' in uri for uri in interrupted.events)
            result = publication.publish(**args)
        else:
            transaction = captured.value['transaction_id']
            for value in vars(args['outputs']).values():
                if hasattr(value, 'is_file') and value.is_file():
                    value.unlink()
            result = publication.resume()
            assert interrupted.read_json(publication.context.receipt_uri).value['transaction_id'] == transaction
        assert result['sha256'] == completed['sha256']
        assert interrupted.read_json(publication.context.state_uri).value['active'] is None
        operations = interrupted.read_json(publication.context.receipt_uri).value['intent']['operations']
        assert all(interrupted.events.count(op['destination']) == 1 for op in operations)


def test_partial_publication_rejects_another_execution_and_changed_destinations(published, tmp_path):
    publisher, store, baseline = published
    publication, args = attempt(publisher, store, baseline, tmp_path / 'attempt')
    # Fail immediately after the receipt exists; all input generations are durable.
    def fail(uri):
        if uri == publication.context.receipt_uri:
            store.fail_after = len(store.events) + 1
        else:
            store.before_write = fail
    store.before_write = fail
    with pytest.raises(LostResponse):
        publication.publish(**args)
    store.fail_after = None
    other = source.SourceFieldPublication(publisher, job.ASSET, execution_id='other-execution',
        executor_sha='c' * 40, configuration={}, store=store)
    # A receipt prepared before claim still has fixed generations. Conflicting
    # writes cannot be accepted as a replacement source on the original retry.
    receipt = store.read_json(publication.context.receipt_uri).value
    target = next(op for op in receipt['intent']['operations'] if op['id'] == 'latest-fgb')
    store.write_bytes(target['destination'], b'foreign bytes', target['expected_generation'], {}, 'application/octet-stream', '')
    with pytest.raises(p.Conflict, match='original expectation'):
        publication.resume()
    with pytest.raises(p.PublicationError, match='original execution'):
        other.resume()


def test_same_execution_may_reenter_its_source_quality_claim_without_stealing_another(monkeypatch):
    flow = source_quality.MPATLAS
    bucket = Client().bucket(flow.BUCKET)
    guard = flow.Guard(bucket, owner='same-execution')
    guard.__enter__()
    retry = flow.Guard(bucket, owner='same-execution')
    retry.__enter__()
    assert not retry.held
    monkeypatch.setattr(flow, 'execution_completed', lambda owner: False)
    with pytest.raises(source_quality.SourceLossError, match='active or interrupted'):
        flow.Guard(bucket, owner='different-execution').__enter__()
    retry.__exit__(None, None, None)


def test_cloud_run_task_retry_keeps_the_original_transaction_context(published, monkeypatch):
    publisher, _, _ = published
    monkeypatch.setenv('CLOUD_RUN_EXECUTION', 'mpatlas-monthly-retry')
    monkeypatch.setenv('SHARED_DATASETS_EXECUTOR_SHA', 'c' * 40)
    monkeypatch.setenv('RUN_DATE', DAY.isoformat())
    monkeypatch.setenv('CLOUD_RUN_TASK_ATTEMPT', '0')
    first = source.SourceFieldPublication.from_runtime(publisher, job.ASSET)
    monkeypatch.setenv('CLOUD_RUN_TASK_ATTEMPT', '1')
    retry = source.SourceFieldPublication.from_runtime(publisher, job.ASSET)
    assert retry.context == first.context
    monkeypatch.setenv('CLOUD_RUN_EXECUTION', 'mpatlas-monthly-other')
    assert source.SourceFieldPublication.from_runtime(publisher, job.ASSET).context != first.context
