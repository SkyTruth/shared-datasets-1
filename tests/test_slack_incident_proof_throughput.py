"""Parallel proof reads remain complete, bounded and fail closed before use."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from threading import Condition, Event, Lock

import pytest

from scripts import deployment_emission as emissions, deployment_revision as deployments
from scripts import operational_retry as retry, slack_incidents as incidents
from test_slack_incidents import IDENTITY, SHA, observation, signed_record


class ProofTransport:
    """Offline gh boundary, including pagination, receipt files and CLI policy."""

    def __init__(self, count):
        targets = sorted((set(deployments.TARGET_WORKFLOWS) | set(deployments.TERRAFORM_WORKFLOWS))
                         - {"typescript-sdk"})[:count]
        assert len(targets) == count
        self.records = []
        self.statuses, self.runs = {}, {}
        self.repo = {"id": 1, "full_name": incidents.REPOSITORY}
        self.workflow = {"id": 2, "path": incidents.WORKFLOW}
        for index, target in enumerate(targets):
            record = signed_record()
            record["id"] = index + 10
            state = incidents.fold(None, observation(scope="deployment:" + target, label=target), IDENTITY)
            state["incident"]["posts"]["parent"] = {"state": "claimed", "ts": None}
            record["payload"].update(state=state, execution_run_id=index + 100)
            self.records.append(record)
            self.statuses[record["id"]] = [{"id": index + 1000, "description": "applied", "state": "success",
                "log_url": emissions.invocation(incidents.REPOSITORY, index + 100, 1)}]
            self.runs[index + 100] = {"id": index + 100, "run_attempt": 1, "head_sha": SHA,
                "head_branch": "main", "path": incidents.WORKFLOW, "workflow_id": 2, "event": "schedule",
                "repository": deepcopy(self.repo), "head_repository": deepcopy(self.repo)}
        self.attested = {record["id"]: deepcopy(emissions.receipt(incidents.REPOSITORY, record, self.statuses[record["id"]][0]))
                         for record in self.records}
        self.api_paths, self.proved, self.contexts = [], [], []
        self.active_reads = self.maximum_reads = 0
        self.lock = Lock()
        self.prove = lambda _: None
        self.before_read = lambda _: None
        self.status_failure = None

    def install(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        monkeypatch.setattr(subprocess, "check_output", self.command)
        return incidents.Ledger(deployments.GitHub(), {}, tmp_path)

    def command(self, command, **kwargs):
        if command[:2] == ["gh", "api"]:
            assert command[2:4] == ["--method", "GET"]
            with self.lock:
                self.active_reads += 1
                self.maximum_reads = max(self.maximum_reads, self.active_reads)
                self.contexts.append(retry._CURRENT.get())
            try:
                path = command[-1]
                self.api_paths.append(path)
                self.before_read(path)
                if "/statuses?" in path:
                    assert "--paginate" in command and "--slurp" in command
                    assert command[command.index("-H") + 1] == "Cache-Control: no-cache"
                    if self.status_failure:
                        raise self.status_failure
                    record_id = int(path.split("/deployments/")[1].split("/")[0])
                    return json.dumps([deepcopy(self.statuses[record_id])]).encode()
                if "/deployments?" in path:
                    assert "--paginate" in command and "--slurp" in command
                    # Multiple pages and persisted string payloads cross the
                    # real GitHub reader and ledger deserializer.
                    rows = deepcopy(self.records)
                    for index, row in enumerate(rows):
                        if index % 2:
                            row["payload"] = json.dumps(row["payload"])
                    return json.dumps([rows[:2], rows[2:]]).encode()
                if "/actions/runs/" in path:
                    return json.dumps(self.runs[int(path.split("/runs/")[1].split("/")[0])]).encode()
                if "/actions/workflows/" in path:
                    return json.dumps(self.workflow).encode()
                assert path == "repos/" + incidents.REPOSITORY
                return json.dumps(self.repo).encode()
            finally:
                with self.lock:
                    self.active_reads -= 1
        assert command[:3] == ["gh", "attestation", "verify"]
        assert kwargs == {"text": True, "stderr": subprocess.PIPE}
        value = json.loads(Path(command[3]).read_bytes())
        original = self.attested[value["deployment_id"]]
        run = self.runs[original["payload"]["execution_run_id"]]
        expected = emissions.policy(incidents.REPOSITORY, self.repo["id"], original, run, incidents.WORKFLOW)
        assert command[4:] == ["--repo", incidents.REPOSITORY, "--signer-workflow",
            incidents.REPOSITORY + "/" + incidents.WORKFLOW, "--signer-digest", SHA,
            "--source-digest", SHA, "--source-ref", "refs/heads/main", "--deny-self-hosted-runners", "--format", "json"]
        self.prove(value["deployment_id"])
        with self.lock:
            self.proved.append(value["deployment_id"])
        return json.dumps([{"verificationResult": {"signature": {"certificate": expected},
            "verifiedTimestamps": ["trusted timestamp"], "statement": {
                "_type": "https://in-toto.io/Statement/v1", "predicateType": emissions.PREDICATE,
                "subject": [{"name": emissions.name(original), "digest": {
                    "sha256": hashlib.sha256(emissions.canonical(original)).hexdigest()}}]}}}])


class BlockingProofs:
    def __init__(self):
        self.condition = Condition()
        self.pending, self.finished = {}, set()
        self.active = self.maximum = 0
        self.open = False

    def __call__(self, record_id):
        gate = Event()
        with self.condition:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.pending[record_id] = gate
            self.condition.notify_all()
        try:
            with self.condition:
                opened = self.open
            if not opened:
                assert gate.wait(10), "test did not release the proof transport"
        finally:
            with self.condition:
                self.active -= 1
                self.finished.add(record_id)
                self.condition.notify_all()

    def release_all(self):
        with self.condition:
            self.open = True
            for gate in self.pending.values():
                gate.set()
            self.condition.notify_all()


def test_complete_parallel_load_is_bounded_ordered_and_returns_no_partial_state(tmp_path, monkeypatch):
    provider, blocked = ProofTransport(9), BlockingProofs()
    provider.prove = blocked
    ledger = provider.install(monkeypatch, tmp_path)
    with ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(ledger.load)
        try:
            with blocked.condition:
                assert blocked.condition.wait_for(lambda: len(blocked.pending) == 4, timeout=5)
            assert not result.done()
            assert blocked.maximum == incidents.PROOF_WORKERS == 4
        finally:
            blocked.release_all()
        states = result.result(timeout=10)
    assert list(states) == [record["payload"]["state"]["scope"] for record in provider.records]
    assert set(provider.proved) == {record["id"] for record in provider.records}
    assert provider.maximum_reads == 1
    assert len([path for path in provider.api_paths if "/statuses?" in path]) == 9
    assert not hasattr(ledger._proof_reads, "post")


def test_all_other_proofs_finish_before_one_bad_signature_is_raised(tmp_path, monkeypatch):
    provider, blocked = ProofTransport(9), BlockingProofs()
    def prove(record_id):
        if record_id == provider.records[0]["id"]:
            raise subprocess.CalledProcessError(1, ["gh", "attestation", "verify"], stderr="Error: invalid signature")
        blocked(record_id)
    provider.prove = prove
    ledger = provider.install(monkeypatch, tmp_path)
    with ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(ledger.load)
        try:
            with blocked.condition:
                assert blocked.condition.wait_for(lambda: len(blocked.pending) == 4, timeout=5)
            assert not result.done(), "a failing proof must still drain all independent read tasks"
        finally:
            blocked.release_all()
        with pytest.raises(subprocess.CalledProcessError):
            result.result(timeout=10)
    assert blocked.finished == {record["id"] for record in provider.records[1:]}


@pytest.mark.parametrize("width,expected_waves", [(1, 8), (4, 2)])
def test_eight_proofs_take_two_controlled_waves_instead_of_eight(tmp_path, monkeypatch, width, expected_waves):
    provider, blocked = ProofTransport(8), BlockingProofs()
    provider.prove = blocked
    ledger = provider.install(monkeypatch, tmp_path)
    monkeypatch.setattr(incidents, "PROOF_WORKERS", width)
    waves = 0
    with ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(ledger.load)
        try:
            while waves * width < len(provider.records):
                with blocked.condition:
                    assert blocked.condition.wait_for(lambda: len(blocked.pending) == width, timeout=5)
                    gates = list(blocked.pending.values())
                    blocked.pending.clear()
                for gate in gates:
                    gate.set()
                waves += 1
        finally:
            blocked.release_all()
        assert len(result.result(timeout=10)) == len(provider.records)
    assert waves == expected_waves
    assert blocked.maximum <= width


@pytest.mark.parametrize("change", ["missing", "failed", "extra", "changed_status", "changed_payload"])
def test_parallel_cached_load_still_requires_fresh_exact_statuses_and_signed_bytes(tmp_path, monkeypatch, change):
    provider = ProofTransport(5)
    ledger = provider.install(monkeypatch, tmp_path)
    first = ledger.load()
    first[next(iter(first))]["label"] = "caller mutation"
    assert len(provider.proved) == 5
    assert all(state["label"] != "caller mutation" for state in ledger.load().values())
    assert len(provider.proved) == 5
    record_id = provider.records[0]["id"]
    if change == "missing":
        provider.statuses[record_id] = []
    elif change == "failed":
        provider.statuses[record_id][0]["state"] = "failure"
    elif change == "extra":
        provider.statuses[record_id].append(deepcopy(provider.statuses[record_id][0]))
    elif change == "changed_status":
        provider.statuses[record_id][0]["id"] += 9000
    else:
        provider.records[0]["payload"]["state"]["label"] = "forged new bytes"
    with pytest.raises((incidents.IncidentError, emissions.EmissionError)):
        ledger.load()
    assert len([path for path in provider.api_paths if "/statuses?" in path]) == 15


@pytest.mark.parametrize("change", ["schema", "identity", "conflicting_duplicate"])
def test_malformed_latest_or_conflicting_history_cannot_produce_partial_states(tmp_path, monkeypatch, change):
    provider = ProofTransport(5)
    if change == "schema":
        provider.records[0]["payload"]["state"]["extra"] = True
    elif change == "identity":
        provider.records[0]["creator"]["login"] = "forged"
    else:
        duplicate = deepcopy(provider.records[0])
        duplicate["payload"]["state"]["label"] = "different same ID"
        provider.records.append(duplicate)
    ledger = provider.install(monkeypatch, tmp_path)
    with pytest.raises(incidents.IncidentError):
        ledger.load()
    if change == "conflicting_duplicate":
        assert provider.proved == []
    else:
        assert set(provider.proved) == {record["id"] for record in provider.records[1:]}


def test_parallel_proofs_keep_one_parent_budget_and_never_start_a_ninth_failed_read(tmp_path, monkeypatch):
    provider = ProofTransport(5)
    ledger = provider.install(monkeypatch, tmp_path)
    clock = [0.0]
    monkeypatch.setattr(retry, "monotonic", lambda: clock[0])
    monkeypatch.setattr(retry, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    provider.status_failure = subprocess.CalledProcessError(1, ["gh", "api"], stderr=b"gh: HTTP 503")
    with retry.operation(profile=retry.UNATTENDED) as budget:
        with pytest.raises(subprocess.CalledProcessError):
            ledger.load()
    assert len([path for path in provider.api_paths if "/statuses?" in path]) == 8
    assert all(context is budget for context in provider.contexts)
    assert budget.retry_index == 7 and clock[0] == 1740
    assert provider.proved == [] and provider.maximum_reads == 1


def test_parallel_api_reads_are_serial_even_when_four_proofs_share_the_parent_budget(tmp_path, monkeypatch):
    provider = ProofTransport(5)
    ledger = provider.install(monkeypatch, tmp_path)
    attempted, entered, gate, overlap = set(), Event(), Event(), Event()
    condition = Condition()
    original_pages = ledger._proof_reads.pages
    def pages(path):
        with condition:
            attempted.add(path)
            condition.notify_all()
        return original_pages(path)
    monkeypatch.setattr(ledger._proof_reads, "pages", pages)
    def block(path):
        if "/statuses?" in path:
            if provider.active_reads > 1:
                overlap.set()
            entered.set()
            assert gate.wait(5)
    provider.before_read = block
    with retry.operation(profile=retry.UNATTENDED) as budget, ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(copy_context().run, ledger.load)
        try:
            assert entered.wait(5)
            with condition:
                assert condition.wait_for(lambda: len(attempted) == 4, timeout=5)
            assert not overlap.wait(0.05)
            assert not result.done()
        finally:
            gate.set()
        assert len(result.result(timeout=10)) == 5
    assert all(context is budget for context in provider.contexts)
    assert provider.maximum_reads == 1


def test_expired_parent_budget_cannot_be_replaced_by_fresh_thread_budgets(tmp_path, monkeypatch):
    provider = ProofTransport(5)
    ledger = provider.install(monkeypatch, tmp_path)
    clock = [0.0]
    monkeypatch.setattr(retry, "monotonic", lambda: clock[0])
    def complete_at_deadline(path):
        if "/deployments?" in path:
            clock[0] = 1800
    provider.before_read = complete_at_deadline
    with retry.operation(profile=retry.UNATTENDED) as budget:
        with pytest.raises(retry.RetryBudgetExhausted):
            ledger.load()
    assert provider.proved == []
    assert all(context is budget for context in provider.contexts)
    assert not any("/statuses?" in path for path in provider.api_paths)


def test_parallel_failure_selection_keeps_inventory_order_not_completion_order(tmp_path, monkeypatch):
    provider = ProofTransport(5)
    ledger = provider.install(monkeypatch, tmp_path)
    first, second, gate = provider.records[0]["id"], Event(), Event()
    def prove(record_id):
        if record_id == first:
            assert gate.wait(5)
            raise emissions.EmissionError("first inventory proof failed")
        if record_id == first + 1:
            second.set()
            raise emissions.EmissionError("second inventory proof failed earlier")
    provider.prove = prove
    with ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(ledger.load)
        try:
            assert second.wait(5)
            assert not result.done()
        finally:
            gate.set()
        with pytest.raises(emissions.EmissionError, match="first inventory proof"):
            result.result(timeout=10)
    assert set(provider.proved) == {record["id"] for record in provider.records[2:]}


@pytest.mark.parametrize("count", [0, 1])
def test_empty_and_single_complete_inventory_still_cross_the_same_proof_boundary(tmp_path, monkeypatch, count):
    provider = ProofTransport(count)
    ledger = provider.install(monkeypatch, tmp_path)
    assert len(ledger.load()) == count
    assert len(provider.proved) == count
