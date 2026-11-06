"""Managed-folder removal must declare narrow authority before merge."""
from pathlib import Path
import io
import tarfile

import pytest

from scripts.cdn_plan_readiness import check_diff


@pytest.mark.parametrize('declared', [False, True])
def test_catalog_folder_removal_requires_declared_delete_permission(tmp_path, monkeypatch, declared):
    before = 'canonical_path\ngs://skytruth-shared-datasets-1/example/asset/latest/a.fgb\n'
    after = 'canonical_path\n'
    role = 'resource "google_project_iam_custom_role" "pmtiles_managed_folder_sync" {\n permissions = [' + ('"storage.managedFolders.delete"' if declared else '"storage.managedFolders.create"') + ']\n}'
    def show(args, **kwargs):
        if args[1] == 'archive':
            assert args[2] == 'b' * 40
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w') as archive:
                member = tarfile.TarInfo('terraform/envs/prod/shared_bucket_public.tf')
                member.size = len(role.encode())
                archive.addfile(member, io.BytesIO(role.encode()))
            return output.getvalue()
        ref = args[-1]
        return before if ref.startswith('a' * 40) else after
    monkeypatch.setattr('scripts.cdn_plan_readiness.subprocess.check_output', show)
    if declared:
        check_diff(tmp_path, 'a' * 40, 'b' * 40)
    else:
        with pytest.raises(ValueError, match='does not declare storage.managedFolders.delete'):
            check_diff(tmp_path, 'a' * 40, 'b' * 40)


def test_retained_folder_needs_no_delete_authority(monkeypatch):
    catalog = 'canonical_path\ngs://skytruth-shared-datasets-1/example/asset/latest/a.fgb\n'
    def show(args, **kwargs):
        assert args[-1].endswith('catalog/shared-datasets-catalog.csv')
        return catalog
    monkeypatch.setattr('scripts.cdn_plan_readiness.subprocess.check_output', show)
    check_diff(Path.cwd(), 'a' * 40, 'b' * 40)
