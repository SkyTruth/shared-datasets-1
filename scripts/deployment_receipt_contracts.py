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
NOTIFICATION_WORKFLOW = "unattended-workflow-alert.yml"
NOTIFICATION_PERMISSIONS = {
    "contents": "read", "actions": "read", "deployments": "write",
    "attestations": "write", "id-token": "write",
}
NOTIFICATION_ATTEST = "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"


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
    runs = {
        "uv sync --locked --no-dev": None,
        "python scripts/install_deployment_verifier.py": None,
        'uv run --no-sync python scripts/slack_incidents.py prepare --work-dir "$RUNNER_TEMP/slack-incidents"': "prepare",
        'uv run --no-sync python scripts/slack_incidents.py deliver --work-dir "$RUNNER_TEMP/slack-incidents"': "deliver",
        'uv run --no-sync python scripts/slack_incidents.py verify --work-dir "$RUNNER_TEMP/slack-incidents"': None,
    }
    actions = {
        "actions/checkout@v4": {"ref": "${{ github.workflow_sha }}", "fetch-depth": 0, "persist-credentials": False},
        "actions/setup-python@v5": {"python-version": "3.12.12"},
        "astral-sh/setup-uv@v6": {"version": "0.11.8", "enable-cache": False},
    }
    environment = {
        "GH_TOKEN": "${{ github.token }}",
        "SHARED_DATASETS_SLACK_BOT_TOKEN": "${{ secrets.SHARED_DATASETS_SLACK_BOT_TOKEN }}",
        "SHARED_DATASETS_SLACK_CHANNEL_ID": "${{ vars.SHARED_DATASETS_SLACK_CHANNEL_ID }}",
        "SHARED_DATASETS_SLACK_INCIDENTS_SINCE": "${{ vars.SHARED_DATASETS_SLACK_INCIDENTS_SINCE }}",
    }
    command_environments = {
        "prepare": environment,
        "deliver": {key: value for key, value in environment.items() if not key.endswith("_SINCE")},
        "verify": {"GH_TOKEN": environment["GH_TOKEN"]},
    }
    if [step.get("uses") for step in steps[1:4]] != list(actions):
        return False
    seen_runs, seen_actions, signers = [], [], {}
    for step in steps[1:]:
        if not enabled(step) or set(step) - {"name", "id", "if", "uses", "with", "run", "env"}:
            return False
        if any(environment.get(key) != value for key, value in step.get("env", {}).items()):
            return False
        if "run" in step:
            run = step["run"].strip()
            if "uses" in step or run not in runs or step.get("id") != runs[run]:
                return False
            phase = next((name for name in command_environments if f"slack_incidents.py {name} " in run), None)
            if step.get("env", {}) != command_environments.get(phase, {}):
                return False
            if phase not in {"deliver", "verify"} and "if" in step:
                return False
            seen_runs.append(run)
        elif step.get("uses") == NOTIFICATION_ATTEST:
            identifier = step.get("id")
            if identifier not in {"attest-claims", "attest-outcomes"} or identifier in signers:
                return False
            phase = "claims" if identifier == "attest-claims" else "outcomes"
            if step.get("with") != {"subject-path": "${{ runner.temp }}/slack-incidents/" + phase + "/*.json", "create-storage-record": False}:
                return False
            signers[identifier] = step
        else:
            action = step.get("uses")
            if action not in actions or step.get("with") != actions[action] or step.get("env") or "if" in step or "id" in step:
                return False
            seen_actions.append(action)
    if seen_actions != list(actions) or seen_runs != list(runs) or set(signers) != {"attest-claims", "attest-outcomes"}:
        return False
    by_id = {step.get("id"): step for step in steps if step.get("id")}
    delivery = by_id["deliver"]
    claims = signers["attest-claims"]
    outcomes = signers["attest-outcomes"]
    verify = steps[-1]
    expected_conditions = (
        (claims, "${{ always() && steps.prepare.outputs.claims == 'true' }}"),
        (delivery, "${{ always() && steps.prepare.outputs.claims == 'true' && steps.attest-claims.outcome == 'success' }}"),
        (outcomes, "${{ always() && steps.deliver.outputs.outcomes == 'true' }}"),
        (verify, "${{ always() && (steps.attest-claims.outcome == 'success' || steps.attest-outcomes.outcome == 'success') }}"),
    )
    if any(step.get("if") != condition for step, condition in expected_conditions):
        return False
    return steps.index(by_id["prepare"]) < steps.index(claims) < steps.index(delivery) < steps.index(outcomes) < len(steps) - 1


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
            notification = notification_boundary(path.name, identifier, value, job)
            if not (protected or notification or caller and (main_only or "pull_request" not in trigger and "pull_request_target" not in trigger)):
                errors.append(f"{path.name}/{identifier}: deployment/signing write token requires a protected production job or main-only reusable caller")
            if any(event in trigger for event in ("pull_request", "pull_request_target")) and not (main_only and caller):
                errors.append(f"{path.name}/{identifier}: PR-capable validation must not receive deployment/signing write tokens")
    return errors
