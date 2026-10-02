from pathlib import Path
import os
import subprocess
import unittest
from unittest import mock

from ingestion.common import publication as p
from ingestion.common.reset_controls import ASSET_JOBS, BUCKET
from scripts import publication_rollout_gate as gate
from test_identity_reset import fixture, install_fixture, intent_for, executor
from test_publication import MemoryStore, operation
from workflow_helpers import load_workflow, workflow_steps_by_name

ROOT = Path(__file__).resolve().parents[1]


def installed_store(slugs):
    combined = MemoryStore()
    for slug in slugs:
        store, candidate, _ctx = fixture(slug, bucket=BUCKET)
        install_fixture(store, candidate)
        combined.objects.update(store.objects)
        combined.history.update(store.history)
        combined.serial = max(combined.serial, store.serial)
    return combined


class PublicationDeploymentTests(unittest.TestCase):
    def test_resume_requires_completed_publication_not_just_installed_state(self):
        store, candidate, context = fixture("ims-sea-ice-extent", bucket=BUCKET)
        install_fixture(store, candidate)
        with self.assertRaisesRegex(p.PublicationError, "first publication"):
            gate.check_deployment(store, "sea-ice-daily", require_published=True)
        intent = intent_for(store, candidate, context)
        intent = p.Intent.build({**intent.value, "operations": [*intent.value["operations"], operation(
            "index", "asset_derived", f"gs://{BUCKET}/_catalog/releases/{context.asset_slug}.json",
            {"kind": "derived", "version": p.FINALIZATION_VERSION,
             "parameters": {"kind": "index"}, "dependencies": ["release-manifest"]})]})
        publisher = executor(store)
        derive = publisher.derive
        def fail_index(parameters, results):
            if parameters["kind"] == "index":
                raise RuntimeError("interrupted finalization")
            return derive(parameters, results)
        publisher.derive = fail_index
        with self.assertRaisesRegex(RuntimeError, "interrupted finalization"):
            publisher.run(context, prepare=lambda: intent, local_sources={})
        self.assertEqual(store.read_json(context.receipt_uri).value["phase"], "committed")
        with self.assertRaisesRegex(p.PublicationError, "first publication"):
            gate.check_deployment(store, "sea-ice-daily", require_published=True)
        executor(store).run(context, prepare=mock.Mock(side_effect=AssertionError("must resume")), local_sources={})
        gate.check_deployment(store, "sea-ice-daily", require_published=True)

    def test_missing_state_blocks_and_complete_state_allows_without_mutations(self):
        for job in set(ASSET_JOBS.values()):
            with self.subTest(job=job):
                with self.assertRaisesRegex(p.PublicationError, "install the reviewed"):
                    gate.check_deployment(MemoryStore(), job)
                store = installed_store([slug for slug, owner in ASSET_JOBS.items() if owner == job])
                gate.check_deployment(store, job)
                self.assertEqual(store.events, [])

    def test_both_wdpa_assets_are_required_but_sea_ice_is_independent(self):
        with self.assertRaisesRegex(p.PublicationError, "wdpa-terrestrial"):
            gate.check_deployment(installed_store(["wdpa-marine"]), "wdpa-monthly")
        gate.check_deployment(installed_store(["ims-sea-ice-extent"]), "sea-ice-daily")

    def test_incomplete_install_or_missing_adoption_receipt_blocks(self):
        for defect in ("incomplete", "missing_receipt", "wrong_contract"):
            store = installed_store(["ims-sea-ice-extent"])
            state_uri = next(uri for uri in store.objects if uri.endswith("/state.json"))
            state = store.read_json(state_uri)
            if defect == "missing_receipt":
                del store.objects[state.value["adoption_receipt"]]
            elif defect == "wrong_contract":
                receipt = store.read_json(state.value["adoption_receipt"])
                receipt.value["identity_contract"] = "old-contract"
                store.write_json(receipt.version.path, receipt.value, receipt.version.generation)
            else:
                uri = state_uri.replace("/state.json", "/reset.json")
                marker = store.read_json(uri)
                marker.value.update(phase="activating", state_generation=None)
                store.write_json(uri, marker.value, marker.version.generation)
            with self.subTest(defect=defect), self.assertRaises(p.PublicationError):
                gate.check_deployment(store, "sea-ice-daily")

    def test_workflows_check_real_state_before_build_and_apply_and_leave_eamlis_alone(self):
        for job in ("wdpa-monthly", "sea-ice-daily", "eamlis-monthly"):
            workflow = load_workflow(ROOT / f".github/workflows/{job}-deploy.yml")
            steps = workflow_steps_by_name(workflow, "deploy")
            names = list(steps)
            gate_name = "Verify feature-ID publication state"
            self.assertNotIn("Enforce publication rollout HOLD", names)
            if job == "eamlis-monthly":
                self.assertNotIn(gate_name, names)
                continue
            step = steps[gate_name]
            self.assertNotIn("if", step)
            self.assertNotIn("continue-on-error", step)
            self.assertEqual(step["run"], f"uv run --no-sync python scripts/publication_rollout_gate.py --job {job}")
            self.assertLess(names.index("Authenticate to Google Cloud"), names.index(gate_name))
            image_step = "Promote accepted wdpa-monthly image" if job == "wdpa-monthly" else f"Build {job} image"
            self.assertLess(names.index(gate_name), names.index(image_step))
            self.assertLess(names.index(gate_name), names.index("Terraform plan"))


class ScheduleControlTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow(ROOT / ".github/workflows/ingestion-schedule-control.yml")
        self.steps = workflow_steps_by_name(self.workflow, "control")

    def test_protected_queue_and_resume_requires_publication(self):
        job = self.workflow["jobs"]["control"]
        self.assertEqual(job["environment"], "shared-datasets-production")
        self.assertEqual(job["concurrency"], {"group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False})
        check = self.steps["Verify publication before resuming"]
        self.assertEqual(check["if"], "${{ inputs.action == 'resume' }}")
        self.assertIn('--job "$JOB_NAME" --require-published', check["run"])
        self.assertLess(list(self.steps).index("Verify publication before resuming"), list(self.steps).index("Set schedule state"))

    def test_scope_rejects_other_refs_jobs_and_actions(self):
        script = self.steps["Validate reviewed main and fixed scope"]["run"]
        for ref, job, action, allowed in (
            ("refs/heads/main", "wdpa-monthly", "pause", True),
            ("refs/heads/main", "sea-ice-daily", "resume", True),
            ("refs/heads/other", "wdpa-monthly", "pause", False),
            ("refs/heads/main", "eamlis-monthly", "pause", False),
            ("refs/heads/main", "wdpa-monthly", "delete", False),
        ):
            with self.subTest(ref=ref, job=job, action=action):
                result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                        env={**os.environ, "GITHUB_REF": ref, "JOB_NAME": job, "SCHEDULE_ACTION": action})
                self.assertEqual(result.returncode == 0, allowed, result.stderr)

    def test_schedule_transitions_are_idempotent_and_reject_unknown_state(self):
        recorder = 'gcloud() { if [[ "$*" == *"value(state)"* ]]; then echo "$TEST_STATE"; else printf "%s\\n" "$*"; fi; }\n'
        script = recorder + self.steps["Set schedule state"]["run"]
        for action, state, mutation, allowed in (
            ("pause", "ENABLED", True, True), ("pause", "PAUSED", False, True),
            ("resume", "PAUSED", True, True), ("resume", "ENABLED", False, True),
            ("pause", "DISABLED", False, False),
        ):
            with self.subTest(action=action, state=state):
                result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                        env={**os.environ, "JOB_NAME": "wdpa-monthly", "SCHEDULE_ACTION": action, "TEST_STATE": state})
                self.assertEqual(result.returncode == 0, allowed, result.stderr)
                command = f"scheduler jobs {action} wdpa-monthly --location=us-central1 --project=shared-datasets-1"
                self.assertEqual(command in result.stdout.splitlines(), mutation)
