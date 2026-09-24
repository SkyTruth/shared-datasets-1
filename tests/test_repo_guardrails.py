from __future__ import annotations

import tempfile
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

import yaml

from scripts import repo_guardrails


CATALOG_BASE = "asset_slug,title\nexample-asset,Old title\n"
CATALOG_HEAD = "asset_slug,title\nexample-asset,New title\n"


class RepoGuardrailsTests(unittest.TestCase):
    def check_workflow_fixture(self, workflow):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / ".github/workflows"
            directory.mkdir(parents=True)
            (directory / "apply.yml").write_text(yaml.safe_dump(workflow))
            return repo_guardrails.check_workflow_boundaries(root)

    def production_workflow(self, command):
        return {
            "on": {"workflow_call": {"inputs": {
                "terraform_dir": {"default": "terraform/envs/prod", "type": "string"},
            }}},
            "env": {"TERRAFORM_DIR": "${{ inputs.terraform_dir }}"},
            "jobs": {"apply": {
                "concurrency": {
                    "group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False,
                },
                "steps": [{"run": '\n'.join([
                    'if [[ "${GITHUB_REF}" != "refs/heads/main" ]]; then exit 1; fi',
                    'allowed_exact="example"',
                    command,
                ])}],
            }},
        }

    def test_production_queue_contract_for_direct_and_wrapped_commands(self):
        for executable in ('terraform', 'bash "${GITHUB_WORKSPACE}/scripts/terraform_retry.sh"'):
            for directory in ('terraform/envs/prod', '"${TERRAFORM_DIR}"'):
                for operation in ('plan', 'apply'):
                    command = f'{executable} -chdir={directory} \\\n  {operation} -input=false'
                    workflow = self.production_workflow(command)
                    with self.subTest(command=command):
                        self.assertEqual(self.check_workflow_fixture(workflow), [])
                    for field, value in (
                        ('group', 'prod-terraform-state-another-writer'),
                        ('group', 'prod-terraform-state-${{ inputs.sync_name }}'),
                        ('queue', None),
                        ('queue', 'single'),
                        ('cancel-in-progress', True),
                        ('cancel-in-progress', None),
                        ('cancel-in-progress', 0),
                    ):
                        changed = deepcopy(workflow)
                        concurrency = changed['jobs']['apply']['concurrency']
                        if value is None:
                            del concurrency[field]
                        else:
                            concurrency[field] = value
                        with self.subTest(command=command, field=field, value=value):
                            errors = self.check_workflow_fixture(changed)
                            self.assertTrue(any('job apply: missing prod Terraform state concurrency' in e for e in errors), errors)

    def test_unrelated_job_or_workflow_concurrency_does_not_protect_writer(self):
        for location in ('unrelated_job', 'parent_workflow'):
            workflow = self.production_workflow('terraform -chdir=terraform/envs/prod apply plan.tfplan')
            concurrency = workflow['jobs']['apply'].pop('concurrency')
            if location == 'unrelated_job':
                workflow['jobs']['unrelated'] = {'concurrency': concurrency, 'steps': [{'run': 'echo ready'}]}
            else:
                workflow['concurrency'] = concurrency
            with self.subTest(location=location):
                errors = self.check_workflow_fixture(workflow)
                self.assertTrue(any('job apply: missing prod Terraform state concurrency' in e for e in errors), errors)

    def test_reusable_callers_do_not_hold_the_execution_queue(self):
        workflow = {'jobs': {'sync': {'uses': repo_guardrails.TARGET_APPLY_WORKFLOW}}}
        self.assertEqual(self.check_workflow_fixture(workflow), [])
        for location in ('job', 'parent'):
            changed = deepcopy(workflow)
            owner = changed['jobs']['sync'] if location == 'job' else changed
            owner['concurrency'] = dict(repo_guardrails.PROD_TERRAFORM_CONCURRENCY)
            with self.subTest(location=location):
                errors = self.check_workflow_fixture(changed)
                self.assertTrue(any('caller must leave concurrency' in e or 'parent workflow must not hold' in e for e in errors), errors)

    def test_preview_job_does_not_inherit_sibling_production_queue_requirement(self):
        workflow = self.production_workflow('terraform -chdir=terraform/envs/prod apply plan.tfplan')
        workflow['jobs']['preview'] = {
            'steps': [{'run': 'terraform -chdir=terraform/envs/preview apply plan.tfplan'}],
        }
        self.assertFalse(repo_guardrails.job_uses_prod_terraform(workflow, workflow['jobs']['preview']))

    def test_all_production_execution_jobs_share_the_queue(self):
        found = set()
        root = Path(__file__).resolve().parents[1]
        for path in (root / '.github/workflows').glob('*.yml'):
            workflow = yaml.safe_load(path.read_text())
            for name, job in workflow['jobs'].items():
                if repo_guardrails.job_uses_prod_terraform(workflow, job):
                    found.add((path.name, name))
                    self.assertEqual(job['concurrency'], repo_guardrails.PROD_TERRAFORM_CONCURRENCY)
        self.assertEqual(found, {
            ('prod-terraform-target-apply.yml', 'sync'),
            ('wdpa-monthly-deploy.yml', 'deploy'),
            ('sea-ice-daily-deploy.yml', 'deploy'),
            ('eamlis-monthly-deploy.yml', 'deploy'),
            ('metadata-service-deploy.yml', 'deploy'),
            ('catalog-viewer-deploy.yml', 'deploy'),
            ('pmtiles-cdn-sync.yml', 'sync'),
        })

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

    def immutable_bootstrap_workflow(self):
        return {
            "on": {"workflow_dispatch": {}},
            "jobs": {"publish": {"steps": [
                {"run": '\n'.join([
                    'set -euo pipefail',
                    'if [[ "${GITHUB_REF}" != "refs/heads/main" ||',
                    '      "${GITHUB_WORKFLOW_REF}" != "${GITHUB_REPOSITORY}/.github/workflows/apply.yml@refs/heads/main" ]]; then',
                    '  echo "Execution must use this workflow from main." >&2',
                    '  exit 1',
                    'fi',
                ])},
                {"uses": "actions/checkout@v4", "with": {"ref": "${{ github.workflow_sha }}"}},
                {"uses": "google-github-actions/auth@v2"},
            ]}},
        }

    def test_workflow_boundaries_accept_guarded_immutable_main_bootstrap(self):
        self.assertEqual(self.check_workflow_fixture(self.immutable_bootstrap_workflow()), [])

    def test_immutable_bootstrap_rejects_inherited_shell_overrides(self):
        for scope in ("workflow", "job"):
            workflow = self.immutable_bootstrap_workflow()
            owner = workflow if scope == "workflow" else workflow["jobs"]["publish"]
            owner["defaults"] = {"run": {"shell": 'bash -c "true"'}}
            with self.subTest(scope=scope):
                self.assertTrue(any(
                    "pinned main-workflow revision" in error for error in self.check_workflow_fixture(workflow)
                ))

    def test_immutable_bootstrap_does_not_trust_markers_or_arbitrary_checkouts(self):
        mutations = {
            "missing guard": lambda job: job["steps"].pop(0),
            "late guard": lambda job: job["steps"].insert(0, {"run": "echo before guard"}),
            "conditional guard": lambda job: job["steps"][0].update({"if": "${{ false }}"}),
            "conditional checkout": lambda job: job["steps"][1].update({"if": "${{ false }}"}),
            "comment only": lambda job: job["steps"][0].update(run="\n".join(
                "# " + line for line in job["steps"][0]["run"].splitlines()
            )),
            "wrong workflow path": lambda job: job["steps"][0].update(
                run=job["steps"][0]["run"].replace("apply.yml", "other.yml")
            ),
            "wrong shell": lambda job: job["steps"][0].update(shell="python"),
        }
        for ref in ("${{ github.sha }}", "${{ github.event.pull_request.head.sha }}", "${{ inputs.ref }}", "${{ needs.other.outputs.sha }}"):
            mutations[ref] = lambda job, ref=ref: job["steps"][1]["with"].update(ref=ref)
        for key in ("repository", "path"):
            mutations[key] = lambda job, key=key: job["steps"][1]["with"].update({key: "other"})
        for owner in ("job", "guard", "checkout"):
            for ignored in (True, "${{ inputs.ignore_failure }}"):
                def suppress(job, owner=owner, ignored=ignored):
                    target = job if owner == "job" else job["steps"][0 if owner == "guard" else 1]
                    target["continue-on-error"] = ignored
                mutations[f"ignored {owner} {ignored}"] = suppress
        for label, mutate in mutations.items():
            workflow = self.immutable_bootstrap_workflow()
            mutate(workflow["jobs"]["publish"])
            with self.subTest(label=label):
                errors = self.check_workflow_fixture(workflow)
                self.assertTrue(any("pinned main-workflow revision" in error for error in errors), errors)

    def test_real_immutable_bootstrap_guards_execute_before_checkout(self):
        for filename, job_name in (
            ("publish-dataset.yml", "reviewed_pr_plans"),
            ("metadata-localization.yml", "materialize"),
        ):
            path = repo_guardrails.REPO_ROOT / ".github/workflows" / filename
            workflow = yaml.safe_load(path.read_text())
            relative_path = path.relative_to(repo_guardrails.REPO_ROOT).as_posix()
            self.assertTrue(repo_guardrails.has_guarded_main_workflow_checkout(workflow, relative_path))
            script = workflow["jobs"][job_name]["steps"][0]["run"]
            workflow_ref = f"SkyTruth/shared-datasets-1/{relative_path}@refs/heads/main"
            for ref, source, expected in (
                ("refs/heads/main", workflow_ref, 0),
                ("refs/heads/feature", workflow_ref, 1),
                ("refs/tags/main", workflow_ref, 1),
                ("refs/heads/main", workflow_ref.replace(filename, "other.yml"), 1),
                ("refs/heads/main", workflow_ref.replace("SkyTruth/", "other/"), 1),
                ("refs/heads/main", workflow_ref.replace("@refs/heads/main", "@refs/heads/feature"), 1),
            ):
                with self.subTest(workflow=filename, ref=ref, source=source):
                    result = subprocess.run(
                        ["/bin/bash", "-c", script],
                        env={"GITHUB_REF": ref, "GITHUB_WORKFLOW_REF": source, "GITHUB_REPOSITORY": "SkyTruth/shared-datasets-1"},
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)

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

    def test_workflow_boundaries_detect_prod_apply_with_flags_and_yaml_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "apply.yaml").write_text(
                "name: Apply\n"
                "on:\n"
                "  push:\n"
                "jobs:\n"
                "  apply:\n"
                "    steps:\n"
                "      - run: terraform -chdir=terraform/envs/prod -input=false apply plan.tfplan\n"
            )

            errors = repo_guardrails.check_workflow_boundaries(root)

        self.assertTrue(any("missing main ref validation" in error for error in errors))
        self.assertTrue(any("missing prod Terraform state concurrency" in error for error in errors))
        self.assertTrue(any("missing resource-change allowlist" in error for error in errors))

    def test_workflow_boundaries_fail_closed_on_undecodable_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "binary.yml").write_bytes(b"\xff\xfe\x00broken")

            errors = repo_guardrails.check_workflow_boundaries(root)

        self.assertTrue(any("not valid UTF-8" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
