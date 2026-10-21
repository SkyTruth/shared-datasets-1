#!/usr/bin/env python3
"""Download a complete, generation-pinned release-index snapshot for catalog build."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

from google.cloud import storage
from google.cloud.storage.retry import DEFAULT_RETRY

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import gcp_read_retry
from scripts.operational_retry import SHORT, UNATTENDED, operation, request_timeout, run_read

PREFIX = '_catalog/releases/'
MAX_INDEX_BYTES = 20 * 1024 * 1024


def collect(client, bucket_name: str, destination: Path, *, retry=DEFAULT_RETRY) -> None:
    if bucket_name != 'skytruth-shared-datasets-1':
        raise ValueError('catalog refresh requires the shared production bucket')
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('release-index destination must be empty')
    # An empty successful enumeration is legitimate. IAM, transport and listing
    # failures propagate; they must never silently replace history with emptiness.
    indexes = list(client.list_blobs(bucket_name, prefix=PREFIX, retry=retry, timeout=request_timeout(60)))
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
        data = pinned.download_as_bytes(if_generation_match=generation, checksum='auto', retry=retry, timeout=request_timeout(60))
        if len(data) != blob.size:
            raise ValueError('release index downloaded size differs from stored metadata')
        (destination / relative).write_bytes(data)
    print(f'Collected {len(indexes)} release indexes with exact generations.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--unattended', action='store_true')
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    if any(args.destination.iterdir()):
        raise ValueError('release-index destination must be empty')

    def snapshot():
        # A failed listing or download must not leave bytes for another attempt
        # to mistake for a complete snapshot. Only this invocation's files are
        # disposable; an existing nonempty destination was rejected above.
        with TemporaryDirectory(prefix='.' + args.destination.name + '-', dir=args.destination.parent) as directory:
            temporary = Path(directory) / 'snapshot'
            collect(storage.Client(project='shared-datasets-1'), args.bucket, temporary, retry=None)
            args.destination.rmdir()
            temporary.rename(args.destination)

    with operation(profile=UNATTENDED if args.unattended else SHORT):
        run_read(snapshot, classify=gcp_read_retry.classify, label='release-index snapshot')


if __name__ == '__main__':
    main()
