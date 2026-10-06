"""Cryptographic verifier boundary and signed deployment receipt policy."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import deployment_emission as e
from scripts import deployment_revision as d

REPO = "SkyTruth/shared-datasets-1"
SHA = "a" * 40
IMAGE = "image@sha256:" + "b" * 64
SIGNER = ".github/workflows/wdpa-monthly-deploy.yml"
ROOT = Path(__file__).resolve().parents[1]


def record():
    return {
        "id": 71, "sha": SHA, "ref": SHA, "environment": "production-wdpa-monthly",
        "creator": {"login": "github-actions[bot]", "type": "Bot"},
        "payload": {"schema": d.SCHEMA, "target": "wdpa-monthly", "artifact": IMAGE, "targets": ["module.wdpa_job"],
                    "ci_run_id": 13, "ci_run_attempt": 2, "execution_run_id": 13, "execution_run_attempt": 2},
    }


def status(phase="started", identifier=81, run_id=13, attempt=2):
    return {"id": identifier, "state": e.PHASES[phase], "description": phase, "environment_url": "",
            "log_url": e.invocation(REPO, run_id, attempt), "creator": {"login": "github-actions[bot]", "type": "Bot"}}


def run(identifier=13, attempt=2, path=".github/workflows/ci.yml", event="push"):
    return {"id": identifier, "run_attempt": attempt, "path": path, "workflow_id": 12,
            "head_sha": SHA, "head_branch": "main", "event": event,
            "repository": {"id": 17, "full_name": REPO}, "head_repository": {"id": 17, "full_name": REPO}}


def verified(value, emitter=None, signer=SIGNER):
    emitter = emitter or run()
    return [{"verificationResult": {
        "signature": {"certificate": e.policy(REPO, 17, value, emitter, signer)},
        "verifiedTimestamps": [{"type": "tlog", "timestamp": "2024-05-01T00:00:00Z"}],
        "statement": {"_type": "https://in-toto.io/Statement/v1", "predicateType": e.PREDICATE,
                      "subject": [{"name": e.name(value), "digest": {"sha256": hashlib.sha256(e.canonical(value)).hexdigest()}}]},
    }}]


class EmissionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = self.root / "outputs"
        self.output.touch()
        environment = patch.dict(e.os.environ, {"RUNNER_TEMP": str(self.root), "GITHUB_OUTPUT": str(self.output),
                                               "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "13", "GITHUB_RUN_ATTEMPT": "2"})
        environment.start()
        self.addCleanup(environment.stop)
        self.api = Mock()
        self.emitter = run()
        self.api.get.side_effect = self.route
        self.api.pages.side_effect = lambda path, field=None: [status()] if "/statuses?" in path else [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]

    def route(self, path):
        if path == "repos/" + REPO:
            return {"id": 17, "full_name": REPO}
        if "/actions/workflows/" in path:
            return {"id": 12, "path": self.emitter["path"]}
        return self.emitter

    def test_canonical_receipt_is_reconstructible_after_artifact_retention(self):
        claim, phase = record(), status()
        path = e.emit(REPO, claim, phase)
        original = path.read_bytes()
        path.unlink()  # No retained file or Actions artifact is required later.
        value = e.receipt(REPO, claim, phase)
        self.assertEqual(e.canonical(value), original)
        with patch.object(e.subprocess, "check_output", return_value=json.dumps(verified(value))) as verifier:
            e.verify(self.api, REPO, claim, phase, original_signer=SIGNER)
        command = verifier.call_args.args[0]
        self.assertEqual(command[:3], ["gh", "attestation", "verify"])
        for flag, expected in (("--repo", REPO), ("--signer-workflow", REPO + "/" + SIGNER), ("--signer-digest", SHA), ("--source-digest", SHA), ("--source-ref", "refs/heads/main")):
            self.assertEqual(command[command.index(flag) + 1], expected)
        self.assertIn("--deny-self-hosted-runners", command)
        self.assertNotIn("--custom-trusted-root", command)
        self.assertFalse(any("artifact" in part for part in command[:3]))

    def test_all_signed_certificate_identity_fields_are_enforced(self):
        value = e.receipt(REPO, record(), status())
        expected = e.policy(REPO, 17, value, run(), SIGNER)
        for field in expected:
            with self.subTest(field=field):
                result = verified(value)
                result[0]["verificationResult"]["signature"]["certificate"][field] = "attacker-controlled"
                with self.assertRaises(e.EmissionError):
                    e.verify_result(result, value, expected)
        e.verify_result(verified(value), value, expected)

    def test_valid_signed_receipt_for_other_record_target_status_or_bytes_cannot_replay(self):
        claim, phase = record(), status()
        signed = verified(e.receipt(REPO, claim, phase))
        for field, item in (("id", 72), ("sha", "c" * 40), ("environment", "production-eamlis-monthly")):
            changed = copy.deepcopy(claim)
            changed[field] = item
            value = e.receipt(REPO, changed, phase)
            with self.subTest(field=field), self.assertRaises(e.EmissionError):
                e.verify_result(signed, value, e.policy(REPO, 17, value, run(), SIGNER))
        for field, item in (("artifact", IMAGE.replace("b", "d")), ("target", "eamlis-monthly"), ("ci_run_attempt", 3), ("targets", ["unapproved.target"])):
            changed = copy.deepcopy(claim)
            changed["payload"][field] = item
            value = e.receipt(REPO, changed, phase)
            with self.subTest(field=field), self.assertRaises(e.EmissionError):
                e.verify_result(signed, value, e.policy(REPO, 17, value, run(), SIGNER))
        for changed in (status(identifier=82), status("verified")):
            value = e.receipt(REPO, claim, changed)
            with self.subTest(status=changed), self.assertRaises(e.EmissionError):
                e.verify_result(signed, value, e.policy(REPO, 17, value, run(), SIGNER))

    def test_pr_signature_cannot_copy_real_main_run_metadata(self):
        claim, phase = record(), status()
        result = verified(e.receipt(REPO, claim, phase))
        cert = result[0]["verificationResult"]["signature"]["certificate"]
        cert.update(sourceRepositoryRef="refs/pull/10/merge", buildTrigger="pull_request",
                    runInvocationURI=e.invocation(REPO, 99, 1), buildConfigURI="https://github.com/" + REPO + "/.github/workflows/ci.yml@refs/pull/10/merge")
        with patch.object(e.subprocess, "check_output", return_value=json.dumps(result)), self.assertRaises(d.DeploymentError):
            d.verify_record(self.api, REPO, claim)
        self.api.post.assert_not_called()

    def test_unsigned_or_bad_signature_never_satisfies_metadata_matching(self):
        failure = subprocess.CalledProcessError(1, ["gh", "attestation", "verify"])
        with patch.object(e.subprocess, "check_output", side_effect=failure), self.assertRaises(subprocess.CalledProcessError):
            d.verify_record(self.api, REPO, record())
        value = e.receipt(REPO, record(), status())
        with self.assertRaises(e.EmissionError):
            e.verify_result([], value, e.policy(REPO, 17, value, run(), SIGNER))
        self.api.post.assert_not_called()

    def test_observer_and_recovery_updates_require_their_own_exact_signed_runs(self):
        claim = record()
        for path, event in ((e.OBSERVER, "schedule"), (e.RECOVERY, "workflow_dispatch")):
            self.emitter = run(19, 1, path, event)
            phase = status("verified", run_id=19, attempt=1)
            value = e.receipt(REPO, claim, phase)
            with self.subTest(path=path), patch.object(e.subprocess, "check_output", return_value=json.dumps(verified(value, self.emitter, path))):
                e.verify(self.api, REPO, claim, phase, original_signer=SIGNER)
            wrong = verified(value, self.emitter, SIGNER)
            with patch.object(e.subprocess, "check_output", return_value=json.dumps(wrong)), self.assertRaises(e.EmissionError):
                e.verify(self.api, REPO, claim, phase, original_signer=SIGNER)
        self.emitter = run(19, 1, ".github/workflows/unrelated.yml", "workflow_dispatch")
        with self.assertRaises(e.EmissionError):
            e.verify(self.api, REPO, claim, status("verified", run_id=19, attempt=1), original_signer=SIGNER)

    def test_terraform_records_use_generic_leaf_signer_instead_of_wrapper(self):
        claim = record()
        target = "terraform-" + hashlib.sha256(b"Artifact Registry writer binding sync").hexdigest()[:16]
        claim["payload"]["target"] = target
        claim["environment"] = "production-" + target
        value = e.receipt(REPO, claim, status())
        signer = ".github/workflows/prod-terraform-target-apply.yml"
        with patch.object(e.subprocess, "check_output", return_value=json.dumps(verified(value, signer=signer))):
            d.verify_record(self.api, REPO, claim)

    def test_unknown_terminal_outcome_emits_receipt_before_failing(self):
        claim = record()
        self.api.get.return_value = claim
        self.api.get.side_effect = None
        self.api.post.return_value = {"id": 82}
        args = SimpleNamespace(deployment_id=71, phase="unknown", execution="")
        with patch.object(d, "verify_record_identity", return_value=claim["payload"]), self.assertRaisesRegex(d.DeploymentError, "terminal runtime evidence"):
            d.finish(args, self.api)
        path = Path(self.output.read_text().strip().split("=", 1)[1])
        value = json.loads(path.read_text())
        self.assertEqual(value["status"]["description"], "unknown")
        self.assertEqual(value["status"]["id"], 82)

    def test_protected_signed_failure_can_prove_claim_ownership_without_unsigned_start(self):
        claim, failed = record(), status("failed", identifier=82)
        self.api.pages.side_effect = lambda path, field=None: [failed, status()] if "/statuses?" in path else [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
        with patch.object(e.subprocess, "check_output", return_value=json.dumps(verified(e.receipt(REPO, claim, failed)))):
            d.verify_record(self.api, REPO, claim)
        with self.assertRaisesRegex(d.DeploymentError, "incomplete or failed"):
            d.replay_decision([claim], SHA, lambda older, newer: True, lambda candidate: [failed])
        self.api.post.assert_not_called()

    def test_start_requires_current_run_signed_rehearsal_before_creating_record(self):
        args = SimpleNamespace(target="wdpa-monthly", artifact=IMAGE, plan_json=None, executor_sha=SHA, source_run_id=13, source_run_attempt=2)
        with patch.object(d, "check", return_value={"proceed": "true"}), patch.object(e, "verify_rehearsal", side_effect=e.EmissionError("signature unavailable")), self.assertRaises(d.DeploymentError):
            d.start(args, self.api)
        self.api.post.assert_not_called()
        with patch.object(d, "check", return_value={"proceed": "false"}), patch.object(e, "verify_rehearsal") as rehearse:
            self.assertEqual(d.start(args, self.api), {"proceed": "false"})
        rehearse.assert_not_called()

    def test_old_signed_claim_preserves_replay_barrier_and_cannot_be_copied(self):
        claim, phase = record(), status()
        claim["created_at"] = "2024-05-01T00:00:00Z"
        value = e.receipt(REPO, claim, phase)
        signed = verified(value)
        with patch.object(e.subprocess, "check_output", return_value=json.dumps(signed)):
            d.verify_record(self.api, REPO, claim)
        copied = copy.deepcopy(claim)
        copied["id"] += 1
        with patch.object(e.subprocess, "check_output", return_value=json.dumps(signed)), self.assertRaises(d.DeploymentError):
            d.verify_record(self.api, REPO, copied)

    def test_standalone_entrypoints_clear_pythonpath_and_fail_bad_crypto(self):
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        help_result = subprocess.run([sys.executable, str(ROOT / "scripts/deployment_revision.py"), "--help"], cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        claim, phase = record(), status()
        receipt_path = self.root / e.name(e.receipt(REPO, claim, phase))
        receipt_path.write_bytes(e.canonical(e.receipt(REPO, claim, phase)))
        routes = {
            "repos/" + REPO: {"id": 17, "full_name": REPO},
            f"repos/{REPO}/deployments/71": claim,
            f"repos/{REPO}/deployments/71/statuses?per_page=100": [[phase]],
            f"repos/{REPO}/actions/workflows/ci.yml": {"id": 12, "path": ".github/workflows/ci.yml"},
            f"repos/{REPO}/actions/runs/13/attempts/2": run(),
            f"repos/{REPO}/actions/runs/13/attempts/2/jobs?per_page=100": [{"total_count": 1, "jobs": [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]}],
        }
        binary = self.root / "bin/gh"
        binary.parent.mkdir()
        log = self.root / "verifier-invocations"
        binary.write_text(
            "#!" + sys.executable + "\nimport json, pathlib, sys\n"
            + f"routes={routes!r}\nlog=pathlib.Path({str(log)!r})\n"
            + "args=sys.argv[1:]\nwith log.open('a') as output: output.write(json.dumps(args)+'\\n')\n"
            + "if args[:2] == ['attestation', 'verify']:\n print('cryptographic verification failed', file=sys.stderr)\n sys.exit(7)\n"
            + "if args[0] != 'api': raise RuntimeError('unexpected command')\nprint(json.dumps(routes[args[-1]]))\n"
        )
        binary.chmod(0o755)
        environment["PATH"] = str(binary.parent) + os.pathsep + environment["PATH"]
        result = subprocess.run([sys.executable, str(ROOT / "scripts/deployment_emission.py"), "verify-file", "--receipt", str(receipt_path)], cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cryptographic verification failed", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        commands = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(commands[-1][:2], ["attestation", "verify"])
        self.assertFalse(any("POST" in command for command in commands))

    def test_wrong_phase_state_missing_timestamp_and_unverified_json_fail(self):
        with self.assertRaises(e.EmissionError):
            e.receipt(REPO, record(), {**status("verified"), "state": "failure"})
        value = e.receipt(REPO, record(), status())
        result = verified(value)
        result[0]["verificationResult"]["verifiedTimestamps"] = []
        with self.assertRaises(e.EmissionError):
            e.verify_result(result, value, e.policy(REPO, 17, value, run(), SIGNER))
        with self.assertRaises(e.EmissionError):
            e.verify_result([{"rawBundle": result}], value, e.policy(REPO, 17, value, run(), SIGNER))
