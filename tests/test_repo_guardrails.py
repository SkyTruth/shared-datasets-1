from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import repo_guardrails


CATALOG_BASE = "asset_slug,title\nexample-asset,Old title\n"
CATALOG_HEAD = "asset_slug,title\nexample-asset,New title\n"


class RepoGuardrailsTests(unittest.TestCase):
    def test_catalog_csv_changes_require_matching_asset_doc_change(self):
        changes = [repo_guardrails.ChangedFile("M", repo_guardrails.CATALOG_PATH)]

        with mock.patch.object(
            repo_guardrails,
            "git_show",
            side_effect=lambda ref, _path, *, repo_root: CATALOG_BASE if ref == "base" else CATALOG_HEAD,
        ):
            errors = repo_guardrails.check_catalog_csv_source(
                changes,
                base="base",
                head="head",
                repo_root=Path("."),
            )

        self.assertIn("example-asset", errors[0])

    def test_catalog_csv_changes_accept_matching_asset_doc_change(self):
        changes = [
            repo_guardrails.ChangedFile("M", repo_guardrails.CATALOG_PATH),
            repo_guardrails.ChangedFile("M", "docs/assets/example-asset.md"),
        ]

        with mock.patch.object(
            repo_guardrails,
            "git_show",
            side_effect=lambda ref, _path, *, repo_root: CATALOG_BASE if ref == "base" else CATALOG_HEAD,
        ):
            errors = repo_guardrails.check_catalog_csv_source(
                changes,
                base="base",
                head="head",
                repo_root=Path("."),
            )

        self.assertEqual(errors, [])

    def test_top_level_category_changes_require_label(self):
        before = "categories:\n  100-geographic-reference:\n    subcategories: {}\n"
        after = before + "  900-new-category:\n    subcategories: {}\n"

        with mock.patch.object(
            repo_guardrails,
            "git_show",
            side_effect=lambda ref, _path, *, repo_root: before if ref == "base" else after,
        ):
            errors = repo_guardrails.check_top_level_categories(
                base="base",
                head="head",
                repo_root=Path("."),
                labels=set(),
            )

        self.assertIn("approval label", errors[0])

    def test_top_level_category_changes_accept_approval_label(self):
        before = "categories:\n  100-geographic-reference:\n    subcategories: {}\n"
        after = before + "  900-new-category:\n    subcategories: {}\n"

        with mock.patch.object(
            repo_guardrails,
            "git_show",
            side_effect=lambda ref, _path, *, repo_root: before if ref == "base" else after,
        ):
            errors = repo_guardrails.check_top_level_categories(
                base="base",
                head="head",
                repo_root=Path("."),
                labels={"approved-taxonomy-change"},
            )

        self.assertEqual(errors, [])

    def test_approved_format_constant_changes_require_label(self):
        def fake_show(ref, path, *, repo_root):
            if path != "scripts/catalog_docs.py":
                return None
            if ref == "base":
                return 'APPROVED_CANONICAL_FORMATS = {"fgb", "csv"}\n'
            return 'APPROVED_CANONICAL_FORMATS = {"fgb", "csv", "parquet"}\n'

        with mock.patch.object(repo_guardrails, "git_show", side_effect=fake_show):
            errors = repo_guardrails.check_approved_formats(
                base="base",
                head="head",
                repo_root=Path("."),
                labels=set(),
            )

        self.assertIn("approved canonical/data format constants changed", errors[0])

    def test_second_iac_framework_files_require_label(self):
        changes = [repo_guardrails.ChangedFile("A", "Pulumi.yaml")]

        errors = repo_guardrails.check_second_iac_framework(changes, repo_root=Path("."), labels=set())

        self.assertIn("second IaC framework", errors[0])
        self.assertEqual(
            repo_guardrails.check_second_iac_framework(
                changes,
                repo_root=Path("."),
                labels={"approved-second-iac-framework"},
            ),
            [],
        )

    def test_ingestion_jobs_must_not_use_gcs_delete_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "ingestion/example_job"
            job.mkdir(parents=True)
            (job / "README.md").write_text("# Example\n")
            (job / "run.py").write_text("def run(bucket):\n    bucket.delete_blob('releases/2026-01-01/file.fgb')\n")

            errors = repo_guardrails.check_ingestion_no_gcs_deletes(root)

        self.assertIn("GCS delete operation", errors[0])

    def test_ingestion_jobs_require_skip_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "ingestion/example_job"
            job.mkdir(parents=True)
            (job / "README.md").write_text("# Example\n")
            (job / "run.py").write_text("def run():\n    return None\n")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_example_job.py").write_text(
                "def test_run_skips_when_source_unchanged():\n    assert 'skipped' == 'skipped'\n"
            )

            errors = repo_guardrails.check_ingestion_skip_tests(root)

        self.assertEqual(errors, [])

    def test_secret_scanner_flags_tracked_private_key_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("-" * 5 + "BEGIN PRIVATE KEY" + "-" * 5)
            with mock.patch.object(repo_guardrails, "tracked_files", return_value=["README.md"]):
                errors = repo_guardrails.check_secrets(root)

        self.assertIn("private key", errors[0])

    def test_terraform_static_rejects_bucket_object_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tf_dir = root / "terraform"
            tf_dir.mkdir()
            (tf_dir / "main.tf").write_text('resource "google_storage_bucket_object" "dataset" {}\n')

            errors = repo_guardrails.check_terraform_static(root)

        self.assertIn("Terraform-managed dataset object", errors[0])

    def test_docs_must_not_recommend_local_production_terraform_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            (docs / "deploy.md").write_text(
                "Deploy production changes:\n"
                "terraform -chdir=terraform/envs/prod apply\n"
            )

            errors = repo_guardrails.check_no_local_terraform_apply_guidance(root)

        self.assertIn("local production Terraform apply guidance", errors[0])

    def test_docs_may_describe_break_glass_or_protected_workflow_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            skills = root / ".claude/skills/protected-terraform-apply"
            docs.mkdir(parents=True)
            skills.mkdir(parents=True)
            (docs / "deploy.md").write_text(
                "The protected production workflow runs terraform -chdir=terraform/envs/prod apply after review and merge.\n"
            )
            (skills / "SKILL.md").write_text(
                "Do not run `terraform apply` locally. Local use of `scripts/terraform_prod_apply.py` is break-glass only.\n"
            )

            errors = repo_guardrails.check_no_local_terraform_apply_guidance(root)

        self.assertEqual(errors, [])

    def test_workflow_boundaries_require_main_for_gcp_auth_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "unsafe.yml").write_text(
                "name: Unsafe\n"
                "on:\n"
                "  workflow_dispatch:\n"
                "jobs:\n"
                "  unsafe:\n"
                "    steps:\n"
                "      - uses: actions/checkout@v4\n"
                "      - uses: google-github-actions/auth@v2\n"
            )

            errors = repo_guardrails.check_workflow_boundaries(root)

        self.assertTrue(any("must validate refs/heads/main" in error for error in errors))
        self.assertTrue(any("must check out trusted main code" in error for error in errors))

    def test_workflow_boundaries_accept_main_guarded_gcp_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "safe.yml").write_text(
                "name: Safe\n"
                "on:\n"
                "  workflow_dispatch:\n"
                "jobs:\n"
                "  safe:\n"
                "    steps:\n"
                "      - name: Validate main ref\n"
                "        run: |\n"
                "          if [[ \"${GITHUB_REF}\" != \"refs/heads/main\" ]]; then exit 1; fi\n"
                "      - uses: actions/checkout@v4\n"
                "        with:\n"
                "          ref: main\n"
                "      - uses: google-github-actions/auth@v2\n"
            )

            errors = repo_guardrails.check_workflow_boundaries(root)

        self.assertEqual(errors, [])

    def test_workflow_boundaries_accept_feature_preview_dropdown_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "feature-preview-deploy.yml").write_text(
                "name: Deploy Feature Branch to Preview\n"
                "on:\n"
                "  workflow_dispatch:\n"
                "jobs:\n"
                "  preview:\n"
                "    steps:\n"
                "      - name: Validate selected ref\n"
                "        env:\n"
                "          PREVIEW_REF: ${{ github.ref_name }}\n"
                "          PREVIEW_SOURCE_REF: ${{ github.ref }}\n"
                "        run: |\n"
                "          echo \"Select the branch or tag to deploy from the workflow branch dropdown.\"\n"
                "      - uses: actions/checkout@v4\n"
                "        with:\n"
                "          ref: main\n"
                "      - uses: actions/checkout@v4\n"
                "        with:\n"
                "          ref: ${{ github.ref }}\n"
                "          path: preview-source\n"
                "      - uses: google-github-actions/auth@v2\n"
            )

            errors = repo_guardrails.check_workflow_boundaries(root)

        self.assertEqual(errors, [])

    def test_workflow_boundaries_keep_dropdown_exception_preview_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "unsafe.yml").write_text(
                "name: Unsafe\n"
                "on:\n"
                "  workflow_dispatch:\n"
                "jobs:\n"
                "  unsafe:\n"
                "    steps:\n"
                "      - name: Validate selected ref\n"
                "        env:\n"
                "          PREVIEW_REF: ${{ github.ref_name }}\n"
                "          PREVIEW_SOURCE_REF: ${{ github.ref }}\n"
                "        run: |\n"
                "          echo \"Select the branch or tag to deploy from the workflow branch dropdown.\"\n"
                "      - uses: actions/checkout@v4\n"
                "        with:\n"
                "          ref: main\n"
                "      - uses: actions/checkout@v4\n"
                "        with:\n"
                "          ref: ${{ github.ref }}\n"
                "      - uses: google-github-actions/auth@v2\n"
            )

            errors = repo_guardrails.check_workflow_boundaries(root)

        self.assertTrue(any("must validate refs/heads/main" in error for error in errors))

    def test_workflow_boundaries_reject_production_uri_in_preview_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "preview.yml").write_text(
                "name: Preview\n"
                "env:\n"
                "  SHARED_DATASETS_BUCKET: skytruth-shared-datasets-1-preview\n"
                "jobs:\n"
                "  preview:\n"
                "    steps:\n"
                "      - run: echo gs://skytruth-shared-datasets-1/path/to/object\n"
            )

            errors = repo_guardrails.check_workflow_boundaries(root)

        self.assertTrue(any("preview workflows must not accept production bucket URIs" in error for error in errors))

    def test_workflow_boundaries_reject_single_object_publish_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "publish.yml").write_text(
                "name: Publish\n"
                "on:\n"
                "  workflow_dispatch:\n"
                "    inputs:\n"
                "      source_uri:\n"
                "        description: Single-object fallback source\n"
                "jobs:\n"
                "  promote:\n"
                "    name: Promote staged object manually\n"
            )

            errors = repo_guardrails.check_workflow_boundaries(root)

        self.assertTrue(any("single-object dataset publish fallback is not allowed" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
