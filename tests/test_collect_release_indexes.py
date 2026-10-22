"""Catalog collection preserves stored generations and exposes actual outages."""
from unittest.mock import Mock

import pytest

from scripts.collect_release_indexes import collect

BUCKET = 'skytruth-shared-datasets-1'


def test_exact_stored_generation_is_used_even_if_download_metadata_changes(tmp_path):
    client = Mock()
    blob = Mock()
    blob.name, blob.generation, blob.size = '_catalog/releases/wdpa-marine.json', '123', 2
    client.list_blobs.return_value = [blob]
    client.bucket.return_value.blob.return_value.download_as_bytes.return_value = b'{}'
    collect(client, BUCKET, tmp_path)
    client.bucket.return_value.blob.assert_called_once_with(blob.name, generation=123)
    client.bucket.return_value.blob.return_value.download_as_bytes.assert_called_once_with(if_generation_match=123, checksum='auto')
    assert (tmp_path / 'wdpa-marine.json').read_bytes() == b'{}'


def test_actual_empty_listing_is_valid_but_permission_or_network_error_is_not(tmp_path):
    client = Mock()
    client.list_blobs.return_value = []
    collect(client, BUCKET, tmp_path)
    assert list(tmp_path.iterdir()) == []
    client.list_blobs.side_effect = RuntimeError('permission or network outage')
    with pytest.raises(RuntimeError, match='outage'):
        collect(client, BUCKET, tmp_path)


@pytest.mark.parametrize('name,generation,size', [('_catalog/releases/../attack.json', 1, 2), ('_catalog/releases/wdpa.json', None, 2), ('_catalog/releases/wdpa.json', 0, 2), ('_catalog/releases/wdpa.json', 1, 0), ('_catalog/releases/wdpa.json', 1, 30 * 1024 * 1024)])
def test_malformed_or_unbounded_listing_fails_before_download(tmp_path, name, generation, size):
    client = Mock()
    blob = Mock()
    blob.name, blob.generation, blob.size = name, generation, size
    client.list_blobs.return_value = [blob]
    with pytest.raises(ValueError):
        collect(client, BUCKET, tmp_path)
    client.bucket.assert_not_called()
