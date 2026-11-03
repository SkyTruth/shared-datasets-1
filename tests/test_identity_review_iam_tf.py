"""Evaluate the actual narrow CEL clauses retained in the Terraform source."""

from pathlib import Path
import re

import pytest

from test_scheduled_ingestion_iam_tf import terraform_resource_block

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "projects/_/buckets/skytruth-shared-datasets-1/objects/"


@pytest.mark.parametrize(
    "path,allowed",
    [
        (
            "_scratch/wdpa-builds/wdpa-monthly-abcde/wdpa-marine/identity-review.json",
            True,
        ),
        (
            "_scratch/wdpa-builds/wdpa-monthly-fghij/wdpa-terrestrial/identity-review.json",
            True,
        ),
        ("_scratch/wdpa-builds/wdpa-monthly-abcde/build-bundle.json", False),
        ("_scratch/wdpa-builds/wdpa-monthly-abcde/wdpa-marine/wdpa-marine.fgb", False),
        ("_scratch/pending-publishes/identity-review.json", False),
        ("_scratch/wdpa-builds/wdpa-monthly-build-abcde/wdpa-marine/data.json", False),
        ("_scratch/other/identity-review.json", False),
        ("_catalog/web/identity-review.json", False),
    ],
)
def test_production_evidence_read_grant_excludes_other_scratch_bytes(path, allowed):
    text = (ROOT / "terraform/envs/prod/catalog_viewer.tf").read_text()
    clauses = re.search(
        r'catalog_viewer_identity_review_condition\s*=\s*join\(" && ", \[(.*?)\]\)',
        text,
        re.S,
    ).group(1)
    literals = re.findall(r'^\s*"([^"\n]+)",\s*$', clauses, re.M)
    assert len(literals) == 2
    name = PREFIX + path
    values = []
    for expression in literals:
        expression = expression.replace(
            "${local.shared_bucket_object_resource_prefix}", PREFIX
        )
        assert "${" not in expression
        expression = (
            expression.replace("resource.name", "name")
            .replace(".startsWith(", ".startswith(")
            .replace(".endsWith(", ".endswith(")
        )
        values.append(eval(expression, {"__builtins__": {}}, {"name": name}))
    assert all(values) is allowed
    binding = terraform_resource_block(
        text, "google_storage_bucket_iam_member", "catalog_viewer_object_viewer"
    )
    assert 'role   = "roles/storage.objectViewer"' in binding
    assert (
        'expression  = "(${local.catalog_viewer_object_viewer_condition}) || (${local.catalog_viewer_identity_review_condition})"'
        in binding
    )
    preview = (ROOT / "terraform/envs/prod/preview_terraform_iam.tf").read_text()
    assert "expression  = local.catalog_viewer_object_viewer_condition" in preview
    assert "catalog_viewer_identity_review_condition" not in preview


def test_notification_origin_comes_from_the_existing_authenticated_viewer():
    text = (ROOT / "terraform/envs/prod/monitoring_slack.tf").read_text()
    service = terraform_resource_block(
        text, "google_cloud_run_v2_service", "monitoring_slack"
    )
    assert 'name  = "CATALOG_VIEWER_URL"' in service
    assert "value = data.google_cloud_run_v2_service.catalog_viewer_live.uri" in service
