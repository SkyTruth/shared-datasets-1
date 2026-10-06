"""Standalone non-mutating receipt bootstrap and verifier installation contracts."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml
from scripts import deployment_emission as emission
from scripts import install_deployment_verifier as installer

ROOT = Path(__file__).resolve().parents[1]
REPO = "SkyTruth/shared-datasets-1"


class ReceiptBootstrapTests(unittest.TestCase):
    def test_real_current_cli_verified_certificate_shape_matches_original_api_run(self):
        # This is a shape regression, not a replacement for cryptographic
        # verification. The checked official archive was actually verified
        # with gh2.96 on 2026-10-06, 96 days after its signed Rekor timestamp.
        value = json.loads((ROOT / "tests/fixtures/deployment-attestation/cli-2.96-verified-shape.json").read_text())
        cert, run = value["certificate"], value["api_run"]
        source = "https://github.com/cli/cli"
        self.assertTrue(value["verifiedTimestamps"])
        for field, expected in {
            "issuer": "https://token.actions.githubusercontent.com", "runnerEnvironment": "github-hosted",
            "sourceRepositoryURI": source, "sourceRepositoryIdentifier": str(run["repository"]["id"]),
            "sourceRepositoryRef": "refs/heads/trunk", "sourceRepositoryDigest": run["head_sha"],
            "buildSignerURI": source + "/" + run["path"] + "@refs/heads/trunk",
            "buildSignerDigest": run["head_sha"], "buildConfigURI": source + "/" + run["path"] + "@refs/heads/trunk",
            "buildConfigDigest": run["head_sha"], "buildTrigger": run["event"],
            "runInvocationURI": emission.invocation("cli/cli", run["id"], run["run_attempt"]),
        }.items():
            with self.subTest(field=field):
                self.assertEqual(cert[field], expected)

    def test_rehearsal_does_not_need_deployment_writer_and_rejects_pr_ref(self):
        api = Mock(spec=["get"])
        api.get.return_value = {"id": 13, "run_attempt": 2, "head_sha": "a" * 40}
        environment = {"GITHUB_RUN_ID": "13", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_REF": "refs/heads/main"}
        with patch.dict(os.environ, environment):
            record, status = emission.rehearsal(api, REPO)
            self.assertEqual(record["environment"], "receipt-rehearsal")
            self.assertNotIn("ci_run_id", record["payload"])
            self.assertEqual(status["log_url"], emission.invocation(REPO, 13, 2))
            with patch.dict(os.environ, {"GITHUB_REF": "refs/pull/1/merge"}), self.assertRaises(emission.EmissionError):
                emission.rehearsal(api, REPO)
        self.assertFalse(hasattr(emission.ReadOnlyGitHub(), "post"))

    def test_unknown_leaf_cannot_be_rehearsed(self):
        with patch("sys.argv", ["deployment_emission.py", "verify-rehearsal", "--signer-workflow", "unknown.yml"]), patch.dict(os.environ, {"GITHUB_REPOSITORY": REPO}), self.assertRaisesRegex(emission.EmissionError, "unknown rehearsal signer"):
            emission.main()

    def test_composite_pins_attestor_and_verifier_and_has_no_mutation_client(self):
        value = yaml.safe_load((ROOT / ".github/actions/deployment-receipt/action.yml").read_text())
        steps = value["runs"]["steps"]
        signer = next(step for step in steps if "uses" in step)
        self.assertEqual(signer["uses"], "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6")
        self.assertIs(signer["with"]["create-storage-record"], False)
        runs = "\n".join(step.get("run", "") for step in steps)
        self.assertIn("prepare-rehearsal", runs)
        self.assertIn("verify-rehearsal", runs)
        self.assertIn("verify-file", runs)
        self.assertIn("install_deployment_verifier.py", runs)
        self.assertNotIn("deployment_revision.py", runs)
        self.assertNotIn("--method POST", runs)


class VerifierInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "github-path"
        self.path.touch()
        self.env = {"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "RUNNER_TEMP": str(self.root), "GITHUB_PATH": str(self.path)}
        environment = patch.dict(os.environ, self.env)
        environment.start()
        self.addCleanup(environment.stop)

    def archive(self, member=None, kind=tarfile.REGTYPE):
        target = self.root / "deployment-receipts/tools" / installer.ASSET
        target.parent.mkdir(parents=True)
        with tarfile.open(target, "w:gz") as bundle:
            entry = tarfile.TarInfo(member or f"gh_{installer.VERSION}_linux_amd64/bin/gh")
            entry.type = kind
            entry.size = 6 if kind == tarfile.REGTYPE else 0
            bundle.addfile(entry, io.BytesIO(b"binary") if entry.size else None)
        return target

    def test_archive_checksum_tamper_fails_before_extraction_or_execution(self):
        self.archive()
        with patch.object(installer.subprocess, "check_output") as execute, self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            installer.install()
        execute.assert_not_called()
        self.assertEqual(self.path.read_text(), "")

    def test_only_checked_expected_regular_binary_is_installed(self):
        archive = self.archive()
        digest = installer.hashlib.sha256(archive.read_bytes()).hexdigest()
        with patch.object(installer, "SHA256", digest), patch.object(installer.subprocess, "check_output", return_value="gh version 2.96.0 (2026-07-02)\n"):
            installer.install()
        binary = self.root / "deployment-receipts/tools/bin/gh"
        self.assertEqual(binary.read_bytes(), b"binary")
        self.assertEqual(self.path.read_text(), str(binary.parent) + "\n")

    def test_unexpected_runner_symlink_and_version_fail(self):
        with patch.dict(os.environ, {"RUNNER_ARCH": "ARM64"}), self.assertRaisesRegex(RuntimeError, "Linux x64"):
            installer.install()
        archive = self.archive(kind=tarfile.SYMTYPE)
        digest = installer.hashlib.sha256(archive.read_bytes()).hexdigest()
        with patch.object(installer, "SHA256", digest), self.assertRaisesRegex(RuntimeError, "regular gh binary"):
            installer.install()
