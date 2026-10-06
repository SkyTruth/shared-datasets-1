#!/usr/bin/env python3
"""Require declared and live authority for catalog-derived managed-folder removal."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import time
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BUCKET = 'skytruth-shared-datasets-1'
DELETE = 'storage.managedFolders.delete'


def catalog_folders(raw: str) -> set[str]:
    rows = csv.DictReader(io.StringIO(raw))
    if not rows.fieldnames or 'canonical_path' not in rows.fieldnames:
        raise ValueError('catalog snapshot has no canonical_path column')
    folders = {'_catalog/'}
    prefix = f'gs://{BUCKET}/'
    for row in rows:
        path = row['canonical_path']
        if path.startswith(prefix):
            folder = posixpath.dirname(posixpath.dirname(path.removeprefix(prefix)))
            if folder:
                folders.add(folder + '/')
    return folders


def check_diff(root: Path, base: str, head: str) -> None:
    if not all(re.fullmatch(r'[0-9a-f]{40}', revision) for revision in (base, head)):
        raise ValueError('complete comparison revisions are required')
    def read(revision, path):
        return subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=root, text=True)
    before = catalog_folders(read(base, 'catalog/shared-datasets-catalog.csv'))
    after = catalog_folders(read(head, 'catalog/shared-datasets-catalog.csv'))
    removed = before - after
    if not removed:
        return
    from scripts.release_contracts import role_permissions
    declared = role_permissions(read(head, 'terraform/envs/prod/shared_bucket_public.tf'), 'pmtiles_managed_folder_sync')
    if DELETE not in declared:
        raise ValueError(f'catalog removes managed folders {sorted(removed)} but reviewed CDN role does not declare {DELETE}; retain catalog rows or establish reviewed removal authority first')


def deletes_managed_folder(plan: dict) -> bool:
    changes = plan.get('resource_changes', [])
    if not isinstance(changes, list):
        raise ValueError('invalid saved CDN plan resource changes')
    return any('delete' in resource.get('change', {}).get('actions', [])
               and re.fullmatch(r'google_storage_managed_folder\.shared_bucket_public_prefixes(?:\["[^"\n]+"\])?', resource.get('address', ''))
               for resource in changes)


def verify_plan(plan: dict, probe, *, attempts=7, pause=time.sleep) -> None:
    if not deletes_managed_folder(plan):
        return
    for attempt in range(attempts):
        missing = probe((DELETE,))
        if not missing:
            return
        if attempt + 1 < attempts:
            pause(10)
    raise RuntimeError(f'CDN saved plan requires unavailable live permission: {DELETE}; no dependent apply is authorized')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    diff = commands.add_parser('check-diff')
    diff.add_argument('--base', required=True)
    diff.add_argument('--head', required=True)
    live = commands.add_parser('verify-plan')
    live.add_argument('--plan-json', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'check-diff':
        check_diff(Path.cwd(), args.base, args.head)
    else:
        plan = json.loads(args.plan_json.read_text())
        if deletes_managed_folder(plan):
            from scripts.deployment_permissions import request
            token = subprocess.check_output(['gcloud', 'auth', 'print-access-token'], text=True).strip()
            if not token:
                raise RuntimeError('authenticated deployer has no access token')
            url = f'https://storage.googleapis.com/storage/v1/b/{BUCKET}/iam/testPermissions?' + urlencode({'permissions': DELETE})
            verify_plan(plan, lambda permissions: request(url, permissions, token))
    print('Catalog-derived CDN removal prerequisites passed.')


if __name__ == '__main__':
    main()
