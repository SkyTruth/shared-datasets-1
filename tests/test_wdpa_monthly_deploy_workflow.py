from __future__ import annotations

import copy
import json
import unittest
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from workflow_helpers import (
    load_workflow,
    python_literal_string_set,
    terraform_targets,
    workflow_steps_by_name,
    workflow_triggers,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/wdpa-monthly-deploy.yml"
DOCKERFILE = REPO_ROOT / "ingestion/wdpa_monthly/Dockerfile"

REQUIRED_SCRIPT_COPIES = (
    "scripts/feature_metadata_localization.py",
    "scripts/feature_metadata_translation_reuse.py",
    "scripts/translation_local_io.py",
    "scripts/pmtiles_zoom.py",
    "scripts/release_feature_model.py",
    "scripts/slack_notify.py",
    "scripts/vector_asset.py",
)


class WdpaMonthlyDeployWorkflowTests(unittest.TestCase):
    def test_publication_layer_keeps_native_producer_and_updates_translation_publisher(self):
        recipe = (REPO_ROOT / "ingestion/wdpa_monthly/Dockerfile.promotion").read_text()
        self.assertEqual(re.findall(r"^FROM (.+)$", recipe, re.MULTILINE), ["${ACCEPTED_IMAGE}"])
        self.assertNotRegex(recipe, r"(?m)^RUN ")
        self.assertEqual(
            re.findall(r"^COPY (\S+) (\S+)$", recipe, re.MULTILINE),
            [
                ("ingestion/wdpa_monthly/artifact_bundle.py", "/app/ingestion/wdpa_monthly/artifact_bundle.py"),
                ("scripts/wdpa_processing_gate.py", "/app/scripts/wdpa_processing_gate.py"),
                ("ingestion/wdpa_monthly/publication_only.py", "/app/ingestion/wdpa_monthly/publication_only.py"),
                ("ingestion/common", "/app/ingestion/common"),
                ("ingestion/wdpa_monthly/run.py", "/app/ingestion/wdpa_monthly/run.py"),
                ("ingestion/wdpa_monthly/translations.py", "/app/ingestion/wdpa_monthly/translations.py"),
                ("scripts/catalog_csv.py", "/app/scripts/catalog_csv.py"),
                ("scripts/release_feature_model.py", "/app/scripts/release_feature_model.py"),
                ("scripts/feature_metadata_localization.py", "/app/scripts/feature_metadata_localization.py"),
                ("scripts/feature_metadata_translation_reuse.py", "/app/scripts/feature_metadata_translation_reuse.py"),
                ("scripts/slack_notify.py", "/app/scripts/slack_notify.py"),
                ("catalog/shared-datasets-catalog.csv", "/app/catalog/shared-datasets-catalog.csv"),
            ],
        )
        self.assertIn('CMD ["python", "-m", "ingestion.wdpa_monthly.publication_only"]', recipe)
        self.assertIn("WDPA_ACCEPTED_BUILD_SOURCE_SHA256=${ACCEPTED_BUILD_SOURCE_SHA256}", recipe)

    def test_allowlist_cannot_increase_worker_size_or_use_ram_scratch(self):
        run = workflow_steps_by_name(load_workflow(DEPLOY_WORKFLOW), "deploy")["Enforce wdpa-monthly resource-change allowlist"]["run"]
        code = run.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        image = "registry/wdpa@sha256:" + "a" * 64
        bundle = json.loads((REPO_ROOT / "catalog/wdpa-processing-acceptance.json").read_text())["build"]["artifact_bundle"]
        after = {"template": [{"template": [{
            "timeout": "86400s", "volumes": [{"name": "work", "empty_dir": [{"medium": "DISK", "size_limit": "100Gi"}]}],
            "containers": [{"image": image, "resources": [{"limits": {"cpu": "4", "memory": "8Gi"}}],
                "volume_mounts": [{"name": "work", "mount_path": "/work"}],
                "env": [{"name": "TMPDIR", "value": "/work/tmp"}, {"name": "SHARED_DATASETS_WORKDIR", "value": "/work/shared-datasets-1"},
                        {"name": "WDPA_PROMOTION_BUNDLE", "value": json.dumps(bundle)}]}],
        }]}]}
        for mismatch in (None, "memory", "disk", "scratch", "missing_bundle", "wrong_bundle", "persistent_date"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as root:
                proposed = copy.deepcopy(after)
                task = proposed["template"][0]["template"][0]
                if mismatch == "memory":
                    task["containers"][0]["resources"][0]["limits"]["memory"] = "16Gi"
                elif mismatch == "disk":
                    task["volumes"][0]["empty_dir"][0]["medium"] = "MEMORY"
                elif mismatch == "scratch":
                    task["containers"][0]["env"][0]["value"] = "/tmp"
                elif mismatch == "missing_bundle":
                    task["containers"][0]["env"].pop()
                elif mismatch == "wrong_bundle":
                    task["containers"][0]["env"][-1]["value"] = json.dumps({**bundle, "generation": bundle["generation"] + 1})
                elif mismatch == "persistent_date":
                    task["containers"][0]["env"].append({"name": "RUN_DATE", "value": "2026-10-01"})
                plan = Path(root) / "plan.json"
                plan.write_text(json.dumps({"resource_changes": [{
                    "address": "module.wdpa_monthly_job.google_cloud_run_v2_job.this",
                    "change": {"actions": ["update"], "after": proposed}}]}))
                result = subprocess.run([sys.executable, "-c", code, str(plan), image], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0 if mismatch is None else 1, result.stdout + result.stderr)

    def test_scheduled_job_uses_reviewed_bundle_without_pinning_the_calendar_date(self):
        terraform = (REPO_ROOT / "terraform/envs/prod/wdpa_monthly.tf").read_text()
        self.assertRegex(terraform, r'WDPA_PROMOTION_BUNDLE\s*=\s*jsonencode\(jsondecode\(file\("\$\{path.module\}/../../../catalog/wdpa-processing-acceptance.json"\)\).build.artifact_bundle\)')
        self.assertNotRegex(terraform, r"(?m)^\s*RUN_DATE\s*=")

    def test_wdpa_monthly_deploy_workflow_is_protected_and_digest_pinned(self):
        workflow = load_workflow(DEPLOY_WORKFLOW)
        trigger = workflow_triggers(workflow)
        deploy = workflow["jobs"]["deploy"]
        env = workflow["env"]
        steps = workflow_steps_by_name(workflow, "deploy")
        step_names = list(steps)

        self.assertEqual(workflow["name"], "WDPA monthly deploy")
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertIn("workflow_dispatch", trigger)
        self.assertIn("canary_run_date", trigger["workflow_dispatch"]["inputs"])
        push_paths = set(trigger["push"]["paths"])
        self.assertIn(".github/workflows/wdpa-monthly-deploy.yml", push_paths)
        self.assertIn("catalog/feature-identity-resolutions/**", push_paths)
        self.assertIn("ingestion/common/**", push_paths)
        self.assertIn("ingestion/wdpa_monthly/**", push_paths)
        for script_path in REQUIRED_SCRIPT_COPIES:
            self.assertIn(script_path, push_paths)
        self.assertEqual(deploy["environment"], "shared-datasets-production")
        self.assertEqual(
            deploy["concurrency"],
            {"group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False},
        )
        self.assertEqual(steps["Check out repository"]["with"]["ref"], "main")
        self.assertEqual(steps["Check out repository"]["with"]["fetch-depth"], 0)
        self.assertEqual(env["IMAGE_NAME"], "wdpa-monthly")
        self.assertEqual(env["JOB_NAME"], "wdpa-monthly")

        promote_run = steps["Prepare reviewed WDPA publication image"]["run"]
        self.assertIn("catalog/wdpa-processing-acceptance.json", promote_run)
        self.assertIn("docker pull --platform linux/amd64", promote_run)
        self.assertIn("docker image inspect", promote_run)
        self.assertIn("--print-source-digest", promote_run)
        self.assertIn("Dockerfile.promotion", promote_run)
        self.assertIn("ACCEPTED_BUILD_SOURCE_SHA256=${expected_source}", promote_run)
        self.assertNotIn("uv run --no-sync python scripts/wdpa_processing_gate.py --print-source-digest", promote_run)
        self.assertIn("WDPA_MONTHLY_IMAGE_TAG=${image_tag}", promote_run)
        self.assertLess(
            step_names.index("Require WDPA processing acceptance and disk quota"),
            step_names.index("Prepare reviewed WDPA publication image"),
        )
        self.assertLess(
            step_names.index("Verify feature-ID publication state"),
            step_names.index("Prepare reviewed WDPA publication image"),
        )

        self.assertIn("tippecanoe --version", steps["Smoke-test native tools in image"]["run"])
        import_run = steps["Smoke-test job import closure in image"]["run"]
        self.assertIn("import ingestion.wdpa_monthly.run", import_run)
        self.assertIn("scripts.feature_metadata_localization", import_run)
        self.assertIn("scripts.translation_local_io", import_run)
        self.assertIn("scripts.vector_asset", import_run)

        push_run = steps["Push wdpa-monthly image"]["run"]
        self.assertIn("docker push", push_run)
        self.assertIn("docker buildx imagetools inspect", push_run)
        self.assertIn("WDPA_MONTHLY_IMAGE=${image_ref}", push_run)

        plan_run = steps["Terraform plan"]["run"]
        self.assertEqual(
            terraform_targets(plan_run),
            {
                "module.wdpa_monthly_job.google_cloud_run_v2_job.this",
                "module.wdpa_observer_service_account.google_service_account.this",
                "google_project_iam_custom_role.wdpa_execution_reader",
                "google_cloud_run_v2_job_iam_member.wdpa_observer_execution_reader",
                "google_storage_bucket_iam_member.wdpa_observer_status_writer",
                "google_service_account_iam_member.wdpa_observer_deployer",
                "module.wdpa_execution_observer_job.google_cloud_run_v2_job.this",
                "google_cloud_run_v2_job_iam_member.wdpa_observer_scheduler_invoker",
                "module.wdpa_execution_observer_scheduler.google_cloud_scheduler_job.this",
                "google_iam_deny_policy.canonical_destructive_actions[0]",
            },
        )
        self.assertIn("wdpa_monthly_image=${WDPA_MONTHLY_IMAGE}", plan_run)
        self.assertIn("eamlis_monthly_image=unused-by-wdpa-monthly-deploy", plan_run)
        self.assertIn("sea_ice_daily_image=unused-by-wdpa-monthly-deploy", plan_run)
        all_step_runs = "\n".join(str(step.get("run", "")) for step in steps.values())
        self.assertNotIn("gcloud run jobs describe eamlis-monthly", all_step_runs)
        self.assertNotIn("gcloud run jobs describe sea-ice-daily", all_step_runs)

        enforce_run = steps["Enforce wdpa-monthly resource-change allowlist"]["run"]
        self.assertEqual(
            python_literal_string_set(enforce_run, "allowed_exact"),
            {
                "module.wdpa_monthly_job.google_cloud_run_v2_job.this",
                "module.wdpa_observer_service_account.google_service_account.this",
                "google_project_iam_custom_role.wdpa_execution_reader",
                "google_cloud_run_v2_job_iam_member.wdpa_observer_execution_reader",
                "google_storage_bucket_iam_member.wdpa_observer_status_writer",
                "google_service_account_iam_member.wdpa_observer_deployer",
                "module.wdpa_execution_observer_job.google_cloud_run_v2_job.this",
                "google_cloud_run_v2_job_iam_member.wdpa_observer_scheduler_invoker",
                "module.wdpa_execution_observer_scheduler.google_cloud_scheduler_job.this",
                "google_iam_deny_policy.canonical_destructive_actions[0]",
            },
        )
        self.assertIn("if actions not in permitted", enforce_run)
        self.assertIn("image != expected_image", enforce_run)
        self.assertIn("terraform -chdir=terraform/envs/prod show -json", steps["Export Terraform plan JSON"]["run"])
        self.assertIn("terraform_retry.sh\" -chdir=terraform/envs/prod apply", steps["Terraform apply"]["run"])
        self.assertLess(
            step_names.index("Enforce wdpa-monthly resource-change allowlist"),
            step_names.index("Terraform apply"),
        )

        self.assertLess(step_names.index("Terraform apply"), step_names.index("Confirm deployed digest"))
        self.assertLess(step_names.index("Confirm deployed digest"), step_names.index("Execute wdpa-monthly canary"))
        self.assertLess(step_names.index("Execute wdpa-monthly canary"), step_names.index("Watch wdpa-monthly canary"))

        canary_run = steps["Execute wdpa-monthly canary"]["run"]
        self.assertIn("--async", canary_run)
        self.assertIn("RUN_DATE=${build_date}", canary_run)
        self.assertIn("WDPA_PROMOTION_BUNDLE=${bundle}", canary_run)
        watch_run = steps["Watch wdpa-monthly canary"]["run"]
        self.assertIn("gcloud run jobs executions describe", watch_run)
        self.assertEqual(steps["Watch wdpa-monthly canary"]["if"], steps["Execute wdpa-monthly canary"]["if"])

    def test_producer_image_mismatch_fails_before_building_the_consumer(self):
        steps = workflow_steps_by_name(load_workflow(DEPLOY_WORKFLOW), "deploy")
        script = steps["Prepare reviewed WDPA publication image"]["run"]
        image = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-validation@sha256:" + "a" * 64
        config = "sha256:" + "b" * 64
        fake_tools = '''docker() {
          printf '%s\\n' "$*" >> "$TEST_DOCKER_CALLS"
          case "$1" in
            pull) return "$TEST_PULL_RESULT" ;;
            image) printf '%s\\n' "$TEST_CONFIG" ;;
            run) printf '%s\\n' "$TEST_SOURCE" ;;
            build) return 0 ;;
            *) return 1 ;;
          esac
        }
        '''
        for mismatch in (None, "config", "source", "pull"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "catalog").mkdir()
                (root / "catalog/wdpa-processing-acceptance.json").write_text(json.dumps({
                    "build": {"cloud_image": image, "image_digest": config, "source_tree_sha256": "expected-source"}}))
                calls = root / "docker-calls"
                env_file = root / "env"
                result = subprocess.run(
                    ["bash", "-c", fake_tools + script], cwd=root,
                    env={**os.environ, "TEST_CONFIG": "wrong" if mismatch == "config" else config,
                         "TEST_SOURCE": "wrong" if mismatch == "source" else "expected-source",
                         "TEST_PULL_RESULT": "1" if mismatch == "pull" else "0",
                         "TEST_DOCKER_CALLS": str(calls), "GITHUB_ENV": str(env_file),
                         "REGION": "us-central1", "GOOGLE_CLOUD_PROJECT": "shared-datasets-1",
                         "ARTIFACT_REGISTRY_REPOSITORY": "shared-datasets-jobs",
                         "IMAGE_NAME": "wdpa-monthly", "GITHUB_SHA": "c" * 40, "GITHUB_RUN_ID": "123"},
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0 if mismatch is None else 1, result.stderr)
                recorded = calls.read_text().splitlines()
                self.assertEqual(recorded[0], f"pull --platform linux/amd64 {image}")
                self.assertEqual(any(call.startswith("build ") for call in recorded), mismatch is None)
                self.assertEqual(env_file.exists(), mismatch is None)
                self.assertFalse(any(call.startswith("push ") for call in recorded))
                if mismatch == "config":
                    self.assertFalse(any(call.startswith("run ") for call in recorded))

    def test_paused_schedule_requires_explicit_canary_date(self):
        steps = workflow_steps_by_name(load_workflow(DEPLOY_WORKFLOW), "deploy")
        script = steps["Resolve in-flight canary"]["run"]
        fake_gcloud = '''gcloud() {
          if [[ "$1" == scheduler ]]; then
            printf '%s\\n' "$TEST_SCHEDULE_STATE"
          elif [[ "$1 $2 $3" == "run jobs executions" ]]; then
            echo queried >> "$TEST_EXECUTION_QUERIES"
          else
            return 1
          fi
        }
        '''
        for state, date, expected in (("PAUSED", "", "false"), ("PAUSED", "2026-09-30", "true"), ("ENABLED", "", "true")):
            with self.subTest(state=state, date=date), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "output"
                queries = Path(tmp) / "queries"
                result = subprocess.run(
                    ["bash", "-c", fake_gcloud + script],
                    env={**os.environ, "TEST_SCHEDULE_STATE": state, "CANARY_RUN_DATE": date,
                         "CANCEL_RUNNING_CANARY": "false", "GITHUB_OUTPUT": str(output),
                         "TEST_EXECUTION_QUERIES": str(queries), "JOB_NAME": "wdpa-monthly",
                         "REGION": "us-central1", "GOOGLE_CLOUD_PROJECT": "shared-datasets-1"},
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text().strip(), f"run_canary={expected}")
                self.assertEqual(queries.exists(), expected == "true")

    def test_declared_docker_script_copies_import_without_repo_fallback(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        declared = re.findall(r"^COPY (scripts/\S+\.py) \./(scripts/\S+\.py)$", dockerfile, re.MULTILINE)
        self.assertTrue(declared)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for source, destination in declared:
                target = root / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO_ROOT / source, target)
            result = subprocess.run(
                [sys.executable, "-I", "-c",
                 f"import sys; sys.path.insert(0, {str(root)!r}); "
                 "import scripts.feature_metadata_localization, scripts.translation_local_io, scripts.feature_metadata_translation_reuse; "
                 f"assert scripts.feature_metadata_localization.__file__.startswith({str(root)!r}); "
                 "print('declared copies import successfully')"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "declared copies import successfully")

    def test_ci_filter_selects_helper_and_its_behavior_tests(self):
        ci = load_workflow(REPO_ROOT / ".github/workflows/ci.yml")
        runs = [str(step.get("run", "")) for job in ci["jobs"].values() for step in job.get("steps", [])]
        detection = next(run for run in runs if "geospatial_pattern=" in run)
        pattern = re.search(r"geospatial_pattern='([^']+)'", detection).group(1)
        for path in ("scripts/translation_local_io.py", "tests/test_translation_local_io.py", "scripts/feature_metadata_translation_reuse.py", "tests/test_feature_metadata_translation_reuse.py", "tests/test_wdpa_translation_inputs.py"):
            with self.subTest(path=path):
                self.assertIsNotNone(re.fullmatch(pattern, path))
        self.assertIsNone(re.fullmatch(pattern, "docs/unrelated.md"))
        geospatial_pytest = next(run for run in runs if "geospatial-pytest.xml" in run)
        self.assertIn("tests/test_translation_local_io.py", geospatial_pytest)

    def test_wdpa_monthly_dockerfile_copies_scripts_import_closure(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        for script_path in REQUIRED_SCRIPT_COPIES:
            self.assertIn(
                f"COPY {script_path} ./{script_path}",
                dockerfile,
                f"wdpa-monthly image must copy {script_path}; the job imports it at runtime",
            )
        self.assertIn(
            "COPY catalog/feature-identity-resolutions ./catalog/feature-identity-resolutions",
            dockerfile,
            "wdpa-monthly image must ship reviewed feature-identity resolutions; "
            "the job loads them from catalog/feature-identity-resolutions/ at runtime",
        )


if __name__ == "__main__":
    unittest.main()
