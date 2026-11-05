"""Offline workflow boundaries for protected deployment receipts and tokens."""
from __future__ import annotations

import re
import json

import yaml
from scripts.ingestion_targets import TARGETS

ACTION = "./.github/actions/deployment-receipt"
BOOTSTRAP = "./.github/actions/deployment-bootstrap"
WORKFLOWS = (
    "ingestion-deploy.yml",
    "prod-terraform-target-apply.yml", *(config["workflow"] for config in TARGETS.values()),
    "dataset-usage-deploy.yml", "pmtiles-cdn-sync.yml",
    "catalog-viewer-deploy.yml", "catalog-web-deploy.yml", "publish-typescript-sdk.yml",
    "deployment-verification.yml", "deployment-recovery.yml",
)
PROTECTED = "shared-datasets-production"
NOTIFICATION_WORKFLOW = "unattended-workflow-alert.yml"
NOTIFICATION_PERMISSIONS = {
    "contents": "read", "actions": "read", "deployments": "write",
    "attestations": "write", "id-token": "write",
}
NOTIFICATION_ATTEST = "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"


def expanded_steps(root, job):
    for step in job.get("steps", []):
        if step.get("uses") != BOOTSTRAP:
            yield step
            continue
        action = yaml.safe_load((root / BOOTSTRAP.removeprefix("./") / "action.yml").read_text())
        encoded = json.dumps(action["runs"]["steps"])
        for key, value in step.get("with", {}).items():
            encoded = encoded.replace("${{ inputs." + key + " }}", value)
        for child in json.loads(encoded):
            if "if" in step:
                child["if"] = step["if"]
            yield child


def top_level_conjuncts(expression):
    """Find mandatory conditions without accepting a top-level OR bypass."""
    text = str(expression).strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    terms, start, depth, quote, index = [], 0, 0, None, 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return []
        elif depth == 0 and text[index:index + 2] == "||":
            return []
        elif depth == 0 and text[index:index + 2] == "&&":
            terms.append(text[start:index].strip())
            index += 1
            start = index + 1
        index += 1
    return [] if quote or depth else terms + [text[start:].strip()]


def notification_boundary(filename, identifier, workflow, job):
    """Admit one trusted-main notification ledger, never a production writer."""
    if filename != NOTIFICATION_WORKFLOW or identifier != "incidents":
        return False
    trigger = workflow.get("on", workflow.get(True, {}))
    if not isinstance(trigger, dict) or set(trigger) != {"workflow_run", "workflow_dispatch"}:
        return False
    upstream = trigger["workflow_run"]
    if not isinstance(upstream, dict) or upstream.get("branches") != ["main"] or upstream.get("types") != ["completed"]:
        return False
    terms = top_level_conjuncts(job.get("if", ""))
    if "github.ref == 'refs/heads/main'" not in terms or "vars.SHARED_DATASETS_SLACK_INCIDENTS_ENABLED == 'true'" not in terms:
        return False
    if workflow.get("permissions") != {"contents": "read", "actions": "read"} or workflow.get("env") or "defaults" in workflow:
        return False
    if job.get("permissions") != NOTIFICATION_PERMISSIONS or any(key in job for key in ("environment", "uses", "env", "defaults", "container", "services", "strategy")):
        return False
    if job.get("runs-on") != "ubuntu-latest":
        return False
    if job.get("concurrency") != {"group": "slack-incidents-${{ github.repository }}", "queue": "max", "cancel-in-progress": False}:
        return False
    steps = job.get("steps", [])
    guard = '''set -euo pipefail
if [[ "${GITHUB_REF}" != "refs/heads/main" ||
      "${GITHUB_WORKFLOW_REF}" != "${GITHUB_REPOSITORY}/.github/workflows/unattended-workflow-alert.yml@refs/heads/main" ]]; then
  echo "Incident notifications require this workflow from main." >&2
  exit 1
fi'''
    if not steps or set(steps[0]) != {"name", "run"} or re.sub(r"\s+", " ", steps[0]["run"].strip()) != re.sub(r"\s+", " ", guard):
        return False
    environment = {
        "GH_TOKEN": "${{ github.token }}",
        "SHARED_DATASETS_SLACK_BOT_TOKEN": "${{ secrets.SHARED_DATASETS_SLACK_BOT_TOKEN }}",
        "SHARED_DATASETS_SLACK_CHANNEL_ID": "${{ vars.SHARED_DATASETS_SLACK_CHANNEL_ID }}",
        "SHARED_DATASETS_SLACK_INCIDENTS_SINCE": "${{ vars.SHARED_DATASETS_SLACK_INCIDENTS_SINCE }}",
    }
    command = 'uv run --no-sync python scripts/slack_incidents.py {} --work-dir "$RUNNER_TEMP/slack-incidents"'
    # One ordered declaration replaces parallel command, action, phase, signer
    # and artifact tables. Names are presentation; every authority field is exact.
    expected = [
        {"uses": "actions/checkout@v4", "with": {"ref": "${{ github.workflow_sha }}", "fetch-depth": 0, "persist-credentials": False}},
        {"uses": "actions/setup-python@v5", "with": {"python-version": "3.12.12"}},
        {"uses": "astral-sh/setup-uv@v6", "with": {"version": "0.11.8", "enable-cache": False}},
        {"run": "uv sync --locked --no-dev"},
        {"run": "python scripts/install_deployment_verifier.py"},
        {"id": "prepare", "env": environment, "run": command.format("prepare")},
        {"id": "attest-claims", "if": "${{ always() && steps.prepare.outputs.claims == 'true' }}",
         "uses": NOTIFICATION_ATTEST, "with": {"subject-path": "${{ runner.temp }}/slack-incidents/claims/*.json", "create-storage-record": False}},
        {"id": "deliver", "if": "${{ always() && steps.prepare.outputs.claims == 'true' && steps.attest-claims.outcome == 'success' }}",
         "env": {key: value for key, value in environment.items() if not key.endswith("_SINCE")}, "run": command.format("deliver")},
        {"id": "attest-outcomes", "if": "${{ always() && steps.deliver.outputs.outcomes == 'true' }}",
         "uses": NOTIFICATION_ATTEST, "with": {"subject-path": "${{ runner.temp }}/slack-incidents/outcomes/*.json", "create-storage-record": False}},
        {"if": "${{ always() && (steps.attest-claims.outcome == 'success' || steps.attest-outcomes.outcome == 'success') }}",
         "env": {"GH_TOKEN": environment["GH_TOKEN"]}, "run": command.format("verify")},
        {"if": "${{ success() && steps.prepare.outputs.verification_cache == 'true' }}", "uses": "actions/upload-artifact@v4",
         "with": {"name": "notification-verification-${{ github.run_id }}-${{ github.run_attempt }}",
                  "path": "${{ runner.temp }}/slack-incidents/cache.json", "if-no-files-found": "error", "retention-days": 1}},
    ]
    actual = [{key: value.strip() if key == "run" else value for key, value in step.items() if key != "name"}
              for step in steps[1:]]
    return actual == expected



def enabled(step):
    return str(step.get("if", "")).strip().casefold() not in {"false", "${{ false }}"}


def command(step, verb):
    run = step.get("run", "").replace("\\\n", " ")
    return (re.search(r"deployment_revision\.py\s+" + verb + r"(?:\s|$)", run) is not None
            or re.search(r"args=\(" + verb + r"\s", run) is not None
            and 'python scripts/deployment_revision.py "${args[@]}"' in run)


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


def sdk_candidate_read_only(workflow):
    """The recoverable candidate job cannot acquire publication authority."""
    candidate = workflow["jobs"].get("candidate", {})
    effective = candidate.get("permissions", workflow.get("permissions", {}))
    if (not candidate.get("steps") or "uses" in candidate or not isinstance(effective, dict)
            or any(value not in {"read", "none"} for value in effective.values())):
        return ["SDK candidate must remain a read-only job"]
    checks = [step for step in candidate["steps"] if step.get("id") == "candidate"]
    if (len(checks) != 1 or "python scripts/sdk_release_authorization.py " not in checks[0].get("run", "")
            or "--admit-source" in checks[0]["run"] or not enabled(checks[0])):
        return ["SDK candidate must retain its observable package verification step"]
    for identifier, job in workflow["jobs"].items():
        if identifier == "publish":
            continue
        for step in job.get("steps", []):
            if any(mutations(step)) or command(step, "(?:start|finish|observe|reconcile)"):
                return ["SDK publication and deployment writes must remain confined to publish"]
    return []


def ingestion_boundary(workflow):
    """Carry the signed claim across apply -> verify without hiding failed apply."""
    jobs = workflow["jobs"]
    if set(jobs) != {"prepare", "apply", "verify"}:
        return ["ingestion-deploy.yml: exact prepare/apply/verify graph required"]
    prepare, apply, verify = (jobs[key] for key in ("prepare", "apply", "verify"))
    errors = []
    for job, condition in ((prepare, "steps.replay.outputs.proceed == 'true' && steps.window.outputs.ready == 'true'"),
                           (apply, "steps.window.outputs.ready == 'true'")):
        rehearsals = [step for step in job.get("steps", []) if step.get("uses") == ACTION and step.get("with", {}).get("mode") == "rehearsal"]
        if len(rehearsals) != 1 or rehearsals[0].get("if") != condition or rehearsals[0].get("continue-on-error"):
            errors.append("ingestion-deploy.yml: exact admitted rehearsal must gate staging and claim creation")
    if (apply.get("needs") != "prepare" or apply.get("if") != "needs.prepare.outputs.ready == 'true'"
            or prepare.get("outputs", {}).get("ready") != "${{ steps.replay.outputs.proceed == 'true' && steps.window.outputs.ready == 'true' }}"):
        errors.append("ingestion-deploy.yml: apply must require prepared replay and runtime admission")
    if apply.get("outputs") != {"deployment_id": "${{ steps.revision.outputs.deployment_id }}", "claim_signed": "${{ steps.claim-receipt.outcome }}"}:
        errors.append("ingestion-deploy.yml: apply must export the exact claim and signature outcome")
    applies = [step for step in apply.get("steps", []) if mutations(step)[1]]
    if len(applies) != 1 or applies[0].get("if") != "steps.revision.outputs.proceed == 'true' && steps.claim-receipt.outcome == 'success'":
        errors.append("ingestion-deploy.yml: saved-plan apply requires the exact signed claim barrier")
    return errors + verification_boundary(workflow, "ingestion-deploy.yml", "apply", ["prepare", "apply"])


def verification_boundary(workflow, filename, parent, needs):
    errors = []
    verify = workflow["jobs"].get("verify", {})
    if verify.get("needs") != needs or verify.get("if") != "${{ always() && needs." + parent + ".outputs.deployment_id != '' }}":
        errors.append(f"{filename}: verify must record every claimed apply outcome, including failure")
    admission = {
        "APPLY_RESULT": "${{ needs." + parent + ".result }}", "CLAIM_SIGNED": "${{ needs." + parent + ".outputs.claim_signed }}",
    }
    guards = [step for step in verify.get("steps", []) if admission.items() <= step.get("env", {}).items()]
    if len(guards) != 1 or "run" not in guards[0] or "if" in guards[0] or guards[0].get("continue-on-error"):
        errors.append(f"{filename}: canary requires successful apply and exact signed claim")
    else:
        steps = verify["steps"]
        if any(mutations(step)[1] for step in steps[:steps.index(guards[0])]):
            errors.append(f"{filename}: signed apply admission must precede canary")
    outcomes = [step for step in verify.get("steps", []) if command(step, "finish")]
    if (len(outcomes) != 1 or outcomes[0].get("if") != "${{ always() }}"
            or not {"GH_TOKEN": "${{ github.token }}", "DEPLOYMENT_ID": "${{ needs." + parent + ".outputs.deployment_id }}", "JOB_STATUS": "${{ job.status }}"}.items() <= outcomes[0].get("env", {}).items()):
        errors.append(f"{filename}: every claimed terminal outcome must be recorded even after failed apply")
    return errors


def terraform_boundary(workflow):
    """Keep preparation/waiting outside the one serialized mutation boundary."""
    jobs = workflow['jobs']
    if set(jobs) != {'prepare', 'sync', 'propagation'}:
        return ['Terraform executor requires preparation, serialized apply and propagation jobs']
    prepare, sync, propagation = (jobs[key] for key in ('prepare', 'sync', 'propagation'))
    if (any(jobs[key].get('environment') != PROTECTED for key in ('prepare', 'sync'))
            or [key for key, job in jobs.items() if 'concurrency' in job] != ['sync']
            or sync['concurrency'] != {'group': 'prod-terraform-state', 'queue': 'max', 'cancel-in-progress': False}
            or sync.get('needs') != 'prepare' or sync.get('if') != "needs.prepare.outputs.proceed == 'true'"):
        return ['Terraform preparation must gate the protected serialized apply']
    if (propagation.get('needs') != 'sync' or propagation.get('permissions') != {}
            or 'environment' in propagation
            or propagation.get('if') != "${{ needs.sync.outputs.applied == 'true' && fromJSON(needs.sync.outputs.wait_seconds) > 0 }}"
            or sync.get('outputs', {}).get('applied') != "${{ steps.revision.outputs.proceed == 'true' && steps.claim-receipt.outcome == 'success' }}"):
        return ['Terraform propagation requires successful signed apply without production authority']
    return []


def boundaries(root):
    errors = []
    for filename in WORKFLOWS:
        value = yaml.safe_load((root / ".github/workflows" / filename).read_text())
        if filename == "publish-typescript-sdk.yml":
            errors.extend(sdk_candidate_read_only(value))
        if filename == "ingestion-deploy.yml":
            errors.extend(ingestion_boundary(value))
        if filename == "dataset-usage-deploy.yml":
            errors.extend(verification_boundary(value, filename, "deploy", "deploy"))
            outputs = value["jobs"].get("deploy", {}).get("outputs", {})
            if any(outputs.get(key) != expected for key, expected in {
                "deployment_id": "${{ steps.revision.outputs.deployment_id }}", "claim_signed": "${{ steps.claim-receipt.outcome }}",
                "image": "${{ steps.image.outputs.image }}",
            }.items()):
                errors.append(f"{filename}: verify must inherit the exact signed claim and image")
        for job_id, job in value["jobs"].items():
            steps = job.get("steps", [])
            inherited = filename in {"ingestion-deploy.yml", "dataset-usage-deploy.yml"} and job_id == "verify"
            rehearsed, claim, barrier = inherited, None, None
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
                    if inherited:
                        if "always()" in condition:
                            errors.append(f"{filename}/{step.get('name')}: canary cannot bypass signed apply admission")
                    elif not barrier or ("always()" in condition and f"steps.{barrier}.outcome == 'success'" not in condition):
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
            notification = notification_boundary(path.name, identifier, value, job)
            if not (protected or notification or caller and (main_only or "pull_request" not in trigger and "pull_request_target" not in trigger)):
                errors.append(f"{path.name}/{identifier}: deployment/signing write token requires a protected production job or main-only reusable caller")
            if any(event in trigger for event in ("pull_request", "pull_request_target")) and not (main_only and caller):
                errors.append(f"{path.name}/{identifier}: PR-capable validation must not receive deployment/signing write tokens")
    return errors
