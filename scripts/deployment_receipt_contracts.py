"""Offline workflow boundaries for protected deployment receipts and tokens."""
from __future__ import annotations

import re

import yaml

ACTION = "./.github/actions/deployment-receipt"
WORKFLOWS = (
    "monitoring-slack-relay-deploy.yml",
    "prod-terraform-target-apply.yml", "wdpa-monthly-deploy.yml", "mpatlas-monthly-deploy.yml", "eamlis-monthly-deploy.yml",
    "dataset-usage-deploy.yml", "sea-ice-daily-deploy.yml", "pmtiles-cdn-sync.yml",
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
    if not isinstance(trigger, dict) or set(trigger) != {"workflow_run", "workflow_dispatch", "schedule"}:
        return False
    if trigger["schedule"] != [{"cron": "3-59/5 * * * *"}] or set(workflow.get("jobs", {})) != {"incidents"}:
        return False
    inputs = trigger["workflow_dispatch"].get("inputs", {})
    if set(inputs) != {"action", "supervision_request", "supervision_request_id", "incident_id", "operation", "delivery_result", "message_ts"}:
        return False
    if (inputs["action"].get("type") != "choice" or inputs["action"].get("options") != ["reconcile-delivery", "supervision"]
            or inputs["action"].get("default") != "reconcile-delivery"
            or any(inputs[key].get("type") != "string" or inputs[key].get("required") is True for key in ("supervision_request", "supervision_request_id", "incident_id", "operation", "message_ts"))
            or inputs["delivery_result"].get("options") != ["none", "delivered", "not-delivered", "seal-checkpoint"]
            or inputs["delivery_result"].get("default") != "none"
            or inputs["delivery_result"].get("required") is True):
        return False
    if workflow.get("run-name") != "${{ inputs.action == 'supervision' && format('Supervision {0}', inputs.supervision_request_id) || 'Workflow health and Slack reconciliation' }}":
        return False
    upstream = trigger["workflow_run"]
    if not isinstance(upstream, dict) or upstream.get("branches") != ["main"] or upstream.get("types") != ["completed"]:
        return False
    terms = top_level_conjuncts(job.get("if", ""))
    if "github.ref == 'refs/heads/main'" not in terms or "vars.SHARED_DATASETS_SLACK_INCIDENTS_ENABLED == 'true'" not in terms:
        return False
    admission = 'vars.SHARED_DATASETS_SLACK_INCIDENTS_ENABLED == \'true\' && github.ref == \'refs/heads/main\' && (\n  github.event_name == \'workflow_dispatch\' ||\n  github.event_name == \'schedule\' ||\n  (\n    github.event.workflow_run.status == \'completed\' &&\n    contains(fromJSON(\'["success","failure","timed_out","cancelled","action_required","startup_failure","stale"]\'), github.event.workflow_run.conclusion) &&\n    (\n      github.event.workflow_run.conclusion != \'success\' ||\n      contains(fromJSON(\'["Deployment completion check","Deployment terminal verification","Deployment read-only reconciliation"]\'), github.event.workflow_run.name)\n    ) &&\n    github.event.workflow_run.head_branch == \'main\' &&\n    github.event.workflow_run.head_repository.full_name == github.repository &&\n    (\n      github.event.workflow_run.event == \'schedule\' ||\n      github.event.workflow_run.event == \'workflow_run\' ||\n      github.event.workflow_run.event == \'workflow_dispatch\' ||\n      (github.event.workflow_run.event == \'push\' && github.event.workflow_run.name == \'CI\')\n    )\n  )\n)'
    if job.get("if", "") != admission:
        return False
    if workflow.get("permissions") != {"contents": "read", "actions": "read"} or workflow.get("env") or "defaults" in workflow:
        return False
    if job.get("permissions") != NOTIFICATION_PERMISSIONS or any(key in job for key in ("environment", "uses", "env", "defaults", "container", "services", "strategy")):
        return False
    if job.get("runs-on") != "ubuntu-latest":
        return False
    if workflow.get("concurrency") != {
        "group": "slack-notification-${{ github.repository }}-${{ github.event_name == 'schedule' && 'scheduled-reconciliation' || github.run_id }}",
        "cancel-in-progress": False,
    }:
        return False  # Coalesce only unstarted ticks, preserving every control/source event.
    if job.get("concurrency") != {"group": "slack-incidents-${{ github.repository }}", "queue": "max", "cancel-in-progress": False}:
        return False
    steps = job.get("steps", [])
    guard = '''set -euo pipefail
if [[ "${GITHUB_REF}" != "refs/heads/main" ||
      "${GITHUB_WORKFLOW_REF}" != "${GITHUB_REPOSITORY}/.github/workflows/unattended-workflow-alert.yml@refs/heads/main" ]]; then
  echo "Execution must use this workflow from main." >&2
  exit 1
fi'''
    if not steps or set(steps[0]) != {"name", "run"} or re.sub(r"\s+", " ", steps[0]["run"].strip()) != re.sub(r"\s+", " ", guard):
        return False
    runs = {
        "uv sync --locked --no-dev": None,
        "python scripts/install_deployment_verifier.py": None,
        'uv run --no-sync python scripts/operational_reconcile.py prepare --work-dir "$RUNNER_TEMP/slack-incidents"': "prepare",
        'uv run --no-sync python scripts/operational_reconcile.py deliver --work-dir "$RUNNER_TEMP/slack-incidents"': "deliver",
        'uv run --no-sync python scripts/operational_reconcile.py verify --work-dir "$RUNNER_TEMP/slack-incidents"': "verify",
        'uv run --no-sync python scripts/operational_reconcile.py finalize-cycle --work-dir "$RUNNER_TEMP/slack-incidents"': "finalize-cycle",
        'uv run --no-sync python scripts/operational_reconcile.py verify-heartbeat --work-dir "$RUNNER_TEMP/slack-incidents"': None,
    }
    actions = {
        "actions/checkout@v4": {"ref": "${{ github.workflow_sha }}", "fetch-depth": 0, "persist-credentials": False},
        "actions/setup-python@v5": {"python-version": "3.12.12"},
        "astral-sh/setup-uv@v6": {"version": "0.11.8", "enable-cache": False},
        "google-github-actions/auth@v2": {
            "workload_identity_provider": "${{ vars.GCP_OPERATIONAL_OBSERVER_WORKLOAD_IDENTITY_PROVIDER }}",
            "service_account": "${{ vars.GCP_OPERATIONAL_OBSERVER_SERVICE_ACCOUNT }}",
            "create_credentials_file": True,
        },
    }
    environment = {
        "GH_TOKEN": "${{ github.token }}",
        "SHARED_DATASETS_SLACK_BOT_TOKEN": "${{ secrets.SHARED_DATASETS_SLACK_BOT_TOKEN }}",
        "SHARED_DATASETS_SLACK_CHANNEL_ID": "${{ vars.SHARED_DATASETS_SLACK_CHANNEL_ID }}",
        "SHARED_DATASETS_SLACK_INCIDENTS_SINCE": "${{ vars.SHARED_DATASETS_SLACK_INCIDENTS_SINCE }}",
        "SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED": "${{ vars.SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED }}",
    }
    command_environments = {
        "prepare": environment,
        "deliver": {key: environment[key] for key in ("GH_TOKEN", "SHARED_DATASETS_SLACK_BOT_TOKEN",
                    "SHARED_DATASETS_SLACK_CHANNEL_ID", "SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED")},
        "verify": {"GH_TOKEN": environment["GH_TOKEN"]},
        "finalize-cycle": {key: environment[key] for key in ("GH_TOKEN", "SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED")},
        "verify-heartbeat": {key: environment[key] for key in ("GH_TOKEN", "SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED")},
    }
    github_prepare_environment = {key: value for key, value in environment.items() if key != "SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED"}
    github_steps = [
        {"id": "github-prepare", "if": "${{ github.event_name == 'schedule' }}", "env": github_prepare_environment,
         "run": 'uv run --no-sync python scripts/operational_reconcile.py prepare-github --work-dir "$RUNNER_TEMP/github-incidents"'},
        {"id": "github-attest-claims", "if": "${{ always() && steps.github-prepare.outputs.claims == 'true' }}", "uses": NOTIFICATION_ATTEST,
         "with": {"subject-path": "${{ runner.temp }}/github-incidents/claims/*.json", "create-storage-record": False}},
        {"id": "github-deliver", "if": "${{ always() && steps.github-prepare.outputs.claims == 'true' && steps.github-attest-claims.outcome == 'success' }}",
         "env": {key: github_prepare_environment[key] for key in ("GH_TOKEN", "SHARED_DATASETS_SLACK_BOT_TOKEN", "SHARED_DATASETS_SLACK_CHANNEL_ID")},
         "run": 'uv run --no-sync python scripts/operational_reconcile.py deliver-github --work-dir "$RUNNER_TEMP/github-incidents"'},
        {"id": "github-attest-outcomes", "if": "${{ always() && steps.github-deliver.outputs.outcomes == 'true' }}", "uses": NOTIFICATION_ATTEST,
         "with": {"subject-path": "${{ runner.temp }}/github-incidents/outcomes/*.json", "create-storage-record": False}},
        {"id": "github-verify", "if": "${{ always() && github.event_name == 'schedule' && (steps.github-prepare.outcome == 'success' || steps.github-attest-claims.outcome == 'success' || steps.github-attest-outcomes.outcome == 'success') }}",
         "env": {"GH_TOKEN": environment["GH_TOKEN"]},
         "run": 'uv run --no-sync python scripts/operational_reconcile.py verify --work-dir "$RUNNER_TEMP/github-incidents"'},
    ]
    if [{key: value for key, value in step.items() if key != "name"} for step in steps[6:11]] != github_steps:
        return False  # Complete the signed GitHub phase before observer credentials exist.
    steps = steps[:6] + steps[11:]
    if [step.get("uses") for step in steps[1:4]] != list(actions)[:3]:
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
            phase = next((name for name in command_environments if f"operational_reconcile.py {name} " in run), None)
            if step.get("env", {}) != command_environments.get(phase, {}):
                return False
            if phase == "prepare":
                if step.get("if") != "${{ success() && (github.event_name != 'schedule' || (vars.SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED == 'true' && steps.github-verify.outcome == 'success')) }}":
                    return False
            elif phase not in {"deliver", "verify", "finalize-cycle", "verify-heartbeat"} and "if" in step:
                return False
            seen_runs.append(run)
        elif step.get("uses") == NOTIFICATION_ATTEST:
            identifier = step.get("id")
            if identifier not in {"attest-claims", "attest-outcomes", "attest-cycle"} or identifier in signers:
                return False
            phase = "outcomes" if identifier == "attest-outcomes" else "claims"
            if step.get("with") != {"subject-path": "${{ runner.temp }}/slack-incidents/" + phase + "/*.json", "create-storage-record": False}:
                return False
            signers[identifier] = step
        else:
            action = step.get("uses")
            if action not in actions or step.get("with") != actions[action] or step.get("env") or "id" in step:
                return False
            expected_if = "${{ success() && github.event_name == 'schedule' && vars.SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED == 'true' && steps.github-verify.outcome == 'success' }}" if action == "google-github-actions/auth@v2" else None
            if step.get("if") != expected_if:
                return False
            seen_actions.append(action)
    if seen_actions != list(actions) or seen_runs != list(runs) or set(signers) != {"attest-claims", "attest-outcomes", "attest-cycle"}:
        return False
    by_id = {step.get("id"): step for step in steps if step.get("id")}
    delivery = by_id["deliver"]
    claims = signers["attest-claims"]
    outcomes = signers["attest-outcomes"]
    verify = by_id["verify"]
    expected_conditions = (
        (claims, "${{ always() && steps.prepare.outputs.claims == 'true' }}"),
        (delivery, "${{ always() && steps.prepare.outputs.claims == 'true' && steps.attest-claims.outcome == 'success' }}"),
        (outcomes, "${{ always() && steps.deliver.outputs.outcomes == 'true' }}"),
        (verify, "${{ always() && (steps.attest-claims.outcome == 'success' || steps.attest-outcomes.outcome == 'success' || steps.prepare.outputs.cycle_proposed == 'true') }}"),
        (by_id["finalize-cycle"], "${{ success() && steps.prepare.outputs.cycle_proposed == 'true' && steps.verify.outcome == 'success' }}"),
        (signers["attest-cycle"], "${{ success() && steps.finalize-cycle.outputs.cycle_saved == 'true' }}"),
        (steps[-1], "${{ success() && steps.attest-cycle.outcome == 'success' }}"),
    )
    if any(step.get("if") != condition for step, condition in expected_conditions):
        return False
    return (steps.index(by_id["prepare"]) < steps.index(claims) < steps.index(delivery) < steps.index(outcomes)
            < steps.index(verify) < steps.index(by_id["finalize-cycle"]) < steps.index(signers["attest-cycle"]) < len(steps) - 1)


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


def sdk_candidate_read_only(workflow):
    """The recoverable candidate job cannot acquire publication authority."""
    candidate = workflow["jobs"].get("candidate", {})
    effective = candidate.get("permissions", workflow.get("permissions", {}))
    if (not candidate.get("steps") or "uses" in candidate or not isinstance(effective, dict)
            or any(value not in {"read", "none"} for value in effective.values())):
        return ["SDK candidate must remain a read-only job"]
    checks = [step for step in candidate["steps"] if step.get("id") == "candidate"]
    if (len(checks) != 1 or checks[0].get("name") != "Verify tested SDK candidate and reviewed version"
            or "python scripts/sdk_release_authorization.py " not in checks[0].get("run", "")
            or "--admit-source" in checks[0]["run"] or not enabled(checks[0])):
        return ["SDK candidate must retain its observable package verification step"]
    for identifier, job in workflow["jobs"].items():
        if identifier == "publish":
            continue
        for step in job.get("steps", []):
            if any(mutations(step)) or command(step, "(?:start|finish|observe|reconcile)"):
                return ["SDK publication and deployment writes must remain confined to publish"]
    return []


def boundaries(root):
    errors = []
    for filename in WORKFLOWS:
        value = yaml.safe_load((root / ".github/workflows" / filename).read_text())
        if filename == "publish-typescript-sdk.yml":
            errors.extend(sdk_candidate_read_only(value))
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
