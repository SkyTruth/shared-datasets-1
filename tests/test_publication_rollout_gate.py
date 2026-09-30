from pathlib import Path
import unittest

from ingestion.common import publication as p
from ingestion.common.reset_controls import ASSET_JOBS, BUCKET
from scripts import publication_rollout_gate as gate
from test_identity_reset import fixture, install_fixture
from test_publication import MemoryStore
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
            self.assertLess(names.index(gate_name), names.index(f"Build {job} image"))
            self.assertLess(names.index(gate_name), names.index("Terraform plan"))
