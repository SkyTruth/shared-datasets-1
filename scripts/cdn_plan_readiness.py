#!/usr/bin/env python3
"""Constrain reviewed CDN bootstrap plans and catalog-derived folder removals."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BUCKET = 'skytruth-shared-datasets-1'
DELETE = 'storage.managedFolders.delete'
ROLE_ADDRESS = 'google_project_iam_custom_role.pmtiles_url_map_sync'
ROLE_NAME = 'projects/shared-datasets-1/roles/sharedDatasetsPmtilesUrlMapSync'
PROBE_PERMISSION = 'compute.urlMaps.list'
PROBE_PERMISSIONS = frozenset({PROBE_PERMISSION, 'compute.backendBuckets.list'})
ROLE_FIELDS = {
    'project': 'shared-datasets-1',
    'role_id': 'sharedDatasetsPmtilesUrlMapSync',
    'id': ROLE_NAME,
    'name': ROLE_NAME,
    'title': 'Shared Datasets PMTiles URL Map Sync',
    'description': 'Allows GitHub Actions Terraform to update the PMTiles CDN URL map for catalog routing.',
    'stage': 'GA',
    'deleted': False,
}
# Exact live role before adoption, retained in the historical failure fixture.
# Do not broaden this bootstrap into a general Compute authority sync.
ORIGINAL_PERMISSIONS = frozenset({
    'compute.backendBuckets.get', 'compute.backendBuckets.use', 'compute.backendServices.get',
    'compute.globalOperations.get', 'compute.projects.get', 'compute.urlMaps.get',
    'compute.urlMaps.invalidateCache', 'compute.urlMaps.update', 'compute.urlMaps.validate',
})
ROLE_PERMISSIONS = ORIGINAL_PERMISSIONS | PROBE_PERMISSIONS
BOOTSTRAP_EXACT = {
    'google_project_iam_custom_role.pmtiles_managed_folder_sync',
    'google_storage_bucket_iam_member.github_actions_pmtiles_managed_folder_sync',
}
BOOTSTRAP_UPDATE_ONLY = {'google_storage_bucket.shared_bucket', ROLE_ADDRESS}


def require(condition, message):
    if not condition:
        raise ValueError('CDN_BOOTSTRAP_CONTRACT: ' + message)


def role_permissions(value, *, phase):
    require(isinstance(value, dict), f'{phase} role values are unavailable')
    for field, expected in ROLE_FIELDS.items():
        require(type(value.get(field)) is type(expected) and value[field] == expected, f'{phase} role {field} must preserve {expected!r}')
    permissions = value.get('permissions')
    require(isinstance(permissions, list) and all(isinstance(item, str) for item in permissions), f'{phase} role permissions are unavailable')
    require(len(permissions) == len(set(permissions)), f'{phase} role permissions are duplicated')
    return frozenset(permissions)


def check_bootstrap(plan):
    """Allow only adoption of the exact role and its two probe-call read grants."""
    require(isinstance(plan, dict) and plan.get('format_version') in {'1.1', '1.2'}, 'saved plan format is unavailable')
    require(not plan.get('errored') and not plan.get('deferred_changes'), 'errored or deferred bootstrap plan')
    rows = plan.get('resource_changes')
    require(isinstance(rows, list), 'resource changes are unavailable')
    roles = [row for row in rows if row.get('address') == ROLE_ADDRESS]
    require(len(roles) == 1, 'exact URL-map role must appear once in the targeted bootstrap plan')
    role = roles[0]
    require(role.get('mode') == 'managed' and role.get('type') == 'google_project_iam_custom_role' and not role.get('deposed'), 'URL-map role must be the current managed custom role')
    change = role.get('change', {})
    unknown = change.get('after_unknown', {})
    require(isinstance(unknown, dict) and not any(unknown.get(field) for field in (*ROLE_FIELDS, 'permissions')), 'role identity, metadata and permissions must be known before apply')
    importing = change.get('importing')
    require(importing is None or importing == {'id': ROLE_NAME}, 'import must use the exact existing role ID')
    before = role_permissions(change.get('before'), phase='before')
    after = role_permissions(change.get('after'), phase='after')
    require(after == ROLE_PERMISSIONS, 'after permissions must preserve the original nine and add only the two selected probe-call read permissions')
    actions = change.get('actions')
    if actions == ['update']:
        require(before == ORIGINAL_PERMISSIONS and after - before == PROBE_PERMISSIONS, 'update must add only the two selected probe-call read permissions to the original role')
    else:
        require(actions == ['no-op'] and before == ROLE_PERMISSIONS, 'URL-map role allows only the reviewed update or an already-adopted no-op')
    for resource in rows:
        address = resource.get('address', '')
        change = resource.get('change', {})
        actions = change.get('actions')
        if address == ROLE_ADDRESS:
            continue
        require(not change.get('importing') or address == 'google_storage_bucket.shared_bucket', f'unrelated import {address}')
        if actions in ([], ['no-op'], ['read']):
            continue
        if address in BOOTSTRAP_UPDATE_ONLY:
            allowed = actions == ['update']
        else:
            allowed = address in BOOTSTRAP_EXACT and actions in (['create'], ['update'])
        require(allowed, f"refusing {'/'.join(actions or [])} {address}")


def bootstrap_definition_errors(text):
    """Offline gate for the checked-in exact role and import definition."""
    errors = []
    name = ROLE_ADDRESS.split('.')[1]
    match = re.search(r'resource "google_project_iam_custom_role" "' + name + r'"\s*\{(.*?)\n\}', text, re.S)
    if not match:
        return ['CDN bootstrap must declare the existing URL-map role']
    block = match[1]
    if not re.search(r'project\s*=\s*var\.project_id\b', block):
        errors.append('URL-map role must use the production project variable')
    for field in ('role_id', 'title', 'description', 'stage'):
        if not re.search(r'^\s*' + field + r'\s*=\s*' + re.escape(json.dumps(ROLE_FIELDS[field])) + r'\s*$', block, re.M):
            errors.append(f'URL-map role must preserve its existing {field}')
    permissions = re.search(r'permissions\s*=\s*\[(.*?)\]', block, re.S)
    actual = re.findall(r'"([a-zA-Z0-9.]+)"', permissions[1]) if permissions else []
    literal = re.fullmatch(r'\s*(?:"[a-zA-Z0-9.]+"\s*,\s*)*', permissions[1]) if permissions else None
    if not literal or len(actual) != len(set(actual)) or set(actual) != ROLE_PERMISSIONS:
        errors.append('URL-map role must preserve all nine existing permissions and add only compute.urlMaps.list and compute.backendBuckets.list')
    if not re.search(r'lifecycle\s*\{\s*prevent_destroy\s*=\s*true\s*\}', block):
        errors.append('URL-map role must prevent destruction')
    expected_id = 'projects/${var.project_id}/roles/' + ROLE_FIELDS['role_id']
    imports = re.findall(r'import\s*\{(.*?)\n\}', text, re.S)
    matches = [value for value in imports if re.search(r'to\s*=\s*' + re.escape(ROLE_ADDRESS) + r'\b', value)]
    if len(matches) != 1 or not re.search(r'^\s*id\s*=\s*' + re.escape(json.dumps(expected_id)) + r'\s*$', matches[0], re.M):
        errors.append('URL-map role must import the exact existing production role')
    return errors


def routing_definition_errors(text, variables, workflow_text):
    """Bind the two-read role to the current production CDN routing consumer.

    Redirect/backend-service mode needs its own reviewed operation and probe
    authority; it cannot inherit this bucket-only readiness declaration.
    """
    errors = []
    modes = re.findall(r'^\s*pmtiles_serving_mode\s*=\s*"([^"]+)"\s*$', variables, re.M)
    if modes != ['cdn'] or 'pmtiles_serving_mode' in workflow_text:
        errors.append('CDN role readiness requires production.auto.tfvars mode cdn without workflow overrides')
    if not re.search(r'^\s*pmtiles_redirector_enabled\s*=\s*var\.pmtiles_serving_mode\s*==\s*"redirect"\s*$', text, re.M):
        errors.append('CDN backend-service routing must remain disabled in production cdn mode')
    bucket = re.search(r'resource "google_compute_backend_bucket" "pmtiles_cdn"\s*\{(.*?)\n\}', text, re.S)
    route = re.search(r'resource "google_compute_url_map" "pmtiles_cdn"\s*\{(.*?)\n\}', text, re.S)
    for name, block in (('backend bucket', bucket), ('URL map', route)):
        if not block or not re.search(r'^\s*name\s*=\s*"shared-datasets-pmtiles-cdn"\s*$', block[1], re.M):
            errors.append(f'CDN {name} must retain its exact configured production identity')
    if route:
        # Remove only the existing conditional redirect path. A service added
        # elsewhere, or a changed condition/path/reference, remains visible to
        # the active-routing contract and fails before merge.
        inactive = r'\n\s+dynamic "path_rule" \{\s*for_each\s*=\s*local\.pmtiles_redirector_enabled\s*\?\s*toset\(\["redirect"\]\)\s*:\s*toset\(\[\]\)\s*content\s*\{\s*paths\s*=\s*\["/pmtiles/\*"\]\s*service\s*=\s*google_compute_backend_service\.pmtiles_redirector\[0\]\.self_link\s*\}\s*\}'
        active = re.sub(inactive, '', route[1])
        references = re.findall(r'^\s*(?:service|default_service|backend_service)\s*=\s*(.*?)\s*$', active, re.M)
        if not references or any(value != 'google_compute_backend_bucket.pmtiles_cdn.self_link' for value in references):
            errors.append('CDN active routing must use the reviewed global backend bucket; new services require separate operation/probe authority')
    return errors


def routing_contract_errors(root):
    """Reject unmodeled Terraform variable inputs rather than guessing mode."""
    prod = root / 'terraform/envs/prod'
    workflow_text = (root / '.github/workflows/pmtiles-cdn-sync.yml').read_text()
    errors = routing_definition_errors(
        (prod / 'pmtiles_cdn.tf').read_text(),
        (prod / 'production.auto.tfvars').read_text(),
        workflow_text,
    )
    auto_files = set(prod.glob('*.auto.tfvars')) | set(prod.glob('*.auto.tfvars.json'))
    unmodeled = auto_files - {prod / 'production.auto.tfvars'}
    unmodeled |= {path for name in ('terraform.tfvars', 'terraform.tfvars.json') if (path := prod / name).exists()}
    if unmodeled:
        errors.append('CDN effective-mode contract refuses unmodeled automatic variable files: ' + ', '.join(sorted(path.name for path in unmodeled)))
    known_image_vars = r'-var="(?:wdpa_monthly_image|sea_ice_daily_image|eamlis_monthly_image)=unused-by-pmtiles-cdn-sync"'
    remaining = re.sub(known_image_vars, '', workflow_text)
    if re.search(r'(?<![\w-])-var\b|TF_VAR_|TF_CLI_ARGS', remaining):
        errors.append('CDN effective-mode contract refuses unmodeled CLI or environment variable overrides')
    return errors


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    diff = commands.add_parser('check-diff')
    diff.add_argument('--base', required=True)
    diff.add_argument('--head', required=True)
    bootstrap = commands.add_parser('check-bootstrap')
    bootstrap.add_argument('--plan-json', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'check-bootstrap':
        check_bootstrap(json.loads(args.plan_json.read_text()))
        print('Exact existing CDN role adoption and bootstrap allowlist passed.')
    else:
        check_diff(Path.cwd(), args.base, args.head)
        print('Catalog-derived CDN removal prerequisites passed.')


if __name__ == '__main__':
    main()
