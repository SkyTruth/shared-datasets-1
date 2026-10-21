"""Strict, pure cloud evidence normalization for the signed incident ledger.

Cloud identity is authenticated by the filtered collector, never by a Git SHA
printed in a log. These observations confer no execution or replay authority.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import re
from pathlib import Path
from urllib.parse import urlencode

import yaml

from ingestion.common.operational_events import validate as validate_event
from scripts import cloud_operational_observations as cloud, deployment_revision as deployments, operational_policy as policy

SCHEMA = "shared-datasets-cloud-incident-proof-v1"
SOURCE = re.compile(r"cloud(?:log|exec):[0-9a-f]{64}")
SCOPE = re.compile(r"[a-z][a-z0-9:._-]{0,150}")
EXECUTION_FIELDS = {"name", "uid", "revision", "coverage_gaps", "terminal", "success", "completion_time", "create_time", "start_time", "images"}


@lru_cache(maxsize=1)
def registered_jobs():
    # Reuse the deployment target graph and its explicit job resource binding.
    # A new runtime job cannot gain suppression from looking like an old name.
    root = Path(__file__).resolve().parents[1] / ".github/workflows"
    return frozenset({name for name, workflow in deployments.TARGET_WORKFLOWS.items()
                      if yaml.safe_load((root / workflow).read_text()).get("env", {}).get("JOB_NAME") == name}
                     | set(deployments.AUXILIARY_CLOUD_JOBS))


def require(condition, message):
    if not condition:
        raise ValueError("CLOUD_INCIDENT: " + message)


def seconds(value):
    return policy.timestamp(cloud.instant(value))


def link(fact):
    execution = fact.get("execution")
    if execution:
        name = execution["name"].rsplit("/", 1)[-1]
        return f"https://console.cloud.google.com/run/jobs/executions/details/{cloud.REGION}/{name}?project={cloud.PROJECT}"
    query = f'logName="{fact["evidence"]["log_name"]}" insertId="{fact["evidence"]["insert_id"]}"'
    return "https://console.cloud.google.com/logs/query?" + urlencode({"project": cloud.PROJECT, "query": query})


def validate_fact(value):
    base = {"source_id", "observed_at", "resource_type", "job", "evidence", "kind"}
    require(isinstance(value, dict) and value.get("kind") in {"runtime", "execution", "execution_request", "scheduler_dispatch"}, "invalid source variant")
    kind = value["kind"]
    fields = base | ({"fact", "execution"} if kind == "runtime" else {"execution"} if kind == "execution"
                     else {"reason", "mutation_status", "status_code", "coverage_gaps"} if kind == "execution_request"
                     else {"mutation_status", "coverage_gaps"})
    optional = {"receipt_metadata", "run_receipt"} if kind == "runtime" else set()
    require(fields <= set(value) <= fields | optional, "unexpected source proof fields")
    require(isinstance(value["source_id"], str) and SOURCE.fullmatch(value["source_id"]), "invalid cloud source identity")
    seconds(value["observed_at"])
    require(value["resource_type"] in {"cloud_run_job", "cloud_scheduler_job"}
            and isinstance(value["job"], str) and cloud.NAME.fullmatch(value["job"]), "invalid authenticated resource")
    evidence = value["evidence"]
    require(isinstance(evidence, dict) and set(evidence) == {"log_name", "insert_id"}
            and isinstance(evidence["log_name"], str) and evidence["log_name"].startswith(f"projects/{cloud.PROJECT}/logs/")
            and isinstance(evidence["insert_id"], str) and 0 < len(evidence["insert_id"]) <= 1024, "invalid authenticated log identity")
    source = "cloudlog:" + cloud.digest({"logName": evidence["log_name"], "insertId": evidence["insert_id"]})
    execution = value.get("execution")
    if execution:
        require(value["resource_type"] == "cloud_run_job" and isinstance(execution, dict) and set(execution) == EXECUTION_FIELDS,
                "invalid execution proof variant")
        require(isinstance(execution["name"], str) and re.fullmatch(re.escape(f"projects/{cloud.PROJECT}/locations/{cloud.REGION}/jobs/{value['job']}/executions/") + r"[a-z][a-z0-9-]{0,62}", execution["name"]), "execution belongs to another resource")
        require(execution["uid"] is None or isinstance(execution["uid"], str) and 0 < len(execution["uid"]) <= 200, "invalid authoritative execution UID")
        require(type(execution["terminal"]) is bool and type(execution["success"]) is bool
                and (not execution["success"] or execution["terminal"]), "invalid execution outcome")
        require((execution["completion_time"] is not None) == execution["terminal"], "execution terminal proof is incomplete")
        if execution["completion_time"]:
            seconds(execution["completion_time"])
        for key in ("create_time", "start_time"):
            if execution[key] is not None:
                seconds(execution[key])
        if execution["create_time"] is not None and execution["start_time"] is not None:
            require(cloud.instant(execution["create_time"]) <= cloud.instant(execution["start_time"]), "execution started before creation")
        if execution["start_time"] is not None and execution["completion_time"] is not None:
            require(cloud.instant(execution["start_time"]) <= cloud.instant(execution["completion_time"]), "execution completed before start")
        images = execution["images"]
        require(isinstance(images, list) and images and all(isinstance(image, str) and image for image in images), "execution images are incomplete")
        immutable = len(images) == 1 and "@" in images[0] and cloud.DIGEST.fullmatch(images[0].rsplit("@", 1)[1])
        expected_revision = images[0].rsplit("@", 1)[1] if immutable else None
        if expected_revision is None and isinstance(execution["uid"], str) and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", execution["uid"]) and execution["uid"].replace("-", "") != "0" * 32:
            expected_revision = "cloudrun-execution:" + execution["uid"]
        require(execution["revision"] == expected_revision and execution["coverage_gaps"] == ([] if immutable else ["execution_image_digest_unresolved"]), "execution artifact proof differs from source")
    if value["source_id"].startswith("cloudexec:"):
        require(execution is not None and value["source_id"] == "cloudexec:" + cloud.digest({"source": source, "proof": execution}), "invalid execution transition identity")
    else:
        require(value["source_id"] == source, "log source identity differs from evidence")
    if kind == "runtime":
        event = validate_event(value["fact"])
        require(event["context"]["job"] == value["job"] and event["context"]["execution"] == execution["name"].rsplit("/", 1)[-1], "runtime fact differs from authenticated execution")
        if "receipt_metadata" in value:
            metadata = value["receipt_metadata"]
            uri = event["details"].get("receipt_uri")
            require(isinstance(metadata, dict) and uri == f"gs://{metadata.get('bucket')}/{metadata.get('name')}"
                    and metadata.get("bucket") == cloud.BUCKET
                    and (event["details"].get("receipt_generation") is None or str(metadata.get("generation")) == str(event["details"]["receipt_generation"])), "translation receipt metadata differs")
        if "run_receipt" in value:
            receipt = cloud.validate_run_receipt(value["run_receipt"])
            require(receipt["uri"] == event["details"].get("run_record_uri") and receipt["generation"] == event["details"].get("run_record_generation")
                    and receipt["asset_slug"] == event["subject"].get("asset_slug") and receipt["release_date"] == event["subject"].get("release"), "run receipt differs from runtime outcome")
            require(event["status"] == receipt["status"], "runtime status differs from canonical run record")
    elif kind == "execution_request":
        require(value["resource_type"] == "cloud_run_job" and value["reason"] in {"permission_denied", "unknown"}
                and type(value["status_code"]) is int and value["status_code"] > 0
                and value["reason"] == ("permission_denied" if value["status_code"] in {7, 16} else "unknown")
                and value["mutation_status"] == "uncertain" and value["coverage_gaps"] == ["execution_request_outcome_unverified"], "invalid failed execution request")
    elif kind == "scheduler_dispatch":
        require(value["resource_type"] == "cloud_scheduler_job" and value["mutation_status"] == "uncertain"
                and value["coverage_gaps"] == ["scheduler_dispatch_outcome_unverified"], "invalid scheduler proof")
    return value


def domain(fact):
    if fact["kind"] != "runtime":
        return None
    event = fact["fact"]
    kind = event["kind"]
    if kind == "identity_decision" or kind == "job_outcome" and event["status"] == "blocked":
        return "identity"
    if kind == "translation_maintenance":
        return "translation"
    return None


def source_scope(fact):
    subject = domain_subject(fact)
    family = domain(fact)
    if family:
        identity = ({"asset": subject["asset_slug"], "release": subject["release"]} if subject else
                    {"unverified_manifest": fact.get("fact", {}).get("subject", {}).get("manifest_uri") or fact["source_id"]})
        return "cloud:" + family + ":" + cloud.digest(identity)
    return f"cloud:{fact['resource_type']}:{fact['job']}"


def domain_subject(fact):
    subject = fact.get("fact", {}).get("subject", {})
    if set(subject) >= {"asset_slug", "release"}:
        return {key: subject[key] for key in ("asset_slug", "release")}
    uri = subject.get("manifest_uri")
    if isinstance(uri, str):
        for slug, root in cloud.asset_roots().items():
            match = re.fullmatch(re.escape(f"gs://{cloud.BUCKET}/{root}/releases/") + r"(\d{4}-\d\d-\d\d)/" + re.escape(slug) + r"[.]manifest[.]json", uri)
            if match:
                return {"asset_slug": slug, "release": match[1]}
    return None


def revision(fact):
    execution = fact.get("execution")
    if execution and execution["revision"] and execution["uid"]:
        return execution["revision"]
    return "cloudlog-source:" + fact["source_id"].split(":", 1)[1]


def provenance_gap(fact, correlation=None):
    return (fact["job"] not in registered_jobs() or not cloud.DIGEST.fullmatch(revision(fact))
            or bool(fact.get("execution", {}).get("coverage_gaps")) or domain(fact) is not None and domain_subject(fact) is None
            or fact["job"] in deployments.AUXILIARY_CLOUD_JOBS and fact.get("execution", {}).get("terminal") is not False
               and (correlation is None or correlation.get("kind") != "auxiliary_artifact"))


def observational_success(fact, correlation):
    execution = fact.get("execution", {})
    return bool(correlation is not None and correlation.get("kind") == "auxiliary_artifact"
                and not provenance_gap(fact, correlation) and execution.get("terminal") and execution.get("success")
                and (fact["kind"] == "execution" or fact["kind"] == "runtime" and fact["fact"]["kind"] == "job_outcome"
                     and fact["fact"]["status"] in {"success", "skipped"}))


def recoverable(fact):
    """A green execution/log alone cannot establish canonical publication."""
    execution = fact.get("execution", {})
    event = fact.get("fact", {})
    receipt = fact.get("run_receipt")
    started = execution.get("start_time") or execution.get("create_time")
    return bool(not provenance_gap(fact) and execution.get("terminal") and execution.get("success")
                and event.get("kind") == "job_outcome" and event.get("status") in {"success", "skipped"}
                and receipt and started and event.get("subject", {}).get("asset_slug")
                and cloud.instant(started) <= cloud.instant(receipt["created_at"]) <= cloud.instant(execution["completion_time"])
                and cloud.instant(receipt["created_at"]) <= cloud.instant(fact["observed_at"])
                and cloud.instant(execution["completion_time"]) >= cloud.instant(event["observed_at"]))


def normalize(fact, *, correlation=None, promote=False):
    validate_fact(fact)
    event = fact.get("fact", {})
    gap = provenance_gap(fact, correlation)
    # These are audit facts. They must not produce a red parent merely because
    # a healthy worker has no publication receipt, or falsely close a failure.
    if not gap and fact["kind"] == "execution" and not fact["execution"]["terminal"]:
        return None
    family = domain(fact)
    if not gap and family == "translation" and (event["status"] == "delivered" or event["details"].get("legacy_delivery_state") == "delivered"):
        return None
    success = recoverable(fact) or observational_success(fact, correlation)
    if not gap and not family and not success and (
            fact["kind"] == "runtime" and event["status"] in {"success", "skipped"} and not success
            or fact["kind"] == "execution" and fact["execution"]["success"]):
        return None
    scope = source_scope(fact)
    kind = "job"
    subject = domain_subject(fact)
    label = f"{fact['job']}" + (f" · {family} · {subject['asset_slug']} {subject['release']}" if family and subject else f" · {family} provenance" if family else "")
    if correlation is not None:
        validate_correlation(correlation)
        require(correlation["artifact"] in fact["execution"]["images"], "correlated artifact differs from execution")
        require(correlation["execution"] == fact["execution"]["name"], "correlated execution differs")
        if promote and family is None and correlation.get("kind") != "auxiliary_artifact":
            scope, kind, label = "deployment:" + fact["job"], "deployment", fact["job"]
    return validate_observation({"source": "cloud", "scope": scope, "kind": kind, "label": label,
        "source_id": fact["source_id"], "revision": revision(fact), "time": seconds(fact["observed_at"]),
        "conclusion": "success" if success else "failure",
        "proof": {"schema": SCHEMA, "fact": deepcopy(fact), "correlation": deepcopy(correlation)}})


def validate_correlation(value):
    if isinstance(value, dict) and value.get("kind") == "auxiliary_artifact":
        require(set(value) == {"kind", "record_id", "status_id", "revision", "artifact", "execution", "deployment_target", "resource"},
                "invalid auxiliary artifact proof schema")
        matches = [contract for job, contract in deployments.AUXILIARY_CLOUD_JOBS.items()
                   if value["deployment_target"] == contract["deployment_target"] and value["resource"] == contract["resource"]
                   and isinstance(value["execution"], str) and re.fullmatch(
                       re.escape(f"projects/{cloud.PROJECT}/locations/{cloud.REGION}/jobs/{job}/executions/") + r"[a-z][a-z0-9-]{0,62}", value["execution"])]
        require(len(matches) == 1 and all(type(value[key]) is int and value[key] > 0 for key in ("record_id", "status_id"))
                and isinstance(value["revision"], str) and policy.SHA.fullmatch(value["revision"])
                and isinstance(value["artifact"], str) and re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", value["artifact"]),
                "auxiliary artifact differs from registered deployment resource")
        return value
    require(isinstance(value, dict) and set(value) == {"record_id", "status_id", "revision", "artifact", "run_id", "attempt", "job_id", "job_name", "workflow", "execution"}, "invalid deployment correlation proof")
    require(all(type(value[key]) is int and value[key] > 0 for key in ("record_id", "status_id", "run_id", "attempt", "job_id"))
            and isinstance(value["revision"], str) and policy.SHA.fullmatch(value["revision"])
            and re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", value["artifact"])
            and all(isinstance(value[key], str) and value[key] for key in ("job_name", "workflow", "execution")), "invalid exact correlation identity")
    return value


def validate_observation(value):
    require(isinstance(value, dict) and set(value) == {"source", "scope", "kind", "label", "source_id", "revision", "time", "conclusion", "proof"}
            and value["source"] == "cloud", "invalid cloud observation variant")
    require(isinstance(value["scope"], str) and SCOPE.fullmatch(value["scope"]) and value["kind"] in {"job", "deployment"}
            and isinstance(value["label"], str) and 0 < len(value["label"]) <= 500, "invalid cloud observation scope/display")
    policy.instant(value["time"])
    proof = value["proof"]
    require(isinstance(proof, dict) and set(proof) == {"schema", "fact", "correlation"} and proof["schema"] == SCHEMA, "invalid cloud proof schema")
    fact = validate_fact(proof["fact"])
    require(value["source_id"] == fact["source_id"] and value["revision"] == revision(fact)
            and value["time"] == seconds(fact["observed_at"]), "cloud observation differs from canonical proof")
    correlated = proof["correlation"] is not None
    if correlated:
        correlation = validate_correlation(proof["correlation"])
        require(correlation["artifact"] in fact["execution"]["images"]
                and correlation["execution"] == fact["execution"]["name"], "correlation differs from source")
    expected_scope = "deployment:" + fact["job"] if value["kind"] == "deployment" else source_scope(fact)
    identity_recovery = (recoverable(fact) and value["kind"] == "job" and value["scope"] == "cloud:identity:" + cloud.digest({
        "asset": fact["fact"]["subject"]["asset_slug"], "release": fact["fact"]["subject"]["release"]}))
    require((value["scope"] == expected_scope or identity_recovery) and (value["kind"] != "deployment" or correlated
            and proof["correlation"].get("kind") != "auxiliary_artifact" and domain(fact) is None), "cloud scope lacks exact provenance")
    require(value["conclusion"] == ("success" if recoverable(fact) or observational_success(fact, proof["correlation"]) else "failure"),
            "cloud outcome differs from canonical proof")
    operational(value)
    return value


def binding(value):
    correlation = value["proof"]["correlation"]
    rev = correlation["revision"] if value["kind"] == "deployment" else value["revision"]
    return policy.OperationBinding(value["scope"], value["scope"].removeprefix("deployment:"), rev, None)


def operational(value):
    fact = value["proof"]["fact"]
    event = fact.get("fact", {})
    family = domain(fact)
    reason, phase, mutation = "unknown", "verification", "uncertain"
    action = "Inspect the exact execution and canonical run records; establish prior writes before authorizing recovery."
    data = "Unknown; an execution outcome alone does not verify canonical data."
    impact = "Impact is unknown; inspect authoritative execution and publication evidence."
    auxiliary = value["proof"]["correlation"] is not None and value["proof"]["correlation"].get("kind") == "auxiliary_artifact"
    if provenance_gap(fact, value["proof"]["correlation"]):
        reason = "artifact_provenance_unverified"
        action = "Resolve the execution image/UID or dispatch provenance gap; inspect writes before any replay."
    elif auxiliary and value["conclusion"] == "success":
        reason, mutation = "healthy", "not_required"
        data = "Only execution-observation health is verified; canonical dataset and deployment outcomes remain separate."
        impact = "The read-only execution observer completed successfully."
        action = "No action for observer health; retain any independent data or deployment incident."
    elif auxiliary and event.get("kind") == "job_outcome" and event.get("status") == "failed" and event.get("details", {}).get("failure_reason"):
        reason, phase, mutation = event["details"]["failure_reason"], "read", "not_started"
        data = "The observation read failed before status-document writes; canonical data is outside this observer's authority."
        impact = "WDPA execution visibility is delayed; this does not establish a dataset failure or recovery."
        action = "Inspect observer read credentials/configuration if scheduled safe read recovery does not succeed."
    elif value["conclusion"] == "success":
        reason, mutation = "healthy", "completed"
        data = "Canonical asset/release publication is verified by an exact-generation successful run record."
        action = "No action for this verified asset publication."
    elif family == "identity":
        reason, phase, mutation = "identity_decision_required", "decision", "not_started"
        data = "Existing published data remains unchanged; this release awaits an identity decision."
        impact = "The named asset/release is paused; existing published releases remain available."
        action = "Review the named asset/release identity cases, record an approved decision, and verify the resumed publication."
    elif family == "translation":
        reason, phase, mutation = "translation_maintenance_required", "notification", "not_required"
        data = "Translation publication/delivery is separate from canonical dataset publication; inspect the referenced manifest."
        action = ("Inspect the existing translation delivery claim; establish delivery status without resending the notice."
                  if event["status"] in {"already_claimed", "delivery_unknown"} else "Review translation maintenance for the named asset/release using its existing receipt; do not replay publication.")
    elif fact["kind"] == "execution_request" and fact["reason"] == "permission_denied":
        reason = "artifact_provenance_unverified"  # No execution/artifact can be established.
    return policy.OperationalObservation.from_dict({**binding(value).to_dict(), "source_id": fact["source_id"], "observed_at": value["time"],
        "phase": phase, "reason": reason, "mutation_status": mutation,
        "recovery": "read_only_retry" if auxiliary and reason == "transient_transport" else "none", "required_action": action,
        "impact": impact, "published_data_state": data, "evidence": [link(fact)]})


def alias(value):
    correlation = value["proof"]["correlation"]
    if value["kind"] == "deployment":
        return f"github:{correlation['run_id']}:{correlation['attempt']}"
    fact = value["proof"]["fact"]
    return "cloud:" + cloud.digest({"resource": fact["resource_type"], "job": fact["job"], "execution": fact.get("execution", {}).get("name") or fact["source_id"]})


def covers(failure, evidence):
    """Exact target and immutable receipts; never infer Git ancestry from images."""
    if failure["scope"] != evidence["scope"]:
        return False
    old, new = failure["proof"]["fact"], evidence["proof"]["fact"]
    if observational_success(new, evidence["proof"]["correlation"]):
        prior = failure["proof"]["correlation"]
        return bool(prior is not None and prior.get("kind") == "auxiliary_artifact" and old["job"] == new["job"]
                    and prior["resource"] == evidence["proof"]["correlation"]["resource"]
                    and new["execution"].get("create_time") is not None
                    and cloud.instant(new["execution"]["create_time"]) > cloud.instant(old["observed_at"])
                    and cloud.instant(new["execution"]["completion_time"]) > cloud.instant(old["observed_at"])
                    )
    if not recoverable(new):
        return False
    if (cloud.instant(new["observed_at"]) <= cloud.instant(old["observed_at"])
            or cloud.instant(new["run_receipt"]["created_at"]) <= cloud.instant(old["observed_at"])):
        return False
    if domain(old) == "translation":
        return False
    if domain(old) == "identity":
        return new["run_receipt"]["status"] == "success" and domain_subject(new) == domain_subject(old)
    # Unknown old mutation is closed only within that same execution. A later
    # unrelated healthy run cannot explain what the failed operation wrote.
    old_subject = old.get("fact", {}).get("subject", {})
    return bool(set(old_subject) >= {"asset_slug", "release"} and old_subject == new["fact"]["subject"]
                and old.get("execution", {}).get("uid") and old["execution"]["uid"] == new["execution"]["uid"]
                and old["execution"]["name"] == new["execution"]["name"] and old["execution"]["images"] == new["execution"]["images"])


def legacy_delivery_covers(observation, report):
    """Suppress only a duplicate new bot broadcast, never assert recovery/ACK."""
    fact = observation["proof"]["fact"]
    if (domain(fact) != "translation" or domain(report) != "translation" or provenance_gap(fact) or provenance_gap(report)
            or fact["job"] != report["job"] or revision(fact) != revision(report)
            or domain_subject(fact) != domain_subject(report)):
        return False
    event, delivered = fact["fact"], report["fact"]
    generation = event["details"].get("manifest_generation")
    receipt = report.get("receipt_metadata")
    return bool(event["status"] in {"required", "already_claimed"} and type(generation) is int and generation > 0
                and delivered["details"].get("manifest_generation") == generation
                and event["subject"].get("manifest_uri") == delivered["subject"].get("manifest_uri")
                and event["subject"].get("manifest_uri") is not None
                and (delivered["status"] == "delivered" or delivered["details"].get("legacy_delivery_state") == "delivered")
                and type(delivered["details"].get("receipt_generation")) is int and delivered["details"]["receipt_generation"] > 0
                and receipt and str(receipt.get("generation")) == str(delivered["details"]["receipt_generation"]))
