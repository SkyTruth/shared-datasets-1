"""Offline workflow boundaries for protected deployment receipts and tokens."""
from __future__ import annotations

import re

import yaml

ACTION = "./.github/actions/deployment-receipt"
WORKFLOWS = (
    "prod-terraform-target-apply.yml", "wdpa-monthly-deploy.yml", "eamlis-monthly-deploy.yml",
    "sea-ice-daily-deploy.yml", "wdpa-processing-validation-deploy.yml", "pmtiles-cdn-sync.yml",
    "catalog-viewer-deploy.yml", "catalog-web-deploy.yml", "publish-typescript-sdk.yml",
    "deployment-verification.yml", "deployment-recovery.yml",
)
PROTECTED = "shared-datasets-production"


def enabled(step):
    return str(step.get("if", "")).strip().casefold() not in {"false", "${{ false }}"}


def command(step, verb):
    run = step.get("run", "").replace("\\\n", " ")
    return re.search(r"deployment_revision\.py\s+" + verb + r"(?:\s|$)", run) is not None


def mutations(step):
    run = step.get("run", "").replace("\\\n", " ")
    staging = re.search(r"\bdocker\s+(push|pull)\s", run) is not None
    deployment = any(re.search(pattern, run) for pattern in (
        r"(?:terraform_retry\.sh|\bterraform\b).*\sapply(?:\s|$)",
        r"\bgcloud\s+run\s+jobs\s+execute\s", r"\bnpm\s+publish\s",
        r"\bscripts/catalog_web_publish\.py\s",
    ))
    return staging, deployment


def receipt_link(step, identifier):
    return step.get("uses") == ACTION and step.get("with", {}).get("mode") == "receipt" and step["with"].get("receipt-path") == "${{ steps." + identifier + ".outputs.receipt_path }}" and enabled(step)


def sdk_boundary(filename, identifier, job):
    """Retain the SDK's existing npm OIDC publisher authorization contract."""
    if filename != "publish-typescript-sdk.yml" or identifier != "publish":
        return False
    steps = job.get("steps", [])
    if len(steps) < 3:
        return False
    guard = steps[0].get("run", "")
    bootstrap = steps[1]
    if not all(item in guard for item in ('if [[ "$GITHUB_REF" != refs/heads/main ]]', "exit 1", "$GITHUB_WORKFLOW_REF", "publish-typescript-sdk.yml@refs/heads/main")) or not str(bootstrap.get("uses", "")).startswith("actions/checkout@") or bootstrap.get("with", {}).get("ref") != "${{ github.workflow_sha }}":
        return False
    for step in steps[2:]:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            return False  # Candidate code cannot replace the trusted verifier.
        if "deployment_revision.py verify --bootstrap --workflow publish-typescript-sdk.yml" in step.get("run", "").replace("\\\n", " "):
            return enabled(step)
    return False


def boundaries(root):
    errors = []
    for filename in WORKFLOWS:
        value = yaml.safe_load((root / ".github/workflows" / filename).read_text())
        for job_id, job in value["jobs"].items():
            steps = job.get("steps", [])
            rehearsed, claim, barrier = False, None, None
            outputs = []
            for index, step in enumerate(steps):
                if step.get("uses") == ACTION and step.get("with", {}).get("mode") == "rehearsal" and enabled(step):
                    if step["with"].get("signer-workflow") != filename:
                        errors.append(f"{filename}/{job_id}: rehearsal must bind the exact leaf signer")
                    else:
                        rehearsed = True
                if command(step, "start"):
                    if not rehearsed:
                        errors.append(f"{filename}/{step.get('name')}: signed rehearsal must precede claim creation")
                    claim, barrier = step.get("id"), None
                    if not claim:
                        errors.append(f"{filename}/{step.get('name')}: claim requires an output ID")
                if claim and receipt_link(step, claim):
                    barrier = step.get("id")
                    if not barrier:
                        errors.append(f"{filename}/{step.get('name')}: signed claim requires a barrier ID")
                staging, deployment = mutations(step)
                if (staging or deployment) and not rehearsed:
                    errors.append(f"{filename}/{step.get('name')}: signed rehearsal must precede artifact and production operations")
                if deployment:
                    condition = str(step.get("if", ""))
                    if not barrier or ("always()" in condition and f"steps.{barrier}.outcome == 'success'" not in condition):
                        errors.append(f"{filename}/{step.get('name')}: verified signed claim must gate production mutation, including always recovery")
                if command(step, "(?:finish|observe|reconcile)"):
                    outputs.append((index, step))
            for index, output in outputs:
                identifier = output.get("id")
                following = [step for step in steps[index + 1:] if identifier and receipt_link(step, identifier)]
                if not following or not any("always()" in str(step.get("if", "")) for step in following):
                    errors.append(f"{filename}/{output.get('name')}: every outcome needs always-run signing of its exact receipt output")
    return errors


def permissions(root):
    """Reviewed validation cannot receive production record/signing write tokens.

    This checks repository declarations. It cannot revoke a malicious contributor's
    independently configured GitHub authority or prevent administrator deletion.
    """
    errors = []
    for path in sorted((root / ".github/workflows").glob("*.yml")):
        value = yaml.safe_load(path.read_text())
        trigger = value.get("on", value.get(True, {}))
        if isinstance(trigger, str):
            trigger = {trigger: {}}
        defaults = value.get("permissions", {})
        for identifier, job in value.get("jobs", {}).items():
            effective = job.get("permissions", defaults)
            writes = effective == "write-all" or isinstance(effective, dict) and any(effective.get(key) == "write" for key in ("deployments", "attestations"))
            if not writes:
                continue
            protected = job.get("environment") == PROTECTED or isinstance(job.get("environment"), dict) and job["environment"].get("name") == PROTECTED or sdk_boundary(path.name, identifier, job)
            caller = str(job.get("uses", "")).startswith("./.github/workflows/")
            condition = str(job.get("if", ""))
            main_only = "github.ref == 'refs/heads/main'" in condition and "github.event_name == 'push'" in condition
            if not (protected or caller and (main_only or "pull_request" not in trigger and "pull_request_target" not in trigger)):
                errors.append(f"{path.name}/{identifier}: deployment/signing write token requires a protected production job or main-only reusable caller")
            if any(event in trigger for event in ("pull_request", "pull_request_target")) and not (main_only and caller):
                errors.append(f"{path.name}/{identifier}: PR-capable validation must not receive deployment/signing write tokens")
    return errors
