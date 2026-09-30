from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from ingestion.common import publication as p, reset_controls as controls
from ingestion.common.reset_installation import install_reset
from scripts import dataset_mutation_authorization as auth, reviewed_dataset_plan as plans
from scripts import install_feature_id_reset as cli
from scripts import feature_id_reset as prepare_cli
from test_identity_reset import fixture, intent_for, executor
from test_publication import LostResponse, publication_temp_directory
from test_dataset_mutation_authorization import FakeGitHub, context, EXECUTOR, review
from workflow_helpers import load_workflow, workflow_steps_by_name


AUTHORITY = {"proposal_key": "a" * 64, "execution_contract_sha256": "b" * 64}


def installation_fixture():
    store, candidate, ctx = fixture(bucket=controls.BUCKET)
    promotions = []
    for index, item in enumerate(candidate.review_envelope()["objects"]):
        uri = f"gs://{controls.BUCKET}/_scratch/pending-publishes/{ctx.asset_slug}/reset-test/{index}.json"
        version = store.write_json(uri, item["value"], 0)
        promotions.append({"source_uri": uri, "source_generation": str(version.generation),
                           "destination_uri": item["path"], "content_type": "application/json", "cache_control": "no-cache"})
    plan = plans.normalize_publish_plan({"asset_slug": ctx.asset_slug, "proposal_id": "reset-test", "promotions": promotions,
                                         "identity_reset": {"inventory": candidate.value}})
    store.events.clear()
    return store, candidate, ctx, plan


def run_install(store, plan, guard=lambda: None):
    return install_reset(store, plan, authorization=AUTHORITY, check_authority_and_jobs=guard)


class ResetInstallationTests(unittest.TestCase):
    def test_offline_preparation_exports_exact_installable_bytes(self):
        _store, candidate, _ctx, _plan = installation_fixture()
        with publication_temp_directory() as tmp:
            directory = Path(tmp)
            inventory = directory / "inventory.json"
            inventory.write_bytes(candidate.encoded)
            prepare_cli.main(["--inventory", str(inventory), "--output", str(directory / "review.json"),
                              "--objects-directory", str(directory / "objects")])
            for index, item in enumerate(candidate.review_envelope()["objects"]):
                self.assertEqual((directory / "objects" / f"{index}.json").read_bytes(), p.canonical(item["value"]))

    def test_installation_is_no_clobber_ordered_and_idempotent(self):
        store, candidate, ctx, plan = installation_fixture()
        before = {item["path"]: store.head(item["path"]) for item in candidate.value["latest_objects"]}
        guard = mock.Mock()
        result = run_install(store, plan, guard)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(store.read_json(ctx.state_uri).value, candidate.initial_state)
        self.assertEqual(store.events, [f"{candidate.root_uri}/publications/reset.json", candidate.evidence_uri, candidate.adoption_uri,
                                       f"{candidate.root_uri}/publications/reset.json", ctx.state_uri, f"{candidate.root_uri}/publications/reset.json"])
        self.assertEqual(guard.call_count, 7)  # admission + every mutation
        self.assertEqual(before, {path: store.head(path) for path in before})
        events = list(store.events)
        self.assertEqual(run_install(store, plan)["status"], "already_installed")
        self.assertEqual(store.events, events)

    def test_lost_response_after_every_durable_write_never_restarts_ids(self):
        for failure in range(1, 7):
            with self.subTest(failure=failure):
                store, candidate, ctx, plan = installation_fixture()
                store.fail_after = failure
                with self.assertRaises(LostResponse):
                    run_install(store, plan)
                store.fail_after = None
                if failure == 4:
                    before = list(store.events)
                    with self.assertRaisesRegex(p.PublicationError, "reviewed recovery required"):
                        run_install(store, plan)
                    self.assertEqual(store.events, before)
                    self.assertIsNone(store.head(ctx.state_uri))
                else:
                    run_install(store, plan)
                    self.assertEqual(store.read_json(ctx.state_uri).value, candidate.initial_state)
                    self.assertEqual(store.events.count(ctx.state_uri), 1)
                    self.assertEqual(store.events.count(candidate.evidence_uri), 1)

    def test_existing_advanced_or_missing_state_is_never_reinitialized(self):
        store, candidate, ctx, plan = installation_fixture()
        run_install(store, plan)
        intent = intent_for(store, candidate, ctx)
        executor(store).run(ctx, prepare=lambda: intent, local_sources={})
        state = store.read_json(ctx.state_uri)
        self.assertEqual(state.value["reserved_next_feature_id"], 3)
        self.assertEqual(run_install(store, plan)["status"], "already_installed")
        self.assertEqual(store.read_json(ctx.state_uri), state)
        del store.objects[ctx.state_uri]
        before = list(store.events)
        with self.assertRaisesRegex(p.PublicationError, "automatic reset is forbidden"):
            run_install(store, plan)
        self.assertEqual(store.events, before)

    def test_updated_executor_can_only_finalize_fully_written_original_reset(self):
        updated = {**AUTHORITY, "execution_contract_sha256": "c" * 64}
        for failure in range(1, 7):
            with self.subTest(failure=failure):
                store, candidate, ctx, plan = installation_fixture()
                store.fail_after = failure
                with self.assertRaises(LostResponse):
                    run_install(store, plan)
                store.fail_after = None
                before = list(store.events)
                state = store.read_json(ctx.state_uri)
                if failure < 5:
                    with self.assertRaises(p.PublicationError):
                        install_reset(store, plan, authorization=updated, check_authority_and_jobs=lambda: None)
                    self.assertEqual(store.events, before)
                else:
                    install_reset(store, plan, authorization=updated, check_authority_and_jobs=lambda: None)
                    self.assertEqual(store.read_json(ctx.state_uri), state)
                    marker_uri = f"{candidate.root_uri}/publications/reset.json"
                    self.assertEqual(store.events[len(before):], [marker_uri] if failure == 5 else [])
                    marker = store.read_json(marker_uri).value
                    self.assertEqual(marker["phase"], "complete")
                    self.assertEqual(marker["execution_contract_sha256"], AUTHORITY["execution_contract_sha256"])
                    self.assertEqual(marker["state_generation"], state.version.generation)

    def test_updated_executor_refuses_changed_or_foreign_reset_objects(self):
        for index in range(3):
            for change in ("missing", "bytes", "metadata"):
                with self.subTest(index=index, change=change):
                    store, candidate, _ctx, plan = installation_fixture()
                    store.fail_after = 5
                    with self.assertRaises(LostResponse):
                        run_install(store, plan)
                    store.fail_after = None
                    item = candidate.review_envelope()["objects"][index]
                    current = store.read_json(item["path"])
                    if change == "missing":
                        del store.objects[item["path"]]
                    else:
                        store.write_bytes(item["path"], b"changed" if change == "bytes" else p.canonical(item["value"]),
                                          current.version.generation,
                                          {} if change == "metadata" else dict(current.version.metadata),
                                          "application/json", "no-cache")
                    before = list(store.events)
                    with self.assertRaises(p.PublicationError):
                        install_reset(store, plan, authorization={**AUTHORITY, "execution_contract_sha256": "c" * 64},
                                      check_authority_and_jobs=lambda: None)
                    self.assertEqual(store.events, before)

    def test_competing_proposals_and_simultaneous_claims_cannot_overwrite(self):
        store, candidate, _ctx, plan = installation_fixture()
        store.fail_after = 1
        with self.assertRaises(LostResponse):
            run_install(store, plan)
        store.fail_after = None
        with self.assertRaisesRegex(p.PublicationError, "another reset installation"):
            install_reset(store, plan, authorization={**AUTHORITY, "proposal_key": "c" * 64}, check_authority_and_jobs=lambda: None)
        run_install(store, plan)

        store, candidate, ctx, plan = installation_fixture()
        # Both callers pass initial preflight; the competing caller wins CAS.
        store.before_write = lambda _uri: run_install(store, plan)
        with self.assertRaises(p.Conflict):
            run_install(store, plan)
        self.assertEqual(store.events.count(ctx.state_uri), 1)
        self.assertEqual(run_install(store, plan)["status"], "already_installed")

    def test_revocation_before_every_write_leaves_only_completed_prior_steps(self):
        for denied_check in range(1, 8):
            with self.subTest(denied_check=denied_check):
                store, _candidate, _ctx, plan = installation_fixture()
                count = 0
                def guard():
                    nonlocal count
                    count += 1
                    if count == denied_check:
                        raise p.PublicationError("approval revoked or live writer fence changed")
                with self.assertRaisesRegex(p.PublicationError, "revoked"):
                    run_install(store, plan, guard)
                self.assertEqual(len(store.events), max(0, denied_check - 2))

    def test_changed_inputs_inventory_run_record_and_prior_protocol_state_refuse(self):
        for change in ("source", "latest", "new_alias", "old_manifest", "new_release", "run_record", "existing_state", "prior_receipt"):
            with self.subTest(change=change):
                store, candidate, ctx, plan = installation_fixture()
                if change == "source":
                    source = plan["promotions"][0]
                    del store.history[(source["source_uri"], int(source["source_generation"]))]
                else:
                    path = {"latest": candidate.value["latest_objects"][0]["path"],
                            "new_alias": candidate.root_uri + "/latest/extra.json",
                            "old_manifest": candidate.value["baseline"]["release_manifest"]["path"],
                            "new_release": candidate.root_uri + "/releases/2026-10-01/extra.json",
                            "run_record": candidate.root_uri + "/runs/2026-10-01.json",
                            "existing_state": ctx.state_uri,
                            "prior_receipt": candidate.root_uri + "/publications/receipts/" + "d" * 64 + ".json"}[change]
                    old = store.head(path)
                    store.write_bytes(path, b"unexpected", old.generation if old else 0, {}, "application/json", "")
                store.events.clear()
                with self.assertRaises(p.PublicationError):
                    run_install(store, plan)
                self.assertEqual(store.events, [])

    def test_missing_or_changed_translation_supplement_refuses_before_installation(self):
        store, candidate, _ctx, plan = installation_fixture()
        supplement = candidate.value["translation_supplement"]
        del store.history[(supplement["path"], supplement["generation"])]
        before = list(store.events)
        with self.assertRaisesRegex(p.PublicationError, "generation/hash"):
            run_install(store, plan)
        self.assertEqual(store.events, before)

    def test_drift_after_full_hash_validation_blocks_activation(self):
        store, candidate, ctx, plan = installation_fixture()
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 6:
                anchor = candidate.value["latest_objects"][0]
                store.write_bytes(anchor["path"], b"changed", anchor["generation"], {}, "application/json", "")
        with self.assertRaisesRegex(p.PublicationError, "generation changed"):
            run_install(store, plan, guard)
        self.assertIsNone(store.head(ctx.state_uri))

    def test_runtime_cannot_publish_until_installation_is_complete(self):
        store, candidate, ctx, plan = installation_fixture()
        store.fail_after = 5
        with self.assertRaises(LostResponse):
            run_install(store, plan)
        store.fail_after = None
        intent = intent_for(store, candidate, ctx)
        with self.assertRaisesRegex(p.PublicationError, "incomplete"):
            executor(store).run(ctx, prepare=lambda: intent, local_sources={})
        run_install(store, plan)
        executor(store).run(ctx, prepare=lambda: intent, local_sources={})

    def test_reset_schema_rejects_replacements_other_targets_and_mixed_deletes(self):
        _store, _candidate, _ctx, plan = installation_fixture()
        mutations = [lambda value: value["promotions"].reverse(),
                     lambda value: value["promotions"][0].update(destination_generation="1"),
                     lambda value: value["promotions"].pop(),
                     lambda value: value["identity_reset"].update(writers_stopped=True),
                     lambda value: value["identity_reset"].update(fence_sha256=""),
                     lambda value: value.update(release_index_asset_slugs=[value["asset_slug"]])]
        for mutate in mutations:
            value = deepcopy(plan)
            mutate(value)
            with self.assertRaises(plans.PlanValidationError):
                plans.normalize_publish_plan(value)


class ResetAuthorizationTests(unittest.TestCase):
    def setUp(self):
        _store, _candidate, _ctx, plan = installation_fixture()
        self.api = FakeGitHub()
        self.api.doc = plans.normalize_document({"plan_version": 1, "finalization_version": plans.FINALIZATION_VERSION, "publish": plan})
        self.api.raw = plans.canonical_bytes(self.api.doc)
        self.api.blob = hashlib.sha1(f"blob {len(self.api.raw)}\0".encode() + self.api.raw).hexdigest()
        self.api.path = plans.document_path(self.api.doc)
        self.api.tree["tree"] = [{"path": self.api.path, "type": "blob", "mode": "100644", "sha": self.api.blob}]
        self.api.files = [{"filename": self.api.path, "status": "added", "sha": self.api.blob}]
        self.api.pr["body"] = plans.render_document(self.api.doc)
        event, self.env = context(self.api)
        self.env["GITHUB_ACTIONS"] = "true"
        self.envelope = auth.capture(self.api, event, self.env)

    def test_reset_uses_same_immutable_review_authority_and_distinct_routing(self):
        flags = auth.plan_outputs(self.api.doc)
        self.assertEqual(flags, {"has_identity_reset_plan": True, "has_publish_plan": False, "has_delete_plan": False})
        with publication_temp_directory() as tmp, mock.patch.object(auth.subprocess, "check_output", return_value=EXECUTOR + "\n"):
            directory = Path(tmp)
            auth.save_envelope(self.envelope, directory, None)
            digest = plans.sha256((directory / auth.ENVELOPE_FILE).read_bytes())
            self.assertEqual(cli.authorized_plan(directory, digest, api=self.api, env=self.env)[0], self.envelope)
            for field, value in (("GITHUB_ACTIONS", "false"), ("GITHUB_REF", "refs/heads/topic"), ("GITHUB_RUN_ID", "701"), ("GITHUB_WORKFLOW_REF", "other")):
                with self.subTest(field=field), self.assertRaises((p.PublicationError, plans.PlanValidationError)):
                    cli.authorized_plan(directory, digest, api=self.api, env={**self.env, field: value})
            self.api.reviews.append(review("DISMISSED", identifier=11))
            with self.assertRaisesRegex(plans.PlanValidationError, "APPROVED"):
                cli.authorized_plan(directory, digest, api=self.api, env=self.env)

    def test_protected_workflow_reuses_existing_identities_and_serializes_with_deployments(self):
        root = Path(__file__).resolve().parents[1]
        workflow = load_workflow(root / ".github/workflows/publish-dataset.yml")
        job = workflow["jobs"]["install-approved-identity-reset"]
        self.assertEqual(job["environment"], "shared-datasets-production")
        self.assertIn("has_identity_reset_plan", job["if"])
        self.assertFalse(job["concurrency"]["cancel-in-progress"])
        steps = workflow_steps_by_name(workflow, "install-approved-identity-reset")
        self.assertEqual(steps["Check out captured immutable executor"]["with"]["ref"], "${{ needs.reviewed_pr_plans.outputs.executor_sha }}")
        names = list(steps)
        self.assertLess(names.index("Verify reset authority"), names.index("Authenticate existing approved publisher"))
        self.assertEqual(steps["Authenticate existing approved publisher"]["with"]["service_account"], "${{ env.PUBLISHER_SERVICE_ACCOUNT }}")
        self.assertNotIn("--check-only", steps["Recheck live controls and install reset"]["run"])

        self.assertEqual(job["concurrency"]["group"], "prod-terraform-state")
        control = steps["Authenticate existing deployer for job checks"]
        self.assertEqual(control["with"]["service_account"], controls.DEPLOYER_ACCOUNT)
        self.assertFalse(control["with"]["export_environment_variables"])
        self.assertEqual(steps["Recheck live controls and install reset"]["env"]["RESET_CONTROL_CREDENTIALS"],
                         "${{ steps.reset_control_auth.outputs.credentials_file_path }}")

    def test_cli_uses_deployer_for_live_checks_and_publisher_for_every_object_write(self):
        store, _candidate, _ctx, plan = installation_fixture()
        import google.auth
        from google.auth.transport import requests
        from google.cloud import storage
        from ingestion.common import publication_gcs

        publisher = mock.Mock(service_account_email=controls.PUBLISHER_ACCOUNT)
        deployer = mock.Mock(service_account_email=controls.DEPLOYER_ACCOUNT)
        reader = mock.Mock()
        with mock.patch.object(cli, "authorized_plan", return_value=(self.envelope, plan)), \
             mock.patch.object(cli.auth, "revalidate") as review_check, \
             mock.patch.object(cli.auth, "identity_digests", return_value=AUTHORITY), \
             mock.patch.object(google.auth, "default", return_value=(publisher, controls.PROJECT)), \
             mock.patch.object(google.auth, "load_credentials_from_file", return_value=(deployer, controls.PROJECT)) as load_control, \
             mock.patch.object(requests, "AuthorizedSession") as session, \
             mock.patch.object(cli, "GoogleControlReader", return_value=reader), \
             mock.patch.object(storage, "Client") as client, \
             mock.patch.object(publication_gcs, "GcsStore", return_value=store), \
             mock.patch.dict("os.environ", {"RESET_CONTROL_CREDENTIALS": "/workflow/control.json"}):
            self.assertEqual(cli.main(["--directory", "authorization", "--expected-sha256", "a" * 64]), 0)
            load_control.assert_called_once_with("/workflow/control.json", scopes=["https://www.googleapis.com/auth/cloud-platform"])
            session.assert_called_once_with(deployer)
            client.assert_called_once_with(project=controls.PROJECT, credentials=publisher)
            self.assertEqual(review_check.call_count, 7)
            self.assertEqual(reader.check_quiescent.call_args_list, [mock.call(plan["asset_slug"])] * 7)
