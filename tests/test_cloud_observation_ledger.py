"""Durable cloud cycles use actual serialization and verified immutable parts."""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import zlib

import pytest

from scripts import cloud_observation_ledger as cycles
from scripts import cloud_operational_observations as cloud
from scripts import deployment_emission as emissions


SHA = "a" * 40
RUN = {"id": 100, "run_attempt": 1, "head_sha": SHA, "head_branch": "main", "path": cycles.WORKFLOW,
       "event": "schedule", "repository": {"full_name": cycles.REPOSITORY}, "head_repository": {"full_name": cycles.REPOSITORY}}


def bundle():
    checkpoint = {"schema": cloud.SCHEMA, "covered_through": "2026-10-21T12:00:00.000000Z", "seen": {}, "pending": [], "watching": {},
                  "last_cycle": {"start": "2026-10-21T11:50:00.000000Z", "end": "2026-10-21T12:00:00.000000Z",
                                 "completed_at": "2026-10-21T12:02:00.000000Z", "new_events": 0, "views": list(cloud.VIEWS),
                                 "coverage_gaps": [], "overlap_seconds": 600, "ingestion_lag_seconds": 120}}
    return checkpoint, {"complete": True, "provenance_complete": True, "public_delivery_count": 0, "decisions": []}


class API:
    def __init__(self):
        self.records, self.statuses, self.signatures, self.writes = {}, {}, {}, []
        self.fail_task = self.fail_status_task = None
        self.duplicate = False
        self.enumeration_failure = False
        self.runs = {(RUN["id"], RUN["run_attempt"]): deepcopy(RUN)}

    def get(self, path):
        if "/actions/runs/" in path:
            run_id, _, attempt = path.split("/actions/runs/")[1].split("/")
            return deepcopy(self.runs[int(run_id), int(attempt)])
        return deepcopy(self.records[int(path.rsplit("/", 1)[-1])])

    def pages(self, path):
        if self.enumeration_failure:
            raise subprocess.CalledProcessError(1, ["gh", "api"], output=b"partial enumeration")
        if "/statuses?" in path:
            return deepcopy(self.statuses.get(int(path.split("/deployments/")[1].split("/")[0]), []))
        assert "environment=operational-observation-cycle" in path and "task=operational-observation-cycle" in path and "per_page=100" in path
        records = [deepcopy(record) for record in self.records.values() if record["task"] == cycles.TASK]
        return records + records[:1] if self.duplicate else records

    def post(self, path, payload):
        # Preserve the real JSON boundary, including str payloads returned by
        # GitHub's deployment API. No mock replaces hashing or decompression.
        payload = json.loads(cycles.canonical(payload))
        self.writes.append((path, deepcopy(payload)))
        if path.endswith("/deployments"):
            if payload["task"] == self.fail_task:
                raise RuntimeError("record persistence failed")
            identifier = 1000 + len(self.records)
            record = {**payload, "id": identifier, "sha": payload["ref"], "creator": {"login": "github-actions[bot]"}}
            record["payload"] = json.dumps(payload["payload"])
            self.records[identifier] = deepcopy(record)
            return record
        identifier = int(path.split("/deployments/")[1].split("/")[0])
        if self.records[identifier]["task"] == self.fail_status_task:
            raise RuntimeError("status persistence failed")
        status = {**payload, "id": 10000 + identifier}
        self.statuses.setdefault(identifier, []).append(deepcopy(status))
        return status

    def sign(self):
        for identifier, record in self.records.items():
            if len(self.statuses.get(identifier, [])) == 1:
                self.signatures[identifier] = cycles.digest(emissions.canonical(emissions.receipt(cycles.REPOSITORY, record, self.statuses[identifier][0])))

    def verify_signature(self, api, repository, record, status, *, original_signer, batch):
        assert api is self and repository == cycles.REPOSITORY and original_signer == cycles.WORKFLOW and batch is True
        actual = cycles.digest(emissions.canonical(emissions.receipt(repository, record, status)))
        if self.signatures.get(record["id"]) != actual:
            raise emissions.EmissionError("signature rejected")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    root = tmp_path / "protocol"
    for relative in cycles.CONTRACT_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("jobs: {}\n" if relative.endswith((".yml", ".yaml")) else "checked in protocol: " + relative + "\n")
    api = API()
    monkeypatch.setattr(emissions, "verify", api.verify_signature)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    return cycles.CycleLedger(api, deepcopy(RUN), tmp_path / "work", root=root)


def saved(ledger, **kwargs):
    checkpoint, evaluation = bundle()
    result = ledger.save(checkpoint, evaluation, "shadow", **kwargs)
    ledger.api.sign()
    return result, checkpoint, evaluation


def manifest_record(ledger, identifier):
    record = ledger.api.records[identifier]
    return record, json.loads(record["payload"])


def replace_payload(ledger, identifier, value):
    ledger.api.records[identifier]["payload"] = json.dumps(value)


def test_save_then_fresh_load_round_trips_complete_bundle_and_exact_receipts(ledger):
    proposal, checkpoint, evaluation = saved(ledger)
    assert set(proposal) == {"manifest_id", "manifest"}
    assert ledger.local_cycle_id() == proposal["manifest_id"]
    assert json.loads((ledger.workdir / "cycle.json").read_text()) == {"manifest_id": proposal["manifest_id"]}
    fresh = cycles.CycleLedger(ledger.api, None, ledger.workdir, root=ledger.root)
    verified = fresh.load()
    assert verified == {**proposal, "checkpoint": checkpoint, "evaluation": evaluation}
    receipts = list((ledger.workdir / "claims").glob("*.json"))
    assert len(receipts) == len(proposal["manifest"]["parts"]) + 1
    for path in receipts:
        receipt = json.loads(path.read_text())
        assert receipt == emissions.receipt(cycles.REPOSITORY, ledger.api.records[receipt["deployment_id"]], ledger.api.statuses[receipt["deployment_id"]][0])
    assert "claims=true" in Path(os.environ["GITHUB_OUTPUT"]).read_text()
    for path, value in ledger.api.writes:
        if path.endswith("/deployments"):
            assert value["auto_merge"] is value["production_environment"] is value["transient_environment"] is False
            assert value["required_contexts"] == []
        else:
            assert value["auto_inactive"] is False


def test_empty_registry_and_duplicate_complete_pages_do_not_invent_cycles(ledger):
    assert ledger.load() is None
    expected, _, _ = saved(ledger)
    ledger.api.duplicate = True
    assert ledger.load()["manifest_id"] == expected["manifest_id"]


def test_partial_pagination_is_a_failure_and_never_reuses_an_old_cursor(ledger):
    saved(ledger)
    ledger.api.enumeration_failure = True
    with pytest.raises(subprocess.CalledProcessError):
        ledger.load()


def test_latest_unsigned_cycle_blocks_instead_of_skipping_to_older_signature(ledger):
    first, _, _ = saved(ledger)
    later = ledger.save(*bundle(), "shadow", previous_cycle_id=first["manifest_id"])
    with pytest.raises(emissions.EmissionError, match="signature"):
        ledger.load()
    assert later["manifest_id"] > first["manifest_id"]
    assert ledger.verify_cycle_id(first["manifest_id"])["manifest_id"] == first["manifest_id"]


@pytest.mark.parametrize("where", ["part-record", "part-status", "manifest-record", "manifest-status"])
def test_interrupted_persistence_never_publishes_a_complete_new_cursor(ledger, where):
    first, _, _ = saved(ledger)
    if where.endswith("record"):
        ledger.api.fail_task = cycles.PART_TASK if where.startswith("part") else cycles.TASK
    else:
        ledger.api.fail_status_task = cycles.PART_TASK if where.startswith("part") else cycles.TASK
    with pytest.raises(RuntimeError):
        ledger.save(*bundle(), "shadow", previous_cycle_id=first["manifest_id"])
    assert ledger.local_cycle_id() == first["manifest_id"]
    if where == "manifest-status":
        with pytest.raises(cycles.CycleLedgerError, match="incomplete"):
            ledger.load()
    else:
        assert ledger.load()["manifest_id"] == first["manifest_id"]


@pytest.mark.parametrize("change", ["schema", "worker", "nonce", "index", "reference", "digest", "status", "foreign-task", "duplicate-status"])
def test_even_signed_parts_must_match_the_exact_manifest_identity(ledger, change):
    proposal, _, _ = saved(ledger)
    manifest_id = proposal["manifest_id"]
    record, manifest = manifest_record(ledger, manifest_id)
    part_id = manifest["parts"][0]["id"]
    part_record, part = manifest_record(ledger, part_id)
    if change == "schema":
        part["schema"] = "unknown"
    elif change == "worker":
        part["execution_run_id"] += 1
        ledger.api.runs[part["execution_run_id"], 1] = {**RUN, "id": part["execution_run_id"]}
        ledger.api.statuses[part_id][0]["log_url"] = emissions.invocation(cycles.REPOSITORY, part["execution_run_id"], 1)
    elif change == "nonce":
        part["cycle_nonce"] = "f" * 32
    elif change == "index":
        part["index"] = 1
    elif change == "reference":
        manifest["parts"][0]["id"] = manifest_id
    elif change == "digest":
        manifest["parts"][0]["sha256"] = "0" * 64
    elif change == "status":
        ledger.api.statuses[part_id][0]["state"] = "failure"
        ledger.api.statuses[part_id][0]["description"] = "failed"
    elif change == "foreign-task":
        part_record["task"] = "other"
    else:
        ledger.api.statuses[part_id].append(deepcopy(ledger.api.statuses[part_id][0]))
    replace_payload(ledger, manifest_id, manifest)
    replace_payload(ledger, part_id, part)
    ledger.api.sign()
    with pytest.raises(cycles.CycleLedgerError):
        ledger.verify_cycle_id(manifest_id)


def test_mutated_body_or_invalid_signature_never_becomes_checkpoint_evidence(ledger):
    proposal, _, _ = saved(ledger)
    record, manifest = manifest_record(ledger, proposal["manifest_id"])
    manifest["body_sha256"] = "0" * 64
    replace_payload(ledger, record["id"], manifest)
    with pytest.raises(emissions.EmissionError):
        ledger.verify_cycle_id(record["id"])
    ledger.api.sign()
    with pytest.raises(cycles.CycleLedgerError, match="body digest"):
        ledger.verify_cycle_id(record["id"])


def test_large_cycle_is_chunked_and_reconstructed_without_direct_payload(ledger, monkeypatch):
    monkeypatch.setattr(cycles, "CHUNK_BYTES", 128)
    checkpoint, evaluation = bundle()
    evaluation["proofs"] = [cycles.digest(str(index).encode()) for index in range(100)]
    proposal = ledger.save(checkpoint, evaluation, "shadow")
    ledger.api.sign()
    assert len(proposal["manifest"]["parts"]) > 1
    assert all(len(json.loads(record["payload"]).get("data", "")) <= 128 for record in ledger.api.records.values())
    assert "checkpoint" not in proposal["manifest"]
    assert ledger.load()["evaluation"] == evaluation


def test_duplicate_part_references_are_rejected_before_decoding(ledger, monkeypatch):
    monkeypatch.setattr(cycles, "CHUNK_BYTES", 128)
    proposal, _, _ = saved(ledger)
    record, manifest = manifest_record(ledger, proposal["manifest_id"])
    assert len(manifest["parts"]) > 1
    manifest["parts"][1]["id"] = manifest["parts"][0]["id"]
    replace_payload(ledger, record["id"], manifest)
    ledger.api.sign()
    with pytest.raises(cycles.CycleLedgerError, match="duplicate"):
        ledger.load()


def test_signed_decode_bomb_is_bounded_before_json_parsing(ledger, monkeypatch):
    proposal, _, _ = saved(ledger)
    record, manifest = manifest_record(ledger, proposal["manifest_id"])
    part_id = manifest["parts"][0]["id"]
    _, part = manifest_record(ledger, part_id)
    raw = b"x" * 100_000
    compressed = zlib.compress(raw)
    part["data"] = base64.b64encode(compressed).decode()
    part["sha256"] = cycles.digest(part["data"].encode())
    manifest.update(body_bytes=1000, body_sha256=cycles.digest(raw), compressed_bytes=len(compressed), compressed_sha256=cycles.digest(compressed))
    manifest["parts"][0]["sha256"] = part["sha256"]
    replace_payload(ledger, record["id"], manifest)
    replace_payload(ledger, part_id, part)
    ledger.api.sign()
    monkeypatch.setattr(cycles, "MAX_BODY_BYTES", 2000)
    with pytest.raises(cycles.CycleLedgerError, match="decompression"):
        ledger.load()


def test_historical_cycle_keeps_original_worker_and_heartbeat_timestamp(ledger):
    first, checkpoint, _ = saved(ledger)
    ledger.run = {**RUN, "id": 101, "head_sha": "b" * 40}
    ledger.api.runs[101, 1] = deepcopy(ledger.run)
    current, evaluation = bundle()
    current["covered_through"] = current["last_cycle"]["end"] = "2026-10-21T12:05:00.000000Z"
    current["last_cycle"]["completed_at"] = "2026-10-21T12:07:00.000000Z"
    later = ledger.save(current, evaluation, "shadow", previous_cycle_id=first["manifest_id"])
    ledger.api.sign()
    old = ledger.verify_cycle_id(first["manifest_id"])
    points = []
    old_bundle = {key: old[key] for key in ("checkpoint", "evaluation")}
    assert cloud.confirm_heartbeat(old_bundle, old_bundle, delivery_verified=True, heartbeat=points.append)
    assert points == [cloud.instant(checkpoint["last_cycle"]["completed_at"])]
    assert old["manifest"]["source_sha"] == SHA and old["manifest"]["execution_run_id"] == 100
    assert ledger.load()["manifest_id"] == later["manifest_id"]


def test_contract_hash_changes_only_when_an_explicit_protocol_file_changes(ledger):
    original = cycles.contract_hash(ledger.root)
    (ledger.root / "unrelated.md").write_text("unrelated main commit")
    assert cycles.contract_hash(ledger.root) == original
    (ledger.root / "scripts/cloud_operational_observations.py").write_text("changed protocol")
    assert cycles.contract_hash(ledger.root) != original
    (ledger.root / cycles.POLICY_FILE).unlink()
    with pytest.raises(FileNotFoundError):
        cycles.contract_hash(ledger.root)


def test_contract_hash_follows_registered_reusable_workflow_graph_and_refuses_missing_children(ledger):
    caller = ledger.root / ".github/workflows/ci.yml"
    caller.write_text("jobs:\n  source:\n    uses: ./.github/workflows/source-proof.yml\n")
    with pytest.raises(FileNotFoundError):
        cycles.contract_hash(ledger.root)
    child = caller.with_name("source-proof.yml")
    child.write_text("jobs: {}\n")
    initial = cycles.contract_hash(ledger.root)
    child.write_text("jobs:\n  validated:\n    runs-on: ubuntu-latest\n")
    assert cycles.contract_hash(ledger.root) != initial


@pytest.mark.parametrize("change", ["incomplete", "missing-provenance", "delivery", "partial-views", "wrong-time", "wrong-hash", "wrong-worker"])
def test_invalid_cycle_fails_before_any_durable_write(ledger, change):
    checkpoint, evaluation = bundle()
    kwargs = {}
    if change == "incomplete":
        evaluation["complete"] = False
    elif change == "missing-provenance":
        del evaluation["provenance_complete"]
    elif change == "delivery":
        evaluation["public_delivery_count"] = 1
    elif change == "partial-views":
        checkpoint["last_cycle"]["views"] = []
    elif change == "wrong-time":
        checkpoint["last_cycle"]["completed_at"] = "2026-10-20T12:00:00Z"
    elif change == "wrong-hash":
        kwargs["collector_contract_sha256"] = "0" * 64
    else:
        kwargs["source_sha"] = "c" * 40
    with pytest.raises((cycles.CycleLedgerError, ValueError)):
        ledger.save(checkpoint, evaluation, "shadow", **kwargs)
    assert ledger.api.writes == []


def test_read_only_gate_can_verify_but_cannot_write_a_cycle(ledger):
    proposal, _, _ = saved(ledger)
    readonly = cycles.CycleLedger(ledger.api, None, ledger.workdir, root=ledger.root)
    assert readonly.verify_cycle_id(proposal["manifest_id"])["manifest_id"] == proposal["manifest_id"]
    with pytest.raises(cycles.CycleLedgerError, match="authenticated main"):
        readonly.save(*bundle(), "shadow")


def test_valid_signature_cannot_claim_another_worker_revision(ledger):
    proposal, _, _ = saved(ledger)
    ledger.api.runs[100, 1]["head_sha"] = "b" * 40
    with pytest.raises(cycles.CycleLedgerError, match="authoritative worker revision"):
        ledger.verify_cycle_id(proposal["manifest_id"])


@pytest.mark.parametrize("change", ["missing-predecessor", "wrong-predecessor", "coverage-rewind", "completion-rewind", "unsigned-latest"])
def test_predecessor_is_verified_and_monotonic_before_persistence(ledger, change):
    first, _, _ = saved(ledger)
    checkpoint, evaluation = bundle()
    predecessor = first["manifest_id"]
    if change == "missing-predecessor":
        predecessor = None
    elif change == "wrong-predecessor":
        predecessor += 1
    elif change == "coverage-rewind":
        checkpoint["covered_through"] = checkpoint["last_cycle"]["end"] = "2026-10-21T11:59:00.000000Z"
    elif change == "completion-rewind":
        checkpoint["last_cycle"]["completed_at"] = "2026-10-21T12:01:00.000000Z"
    else:
        ledger.save(checkpoint, evaluation, "shadow", previous_cycle_id=predecessor)
    written = len(ledger.api.writes)
    with pytest.raises((cycles.CycleLedgerError, emissions.EmissionError)):
        ledger.save(checkpoint, evaluation, "shadow", previous_cycle_id=predecessor)
    assert len(ledger.api.writes) == written
