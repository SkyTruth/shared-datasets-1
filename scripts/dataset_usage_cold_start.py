#!/usr/bin/env python3
"""Exercise the usage entrypoint with disabled collection and no network/credentials.

The installed worker, SDK Bucket representation, SQLite collector and GcsStore
remain real. Only credential discovery, HTTP and Storage transport are replaced.
Host regression tests and the production-image smoke share this small fixture.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.auth.credentials import AnonymousCredentials
from google.cloud.storage import Bucket

# The worker's entrypoint reads repository-relative config from its WORKDIR.
# This fixture is also passed to the image's Python via -c, without packaging it.
ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

SHARED = 'skytruth-shared-datasets-1'
RAW = 'fixture-usage-raw'
STATE = 'fixture-usage-state'
CATALOG = b'''asset_slug,title,status,canonical_path,access_tier
public-sentinel,Public sentinel,active,gs://skytruth-shared-datasets-1/example/public/latest/example.csv,public
restricted-sentinel,Restricted sentinel,active,gs://skytruth-shared-datasets-1/example/restricted/latest/example.csv,restricted
'''


class Blob:
    def __init__(self, client, bucket, name, generation=None):
        self.client, self.bucket, self.name = client, bucket, name
        self.generation, self.size = generation, None

    def reload(self, *, if_generation_match=None, timeout):
        key = (self.bucket, self.name)
        if key not in self.client.objects:
            raise NotFound('fixture object absent')
        generation, data = self.client.objects[key]
        if if_generation_match is not None and generation != if_generation_match:
            raise PreconditionFailed('fixture generation changed')
        self.generation, self.size = generation, len(data)

    def download_as_bytes(self, *, timeout, if_generation_match=None, **kwargs):
        self.reload(if_generation_match=if_generation_match, timeout=timeout)
        return self.client.objects[self.bucket, self.name][1]

    def upload_from_filename(self, path, *, if_generation_match, timeout):
        self.upload_from_string(Path(path).read_bytes(), if_generation_match=if_generation_match, timeout=timeout)

    def upload_from_string(self, data, *, if_generation_match, timeout, **kwargs):
        assert self.bucket == STATE, 'Cold start must never write dataset or raw objects'
        key = (self.bucket, self.name)
        current = self.client.objects.get(key, (0, b''))[0]
        if current != if_generation_match:
            raise PreconditionFailed('fixture publication precondition failed')
        data = data.encode() if isinstance(data, str) else data
        self.generation, self.size = current + 1, len(data)
        self.client.objects[key] = (self.generation, data)
        self.client.writes.append((key, if_generation_match))


class Client:
    def __init__(self, *, logging=None):
        self.objects = {(SHARED, '_catalog/shared-datasets-catalog.csv'): (7, CATALOG)}
        self.writes = []
        self.source_bucket = Bucket(client=None, name=SHARED)
        if logging is not None:
            self.source_bucket._properties['logging'] = logging

    def bucket(self, name):
        return SimpleNamespace(name=name, blob=lambda path, generation=None: Blob(self, name, path, generation))

    def get_bucket(self, name, *, timeout):
        assert name == SHARED
        return self.source_bucket

    def list_blobs(self, bucket, *, page_size):
        assert bucket.name == RAW and page_size == 256
        return []


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def read(self, size):
        assert size == 1
        return b'x'

    def iter_content(self, *, chunk_size):
        assert chunk_size == 1
        return iter([b'x'])


def exercise(directory, *, client=None):
    """Run the actual cold-start CLI; assertions are the image acceptance contract."""
    client = Client() if client is None else client
    directory.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / 'catalog/dataset-usage.json').read_text())
    config['collection_enabled'] = False
    config['cdn_host'] = 'tiles.example.invalid'
    config_path, activation_path = directory / 'config.json', directory / 'activation.json'
    config_path.write_text(json.dumps(config))
    activation_path.write_text(json.dumps({'schema_version': 1, 'verified_at': None, 'evidence': None}))
    session = Mock()
    session.get.return_value = Response()
    http = Mock(return_value=Response())
    environment = {'SHARED_DATASETS_BUCKET': SHARED, 'DATASET_USAGE_RAW_BUCKET': RAW,
                   'DATASET_USAGE_STATE_BUCKET': STATE, 'GOOGLE_CLOUD_PROJECT': 'fixture-project'}
    argv = ['ingestion.dataset_usage.run', '--config', str(config_path), '--activation', str(activation_path),
            '--work-dir', str(directory / 'runs')]
    with (patch.dict(os.environ, environment), patch.object(sys, 'argv', argv),
          patch('google.cloud.storage.Client', return_value=client),
          patch('google.auth.default', return_value=(AnonymousCredentials(), 'fixture-project')),
          patch('google.auth.transport.requests.AuthorizedSession', return_value=session),
          patch('urllib.request.urlopen', http),
          patch('socket.socket.connect', side_effect=AssertionError('Unexpected cold-start network access'))):
        runpy.run_path(str(ROOT / 'ingestion/dataset_usage/run.py'), run_name='__main__')
    assert len(client.writes) == 3 and all(generation == 0 for _, generation in client.writes)
    manifest = json.loads(client.objects[STATE, 'published/manifest.json'][1])
    for kind in ('ledger', 'report'):
        ref = manifest[kind]
        generation, data = client.objects[STATE, ref['path']]
        assert str(generation) == ref['generation'] and len(data) == ref['size']
        assert hashlib.sha256(data).hexdigest() == ref['sha256']
    payload = json.loads(client.objects[STATE, manifest['report']['path']][1])
    assert payload['collection_errors'] == []
    assert len(payload['assets']) == 2
    assert all(asset['state'] == 'collecting_history' and asset['observed_from'] is None for asset in payload['assets'])
    assert {row['source'] for row in payload['coverage']} == {'gcs_audit', 'cdn', 'gcs_usage', 'catalog'}
    assert all(row['state'] == 'gap' for row in payload['coverage'])
    ledger = directory / 'published-ledger.sqlite'
    ledger.write_bytes(client.objects[STATE, manifest['ledger']['path']][1])
    with sqlite3.connect(ledger) as db:
        assert db.execute('SELECT COUNT(*),SUM(trusted),SUM(seen) FROM probes').fetchone() == (4, 0, 0)
    assert json.loads(config_path.read_text())['collection_enabled'] is False
    assert client.source_bucket.get_logging() is None
    assert http.call_count == 2 and session.get.call_count == 1
    public_requests = [call.args[0] for call in http.call_args_list]
    assert [(request.type, request.host, request.get_method()) for request in public_requests] == [
        ('https', 'tiles.example.invalid', 'GET'), ('https', 'storage.googleapis.com', 'HEAD'),
    ]
    assert session.get.call_args.args[0].startswith(f'https://storage.googleapis.com/storage/v1/b/{SHARED}/o/')
    assert session.get.call_args.kwargs['stream'] is True
    assert session.post.call_count == 0
    print(json.dumps({'event': 'dataset_usage_cold_start_verified', 'schema_version': 1,
                      'coverage': 'unverified', 'probes': 4, 'state_objects': 3}))
    return payload


def main():
    work = Path(os.environ.get('SHARED_DATASETS_WORKDIR', str(Path(tempfile.gettempdir()) / 'shared-datasets-1'))) / 'dataset-usage-cold-start'
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fixture-', dir=work) as directory:
        exercise(Path(directory))


if __name__ == '__main__':
    main()
