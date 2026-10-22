#!/usr/bin/env python3
"""Download a complete, generation-pinned release-index snapshot for catalog build."""
from __future__ import annotations

import argparse
from pathlib import Path
import re

from google.cloud import storage

PREFIX = '_catalog/releases/'
MAX_INDEX_BYTES = 20 * 1024 * 1024


def collect(client, bucket_name: str, destination: Path) -> None:
    if bucket_name != 'skytruth-shared-datasets-1':
        raise ValueError('catalog refresh requires the shared production bucket')
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('release-index destination must be empty')
    # An empty successful enumeration is legitimate. IAM, transport and listing
    # failures propagate; they must never silently replace history with emptiness.
    indexes = list(client.list_blobs(bucket_name, prefix=PREFIX))
    seen = set()
    for blob in indexes:
        relative = blob.name.removeprefix(PREFIX)
        if not blob.name.startswith(PREFIX) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.json', relative):
            raise ValueError('unexpected release-index object path')
        if relative in seen:
            raise ValueError('duplicate release-index object')
        seen.add(relative)
        if not re.fullmatch(r'[1-9][0-9]*', str(blob.generation)) or not isinstance(blob.size, int) or not 0 < blob.size <= MAX_INDEX_BYTES:
            raise ValueError('release index lacks bounded immutable metadata')
        generation = int(blob.generation)
        pinned = client.bucket(bucket_name).blob(blob.name, generation=generation)
        data = pinned.download_as_bytes(if_generation_match=generation, checksum='auto')
        if len(data) != blob.size:
            raise ValueError('release index downloaded size differs from stored metadata')
        (destination / relative).write_bytes(data)
    print(f'Collected {len(indexes)} release indexes with exact generations.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    collect(storage.Client(project='shared-datasets-1'), args.bucket, args.destination)


if __name__ == '__main__':
    main()
