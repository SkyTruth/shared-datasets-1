"""Managed-folder removals require reviewed declarations and actual live authority."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.cdn_plan_readiness import DELETE, check_diff, verify_plan


@pytest.mark.parametrize('declared', [False, True])
def test_catalog_folder_removal_requires_declared_delete_permission(monkeypatch, declared):
    before = 'canonical_path\ngs://skytruth-shared-datasets-1/example/asset/latest/a.fgb\n'
    after = 'canonical_path\n'
    role = 'resource "google_project_iam_custom_role" "pmtiles_managed_folder_sync" {\n permissions = [' + ('"storage.managedFolders.delete"' if declared else '"storage.managedFolders.create"') + ']\n}'
    def show(args, **kwargs):
        ref = args[-1]
        if ref.endswith('shared_bucket_public.tf'):
            return role
        return before if ref.startswith('a' * 40) else after
    monkeypatch.setattr('scripts.cdn_plan_readiness.subprocess.check_output', show)
    if declared:
        check_diff(Path.cwd(), 'a' * 40, 'b' * 40)
    else:
        with pytest.raises(ValueError, match='does not declare storage.managedFolders.delete'):
            check_diff(Path.cwd(), 'a' * 40, 'b' * 40)


def plan(address='google_storage_managed_folder.shared_bucket_public_prefixes["example/asset/"]', actions=None):
    return {'resource_changes': [{'address': address, 'change': {'actions': actions or ['delete']}}]}


def test_only_actual_folder_delete_needs_extra_permission():
    probe = Mock(return_value=[])
    verify_plan(plan(actions=['update']), probe)
    verify_plan(plan(address='google_storage_managed_folder_iam_member.shared_bucket_public_object_viewers["example/asset/"]'), probe)
    probe.assert_not_called()
    verify_plan(plan(), probe)
    probe.assert_called_once_with((DELETE,))


def test_missing_live_delete_authority_blocks_apply_and_outages_remain_visible():
    probe, pause = Mock(return_value=[DELETE]), Mock()
    with pytest.raises(RuntimeError, match='no dependent apply'):
        verify_plan(plan(), probe, attempts=2, pause=pause)
    assert probe.call_count == 2
    pause.assert_called_once_with(10)
    with pytest.raises(OSError, match='outage'):
        verify_plan(plan(), Mock(side_effect=OSError('outage')), pause=pause)
