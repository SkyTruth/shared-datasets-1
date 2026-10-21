"""Signed, bounded cloud collection checkpoints across ephemeral runners.

Parts are persisted before their manifest. Every record needs an exact applied
receipt from the main notification workflow before it becomes evidence. Failed
or unsigned latest manifests block collection; older cursors are never silently
substituted. This module neither emits a heartbeat nor grants recovery authority.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlencode
import zlib

import yaml

from scripts import cloud_operational_observations as collector
from scripts import deployment_emission as emissions
from scripts import deployment_revision as deployments
from scripts import workflow_failure_alert as alerts


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "SkyTruth/shared-datasets-1"
WORKFLOW = ".github/workflows/unattended-workflow-alert.yml"
ENVIRONMENT = TASK = "operational-observation-cycle"
PART_TASK = "operational-observation-part"
SCHEMA = "shared-datasets-cloud-observation-cycle-v1"
PART_SCHEMA = "shared-datasets-cloud-observation-part-v1"
ENCODING = "zlib+base64"
CHUNK_BYTES = 32 * 1024
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_COMPRESSED_BYTES = 12 * 1024 * 1024
MAX_PARTS = 512
HEX = re.compile(r"[0-9a-f]{64}")
SHA = re.compile(r"[0-9a-f]{40}")
NONCE = re.compile(r"[0-9a-f]{32}")
POLICY_FILE = "catalog/alert-routing.json"
# Each path participates in the actual protocol. Unrelated main commits do
# not reset shadow evidence; a missing protocol file is a configuration error.
CONTRACT_FILES = tuple(sorted({
    POLICY_FILE, "catalog/shared-datasets-catalog.csv", "scripts/operational_policy.py", "scripts/cloud_operational_observations.py",
    "ingestion/common/operational_events.py", "scripts/slack_incidents.py",
    "scripts/cloud_incident_adapter.py", "scripts/operational_reconcile.py",
    "scripts/translation_recovery.py", "scripts/release_feature_model.py", "scripts/catalog_csv.py",
    "scripts/feature_metadata_localization.py", "scripts/translation_local_io.py",
    "scripts/operational_cutover.py", "scripts/operational_delivery_activation.py",
    ".github/workflows/prod-terraform-target-apply.yml",
    "terraform/envs/prod/operational_observer.tf",
    "terraform/envs/prod/github_readonly_iam.tf",
    "scripts/readonly_wif_trust.py", "scripts/wif_setup_permissions.py", "scripts/setup_grant_proof.py",
    "scripts/cloud_observation_ledger.py", "scripts/deployment_receipt_contracts.py",
    "scripts/deployment_emission.py", "scripts/deployment_revision.py", "scripts/slack_incident_api.py", "scripts/workflow_failure_alert.py",
    "scripts/operational_retry.py",
    "scripts/github_api_read.py",
    "scripts/gcp_read_retry.py", "ingestion/wdpa_monthly/execution_observer.py",
    "terraform/envs/prod/wdpa_execution_observer.tf",
    "ingestion/wdpa_monthly/Dockerfile", "ingestion/wdpa_monthly/Dockerfile.promotion",
    "ingestion/common/runtime.py", "ingestion/common/feature_metadata.py", "ingestion/common/translation_notices.py",
    "ingestion/common/legacy_runtime_notifications.py", "scripts/slack_notify.py", WORKFLOW,
    *('.github/workflows/' + path for path in set(deployments.TARGET_WORKFLOWS.values())
      | set(deployments.TERRAFORM_WORKFLOWS.values()) | set(alerts.WORKFLOW_PATHS.values()) | {"ci.yml"}),
}))
WORKER_FIELDS = {"source_sha", "execution_run_id", "execution_run_attempt"}
PART_FIELDS = {"schema", "cycle_nonce", *WORKER_FIELDS, "index", "total", "data", "sha256"}
MANIFEST_FIELDS = {"schema", "cycle_nonce", *WORKER_FIELDS, "policy_sha256", "collector_contract_sha256", "mode",
                   "started_at", "completed_at", "covered_through", "previous_cycle_id", "encoding",
                   "body_sha256", "body_bytes", "compressed_sha256", "compressed_bytes", "parts"}


class CycleLedgerError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise CycleLedgerError("CLOUD_CYCLE: " + message)


def positive(value):
    return type(value) is int and value > 0


def canonical(value):
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()
    except (TypeError, ValueError) as error:
        raise CycleLedgerError("cloud cycle must contain finite JSON values") from error


def strict_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = item
        return result

    def reject_constant(value):
        raise CycleLedgerError("non-finite JSON constant")

    try:
        return json.loads(value, object_pairs_hook=unique, parse_constant=reject_constant)
    except (TypeError, ValueError) as error:
        raise CycleLedgerError("invalid cloud cycle JSON") from error


def digest(value):
    return hashlib.sha256(value).hexdigest()


def policy_hash(root=ROOT):
    return digest((Path(root) / POLICY_FILE).read_bytes())


def contract_hash(root=ROOT):
    root, paths = Path(root), set(CONTRACT_FILES)
    pending = [path for path in paths if path.startswith(".github/workflows/")]
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        document = yaml.safe_load((root / path).read_bytes())
        require(isinstance(document, dict) and isinstance(document.get("jobs"), dict), "invalid reviewed workflow target graph")
        for job in document["jobs"].values():
            require(isinstance(job, dict), "invalid reviewed workflow job")
            caller = job.get("uses", "")
            if isinstance(caller, str) and caller.startswith("./.github/workflows/"):
                require(re.fullmatch(r"[a-zA-Z0-9_.-]+\.ya?ml", caller.rsplit("/", 1)[-1])
                        and len(caller.split("/")) == 4, "invalid local workflow dependency")
                child = caller.removeprefix("./")
                paths.add(child)
                pending.append(child)
    return digest(canonical({path: digest((root / path).read_bytes()) for path in sorted(paths)}))


def validate_bundle(checkpoint, evaluation, mode):
    require(mode in {"shadow", "unified"}, "unsupported collection mode")
    collector.validate_checkpoint(checkpoint)
    cycle = checkpoint["last_cycle"]
    require(set(cycle) == {"start", "end", "completed_at", "new_events", "views", "coverage_gaps", "overlap_seconds", "ingestion_lag_seconds"},
            "incomplete cycle coverage evidence")
    start, end, completed = (collector.instant(cycle[key]) for key in ("start", "end", "completed_at"))
    require(start <= end <= completed and cycle["end"] == checkpoint["covered_through"], "inconsistent coverage clock")
    require(cycle["views"] == list(collector.VIEWS), "cycle did not cover every registered log view")
    require(type(cycle["new_events"]) is int and 0 <= cycle["new_events"] <= collector.MAX_EVENTS, "invalid event count")
    require(isinstance(cycle["coverage_gaps"], list) and len(cycle["coverage_gaps"]) <= collector.MAX_EVENTS
            and all(isinstance(gap, str) and 0 < len(gap) <= 1000 for gap in cycle["coverage_gaps"]), "invalid coverage gaps")
    require(type(cycle["overlap_seconds"]) is int and cycle["overlap_seconds"] == int(collector.OVERLAP.total_seconds())
            and type(cycle["ingestion_lag_seconds"]) is int and cycle["ingestion_lag_seconds"] == int(collector.INGESTION_LAG.total_seconds()),
            "cycle overlap or lag contract changed")
    require(isinstance(evaluation, dict) and evaluation.get("complete") is True, "policy evaluation is incomplete")
    require(type(evaluation.get("provenance_complete")) is bool, "evaluation must explicitly report provenance coverage")
    require(type(evaluation.get("public_delivery_count")) is int and evaluation["public_delivery_count"] >= 0,
            "evaluation must report public delivery count")
    require(mode != "shadow" or evaluation["public_delivery_count"] == 0, "shadow evaluation cannot deliver public messages")
    canonical({"checkpoint": checkpoint, "evaluation": evaluation})


def worker_payload(value):
    require(isinstance(value["source_sha"], str) and SHA.fullmatch(value["source_sha"])
            and positive(value["execution_run_id"]) and positive(value["execution_run_attempt"]), "invalid main worker identity")
    require(isinstance(value["cycle_nonce"], str) and NONCE.fullmatch(value["cycle_nonce"]), "invalid collection nonce")


def validate_manifest(value):
    require(isinstance(value, dict) and set(value) == MANIFEST_FIELDS and value["schema"] == SCHEMA, "invalid cycle manifest schema")
    worker_payload(value)
    require(value["mode"] in {"shadow", "unified"} and value["encoding"] == ENCODING, "unsupported cycle protocol")
    require(all(isinstance(value[key], str) and HEX.fullmatch(value[key]) for key in (
        "policy_sha256", "collector_contract_sha256", "body_sha256", "compressed_sha256")), "invalid cycle content digest")
    require(positive(value["body_bytes"]) and value["body_bytes"] <= MAX_BODY_BYTES
            and positive(value["compressed_bytes"]) and value["compressed_bytes"] <= MAX_COMPRESSED_BYTES, "cycle content exceeds limit")
    require(value["previous_cycle_id"] is None or positive(value["previous_cycle_id"]), "invalid predecessor cycle")
    start, covered, completed = (collector.instant(value[key]) for key in ("started_at", "covered_through", "completed_at"))
    require(start <= covered <= completed, "invalid cycle manifest clock")
    refs = value["parts"]
    require(isinstance(refs, list) and 1 <= len(refs) <= MAX_PARTS, "invalid cycle part count")
    require(all(isinstance(ref, dict) and set(ref) == {"id", "index", "sha256"}
                and positive(ref["id"]) and type(ref["index"]) is int and ref["index"] == index
                and isinstance(ref["sha256"], str) and HEX.fullmatch(ref["sha256"]) for index, ref in enumerate(refs)),
            "invalid or unordered cycle part reference")
    require(len({ref["id"] for ref in refs}) == len(refs), "duplicate cycle part reference")
    return value


def validate_part(value):
    require(isinstance(value, dict) and set(value) == PART_FIELDS and value["schema"] == PART_SCHEMA, "invalid cycle part schema")
    worker_payload(value)
    require(type(value["index"]) is int and type(value["total"]) is int and 0 <= value["index"] < value["total"] <= MAX_PARTS,
            "invalid cycle part index")
    data = value["data"]
    require(isinstance(data, str) and 0 < len(data) <= CHUNK_BYTES and data.isascii()
            and re.fullmatch(r"[A-Za-z0-9+/=]+", data), "invalid cycle part encoding")
    require(value["index"] == value["total"] - 1 or len(data) == CHUNK_BYTES, "incomplete intermediate cycle part")
    require(value["sha256"] == digest(data.encode("ascii")), "cycle part digest mismatch")
    return value


def validate_worker(run):
    require(isinstance(run, dict) and run.get("path") == WORKFLOW and run.get("head_branch") == "main"
            and run.get("event") in {"schedule", "workflow_run", "workflow_dispatch"}
            and positive(run.get("id")) and positive(run.get("run_attempt"))
            and isinstance(run.get("head_sha"), str) and SHA.fullmatch(run["head_sha"])
            and all(run.get(key, {}).get("full_name") == REPOSITORY for key in ("repository", "head_repository")),
            "cycle writes require the authenticated main notification worker")
    return run


class CycleLedger:
    def __init__(self, api, run, workdir, *, root=ROOT):
        self.api, self.run, self.workdir, self.root = api, run, Path(workdir), Path(root)

    def context(self):
        run = validate_worker(self.run)
        return {"source_sha": run["head_sha"], "execution_run_id": run["id"], "execution_run_attempt": run["run_attempt"],
                "policy_sha256": policy_hash(self.root), "collector_contract_sha256": contract_hash(self.root)}

    def _verified_payload(self, record, *, task, schema):
        value = strict_json(record["payload"]) if isinstance(record.get("payload"), str) else record.get("payload")
        require(positive(record.get("id")) and isinstance(value, dict) and value.get("schema") == schema
                and record.get("task") == task and record.get("environment") == ENVIRONMENT
                and record.get("production_environment") is False and record.get("transient_environment") is False
                and record.get("ref") == record.get("sha") == value.get("source_sha")
                and record.get("creator", {}).get("login") == "github-actions[bot]", "invalid cycle deployment identity")
        (validate_manifest if schema == SCHEMA else validate_part)(value)
        statuses = self.api.pages(f"repos/{REPOSITORY}/deployments/{record['id']}/statuses?per_page=100")
        require(len(statuses) == 1 and positive(statuses[0].get("id")) and statuses[0].get("description") == "applied"
                and statuses[0].get("state") == "success" and statuses[0].get("environment_url") == ""
                and statuses[0].get("log_url") == emissions.invocation(REPOSITORY, value["execution_run_id"], value["execution_run_attempt"]),
                "cycle checkpoint is incomplete or has another worker status")
        emissions.verify(self.api, REPOSITORY, record, statuses[0], original_signer=WORKFLOW, batch=True)
        worker = validate_worker(self.api.get(
            f"repos/{REPOSITORY}/actions/runs/{value['execution_run_id']}/attempts/{value['execution_run_attempt']}"))
        require(worker["id"] == value["execution_run_id"] and worker["run_attempt"] == value["execution_run_attempt"]
                and worker["head_sha"] == value["source_sha"], "cycle source differs from authoritative worker revision")
        return value

    def verify(self, record):
        manifest = self._verified_payload(record, task=TASK, schema=SCHEMA)
        require(manifest["previous_cycle_id"] is None or manifest["previous_cycle_id"] < record["id"], "invalid cycle predecessor order")
        chunks = []
        for index, ref in enumerate(manifest["parts"]):
            require(ref["id"] < record["id"], "manifest precedes one of its parts")
            part_record = self.api.get(f"repos/{REPOSITORY}/deployments/{ref['id']}")
            require(part_record.get("id") == ref["id"], "cycle part identity changed")
            part = self._verified_payload(part_record, task=PART_TASK, schema=PART_SCHEMA)
            require(all(part[key] == manifest[key] for key in {"cycle_nonce", *WORKER_FIELDS})
                    and part["index"] == index and part["total"] == len(manifest["parts"])
                    and part["sha256"] == ref["sha256"], "cycle part belongs to another collection")
            chunks.append(part["data"])
        encoded = "".join(chunks)
        require(len(encoded) <= 4 * ((MAX_COMPRESSED_BYTES + 2) // 3), "encoded cycle exceeds limit")
        try:
            compressed = base64.b64decode(encoded, validate=True)
            require(base64.b64encode(compressed).decode("ascii") == encoded, "noncanonical cycle base64")
            require(len(compressed) == manifest["compressed_bytes"] and digest(compressed) == manifest["compressed_sha256"],
                    "compressed cycle digest mismatch")
            inflater = zlib.decompressobj()
            body = inflater.decompress(compressed, MAX_BODY_BYTES + 1)
            require(len(body) <= MAX_BODY_BYTES and inflater.eof and not inflater.unconsumed_tail and not inflater.unused_data,
                    "cycle decompression exceeds limit or has trailing/incomplete bytes")
        except (binascii.Error, zlib.error) as error:
            raise CycleLedgerError("invalid compressed cycle encoding") from error
        require(len(body) == manifest["body_bytes"] and digest(body) == manifest["body_sha256"], "cycle body digest mismatch")
        bundle = strict_json(body)
        require(isinstance(bundle, dict) and set(bundle) == {"checkpoint", "evaluation"} and canonical(bundle) == body, "invalid canonical cycle bundle")
        validate_bundle(bundle["checkpoint"], bundle["evaluation"], manifest["mode"])
        cycle = bundle["checkpoint"]["last_cycle"]
        require(manifest["started_at"] == cycle["start"] and manifest["completed_at"] == cycle["completed_at"]
                and manifest["covered_through"] == bundle["checkpoint"]["covered_through"], "manifest differs from exact covered cycle")
        return {"manifest_id": record["id"], "manifest": deepcopy(manifest), **bundle}

    def verify_cycle_id(self, identifier):
        require(positive(identifier), "invalid cycle identifier")
        record = self.api.get(f"repos/{REPOSITORY}/deployments/{identifier}")
        require(record.get("id") == identifier, "cycle identity changed")
        return self.verify(record)

    def load(self):
        query = urlencode({"environment": ENVIRONMENT, "task": TASK, "per_page": 100})
        records = self.api.pages(f"repos/{REPOSITORY}/deployments?{query}")
        require(isinstance(records, list) and all(isinstance(item, dict) and positive(item.get("id")) for item in records), "incomplete cycle enumeration")
        unique = {}
        for record in records:
            identifier = record["id"]
            require(identifier not in unique or canonical(unique[identifier]) == canonical(record), "conflicting repeated cycle identity")
            unique[identifier] = record
        return self.verify(max(unique.values(), key=lambda item: item["id"])) if unique else None

    def _save_record(self, value, task):
        record = self.api.post(f"repos/{REPOSITORY}/deployments", {
            "ref": self.run["head_sha"], "task": task, "environment": ENVIRONMENT, "production_environment": False,
            "transient_environment": False, "auto_merge": False, "required_contexts": [],
            "description": f"Cloud observation {value['cycle_nonce']} {task}", "payload": value,
        })
        actual = strict_json(record["payload"]) if isinstance(record.get("payload"), str) else record.get("payload")
        require(positive(record.get("id")) and canonical(actual) == canonical(value)
                and record.get("ref") == record.get("sha") == self.run["head_sha"]
                and record.get("task") == task and record.get("environment") == ENVIRONMENT
                and record.get("production_environment") is False and record.get("transient_environment") is False
                and record.get("creator", {}).get("login") == "github-actions[bot]", "cycle record write was not acknowledged exactly")
        status = self.api.post(f"repos/{REPOSITORY}/deployments/{record['id']}/statuses", {
            "state": "success", "description": "applied", "auto_inactive": False, "environment_url": "",
            "log_url": emissions.invocation(REPOSITORY, self.run["id"], self.run["run_attempt"]),
        })
        require(positive(status.get("id")) and all(status.get(key) == expected for key, expected in (
            ("state", "success"), ("description", "applied"), ("environment_url", ""),
            ("log_url", emissions.invocation(REPOSITORY, self.run["id"], self.run["run_attempt"])))), "cycle status was not acknowledged exactly")
        receipt = emissions.receipt(REPOSITORY, record, status)
        directory = self.workdir / "claims"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / emissions.name(receipt)).write_bytes(emissions.canonical(receipt))
        if output := os.environ.get("GITHUB_OUTPUT"):
            with Path(output).open("a") as stream:
                stream.write("claims=true\n")
        return record

    def save(self, checkpoint, evaluation, mode, *, before_commit, previous_cycle_id=None, source_sha=None,
             policy_sha256=None, collector_contract_sha256=None):
        run = validate_worker(self.run)
        require(source_sha is None or source_sha == run["head_sha"], "cycle source differs from current main worker")
        require(previous_cycle_id is None or positive(previous_cycle_id), "invalid cycle predecessor")
        validate_bundle(checkpoint, evaluation, mode)
        actual_policy, actual_contract = policy_hash(self.root), contract_hash(self.root)
        require(policy_sha256 is None or policy_sha256 == actual_policy, "cycle policy hash differs from checked-out policy")
        require(collector_contract_sha256 is None or collector_contract_sha256 == actual_contract, "cycle contract hash differs from checked-out protocol")
        previous = self.load()
        require((previous["manifest_id"] if previous else None) == previous_cycle_id, "cycle predecessor differs from latest verified checkpoint")
        if previous is not None:
            require(collector.instant(checkpoint["covered_through"]) >= collector.instant(previous["checkpoint"]["covered_through"])
                    and collector.instant(checkpoint["last_cycle"]["completed_at"]) >= collector.instant(previous["manifest"]["completed_at"]),
                    "cycle cannot rewind durable coverage or completion")
        body = canonical({"checkpoint": checkpoint, "evaluation": evaluation})
        require(len(body) <= MAX_BODY_BYTES, "cycle body exceeds persistence limit")
        compressed = zlib.compress(body, level=9)
        require(len(compressed) <= MAX_COMPRESSED_BYTES, "compressed cycle exceeds persistence limit")
        encoded = base64.b64encode(compressed).decode("ascii")
        chunks = [encoded[index:index + CHUNK_BYTES] for index in range(0, len(encoded), CHUNK_BYTES)]
        require(len(chunks) <= MAX_PARTS, "cycle needs too many durable parts")
        common = {"cycle_nonce": secrets.token_hex(16), "source_sha": run["head_sha"],
                  "execution_run_id": run["id"], "execution_run_attempt": run["run_attempt"]}
        refs = []
        before_commit()  # Predecessor verification can outlive the installed policy snapshot.
        for index, data in enumerate(chunks):
            part = validate_part({"schema": PART_SCHEMA, **common, "index": index, "total": len(chunks), "data": data, "sha256": digest(data.encode("ascii"))})
            record = self._save_record(part, PART_TASK)
            refs.append({"id": record["id"], "index": index, "sha256": part["sha256"]})
        cycle = checkpoint["last_cycle"]
        manifest = validate_manifest({"schema": SCHEMA, **common, "policy_sha256": actual_policy,
            "collector_contract_sha256": actual_contract, "mode": mode, "started_at": cycle["start"],
            "completed_at": cycle["completed_at"], "covered_through": checkpoint["covered_through"],
            "previous_cycle_id": previous_cycle_id, "encoding": ENCODING, "body_sha256": digest(body), "body_bytes": len(body),
            "compressed_sha256": digest(compressed), "compressed_bytes": len(compressed), "parts": refs})
        before_commit()  # Unreferenced parts do not advance coverage after a transition.
        record = self._save_record(manifest, TASK)
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "cycle.json").write_bytes(canonical({"manifest_id": record["id"]}))
        return {"manifest_id": record["id"], "manifest": deepcopy(manifest)}

    def local_cycle_id(self):
        value = strict_json((self.workdir / "cycle.json").read_bytes())
        require(isinstance(value, dict) and set(value) == {"manifest_id"} and positive(value["manifest_id"]), "invalid local cycle reference")
        return value["manifest_id"]
