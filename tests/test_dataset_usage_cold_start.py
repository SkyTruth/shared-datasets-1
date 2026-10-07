"""The installed entrypoint must publish truthful gaps without collection enabled."""
import json
from pathlib import Path
from unittest.mock import Mock

from google.api_core.exceptions import Forbidden, NotFound
import pytest

from scripts.dataset_usage_cold_start import Client, STATE, exercise
from scripts.production_image_contracts import commands


def test_real_entrypoint_cold_start_without_bucket_logging(tmp_path, capsys):
    payload = exercise(tmp_path)
    assert payload['collection_errors'] == []
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event['event'] for event in events] == [
        'dataset_usage_probe', 'dataset_usage_published', 'dataset_usage_cold_start_verified',
    ]


def test_cold_start_malformed_logging_cannot_publish_coverage(tmp_path):
    client = Client(logging=[])
    with pytest.raises(TypeError, match='logging'):
        exercise(tmp_path, client=client)
    assert client.writes == []


def test_cold_start_denied_storage_read_is_not_healthy_or_disabled(tmp_path):
    client = Client()
    denied = Forbidden('fixture bucket read denied')
    client.get_bucket = Mock(side_effect=denied)
    with pytest.raises(Forbidden) as raised:
        exercise(tmp_path, client=client)
    assert raised.value is denied
    assert client.writes == []


def test_cold_start_incompatible_persisted_manifest_still_fails(tmp_path):
    client = Client()
    client.objects[STATE, 'published/manifest.json'] = (9, b'{"schema_version":999}')
    with pytest.raises(ValueError, match='Unsupported usage manifest'):
        exercise(tmp_path, client=client)
    assert client.writes == []


def test_cold_start_missing_published_catalog_still_fails(tmp_path):
    client = Client()
    client.objects.clear()
    with pytest.raises(NotFound, match='fixture object absent'):
        exercise(tmp_path, client=client)
    assert client.writes == []


def test_cold_start_failed_probe_cannot_publish_success(tmp_path, monkeypatch):
    client = Client()
    denied = Forbidden('fixture probe denied')
    monkeypatch.setattr('scripts.dataset_usage_cold_start.Response.raise_for_status', Mock(side_effect=denied))
    with pytest.raises(Forbidden) as raised:
        exercise(tmp_path, client=client)
    assert raised.value is denied
    assert client.writes == []


def test_usage_image_executes_maintained_cold_start_and_default_entrypoint():
    image = 'sha256:' + 'a' * 64
    selected = commands('dataset-usage', 'b' * 40, image_id=image)
    assert selected[0][selected[0].index('-f') + 1] == 'ingestion/dataset_usage/Dockerfile'
    assert selected[1][-2:] == [image, '--print-policy-hash']
    assert '--entrypoint' not in selected[1]
    assert selected[2][-3:] == [image, '-c', Path('scripts/dataset_usage_cold_start.py').read_text()]
    for command in selected[1:]:
        assert command[command.index('--network') + 1] == 'none'
        assert command[command.index('--platform') + 1] == 'linux/amd64'
        assert not any(value in command for value in ('-v', '--volume', '--mount', '-e', '--env'))
    assert json.loads(Path('ingestion/dataset_usage/Dockerfile').read_text().split('ENTRYPOINT ', 1)[1]) == [
        'python', '-m', 'ingestion.dataset_usage.run',
    ]
