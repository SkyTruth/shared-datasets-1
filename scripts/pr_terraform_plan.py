"""Read-only PR plans. Never retain plans or expose provider attribute values."""
from __future__ import annotations

import argparse
from functools import cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, unquote
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ci_toolchain import TOOLCHAIN
from scripts.deployment_permissions import checks
from scripts.ingestion_targets import DEPLOY_TARGETS
from scripts.terraform_plan_allowlist import blocked_changes
from scripts.terraform_plan_permissions import PROJECT, PROJECT_URL, plan_checks, probe_call_checks, require
from scripts.terraform_target_contracts import ROOT, address, plan_scope, registry

REPOSITORY = "SkyTruth/shared-datasets-1"
TF_COMMANDS = frozenset({"version", "init", "show", "plan"})


def scopes(plan, root=ROOT):
    """Resolve CI-selected executors and manual-only changed resource owners."""
    import yaml

    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]
    workflows = {Path(ci[name.replace("_", "-")]["uses"]).name for name in plan["deployments"]}
    changed = set(plan["changed_paths"] or [])
    declarations = set()
    for relative in changed:
        path = root / relative
        if relative.startswith("terraform/envs/prod/") and path.suffix == ".tf":
            sources = [path.read_text()] if path.is_file() else []
            if plan.get("base"):
                before = subprocess.run(["git", "show", plan["base"] + ":" + relative], cwd=root, text=True, capture_output=True)
                if before.returncode == 0:
                    sources.append(before.stdout)
            for source in sources:
                declarations.update(a + "." + b if kind == "resource" else "module." + a
                                    for kind, a, b in re.findall(r'^(resource|module) "([^"\n]+)"(?: "([^"\n]+)")?\s*{', source, re.M))
    result = []
    for name, item in registry(root).items():
        resources = item["resources"]
        if item["workflow"] in workflows or any(any(address(r) == d or address(r).startswith(d + ".") for d in declarations) for r in resources):
            result.append(dict(name=name, resources=resources, exact=set(resources), patterns=[],
                               variables=dict(row.split("=", 1) for row in item.get("tf_vars", [])),
                               required=[*checks(item["readiness_target"]), *(checks("monitoring-slack-secret") if item.get("prepare_monitoring_slack") else [])],
                               job_image=None, block_deletes=item.get("block_deletes", False), delete_only=item.get("delete_only", False)))
    for workflow, entries in registry(root, section="plan_scopes").items():
        if workflow not in workflows:
            continue
        for name, item in entries.items():
            # A retirement sub-scope is already contained in runtime; it is not
            # an independent deployment and must not restrict the normal plan.
            if name == "retirement":
                continue
            exact, patterns = plan_scope(workflow, name, root)
            resources = [*item.get("exact", []), *item.get("instances", []),
                         *(prefix + "." + child for prefix, child in item.get("modules", {}).items())]
            result.append(dict(name=workflow + "/" + name, resources=resources, exact=exact, patterns=patterns,
                               variables={}, required=[], job_image=None, block_deletes=False, delete_only=False))
    for name, config in DEPLOY_TARGETS.items():
        if config["workflow"] in workflows and config["targets"]:
            result.append(dict(name=name, resources=config["targets"], exact=set(config["targets"]), patterns=[],
                               variables={}, required=checks(name), job_image=(config["targets"][0], config["image_variable"]),
                               block_deletes=True, delete_only=False))
    return result


def selected(plan):
    paths = plan["changed_paths"]
    return paths is None or any(p.startswith("terraform/") and not p.endswith(".md")
                               or p == ".github/terraform-targets.json" for p in paths)


def same_repo(event):
    pr = event.get("pull_request", {})
    return (pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY
            and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY)


def terraform(command, *arguments, directory, environment=None):
    require(command in TF_COMMANDS, "PR Terraform command is not read-only")
    completed = subprocess.run(["terraform", "-chdir=" + str(directory), command, *arguments],
                               env=environment, text=True, capture_output=True, timeout=900)
    # Neither Terraform diagnostics nor CalledProcessError may escape: both can
    # contain state, provider responses and arbitrary attribute values.
    require(completed.returncode == 0, "PR Terraform " + command + " failed (attribute diagnostics withheld)")
    return completed.stdout


def deployed_variables(state):
    """Read existing immutable images and non-secret settings without state writes."""
    values = {}
    images = {c["targets"][0]: c["image_variable"] for c in DEPLOY_TARGETS.values() if c["targets"]}
    images.update({"module.dataset_usage_job.google_cloud_run_v2_job.this": "dataset_usage_image",
                   "google_cloud_run_v2_service.catalog_viewer": "catalog_viewer_image",
                   "google_cloud_run_v2_service.monitoring_slack": "monitoring_slack_image"})

    def walk(module):
        for row in module.get("resources", []):
            if row["address"] not in images:
                continue
            template = row["values"]["template"][0]
            task = template["template"][0] if row["type"] == "google_cloud_run_v2_job" else template
            container = task["containers"][0]
            image = container["image"]
            require(re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", image), "deployed image is not immutable")
            values[images[row["address"]]] = image
            for env in container.get("env", []):
                if env["name"] == "WDPA_BUILD_IMAGE_CONFIG_DIGEST":
                    values["wdpa_monthly_image_config_digest"] = env["value"]
                if row["address"] == "google_cloud_run_v2_service.monitoring_slack":
                    if env["name"] == "SHARED_DATASETS_SLACK_CHANNEL_ID":
                        values["monitoring_slack_channel_id"] = env["value"]
                    elif env["name"] == "SHARED_DATASETS_SLACK_BOT_TOKEN":
                        values["monitoring_slack_token_version"] = env["value_source"][0]["secret_key_ref"][0]["version"]
        for child in module.get("child_modules", []):
            walk(child)
    walk(state["values"]["root_module"])
    return values


def policy_endpoint(url):
    """Map existing plan_checks probes to read-only IAM policy endpoints."""
    parsed = urlsplit(url)
    base = url.split("?", 1)[0]
    # URL maps and WIF pools inherit project allow policy; their APIs expose
    # testIamPermissions but no resource getIamPolicy operation.
    if "/urlMaps/" in parsed.path or "/workloadIdentityPools/" in parsed.path:
        return PROJECT_URL.removesuffix(":testIamPermissions") + ":getIamPolicy", {"options": {"requestedPolicyVersion": 3}}
    if parsed.hostname == "storage.googleapis.com":
        base = base.removesuffix("/testPermissions")
        return base + "?optionsRequestedPolicyVersion=3", None
    if parsed.hostname == "compute.googleapis.com":
        return base.removesuffix("/testIamPermissions") + "/getIamPolicy?optionsRequestedPolicyVersion=3", None
    base = base.removesuffix(":testIamPermissions") + ":getIamPolicy"
    if parsed.hostname in {"cloudresourcemanager.googleapis.com", "iam.googleapis.com", "iap.googleapis.com"}:
        return base, {"options": {"requestedPolicyVersion": 3}}
    return base + "?options.requestedPolicyVersion=3", None


def full_resource(url, project_number):
    parsed = urlsplit(url)
    if url == PROJECT_URL:
        return "//cloudresourcemanager.googleapis.com/projects/" + project_number
    if parsed.hostname == "storage.googleapis.com":
        name = unquote(parsed.path.split("/storage/v1/b/", 1)[1].split("/iam/", 1)[0])
        return "//storage.googleapis.com/projects/_/buckets/" + name
    path = parsed.path.removesuffix(":testIamPermissions").removesuffix("/testIamPermissions")
    path = re.sub(r"^/(?:compute/)?v[12]/", "", path)
    return "//" + parsed.hostname + "/" + path


def allow_permissions(policies, roles, member):
    """Unconditional direct grants only. Google evaluates CEL and deny policies."""
    return {permission for policy in policies for binding in policy.get("bindings", [])
            if member in binding["members"] and not binding.get("condition")
            for permission in roles(binding["role"])["includedPermissions"]}


class PolicyReader:
    def __init__(self, token):
        self.token = token

    @cache
    def read(self, url, body=None):
        request = urllib.request.Request(url, data=body.encode() if body is not None else None,
                                         headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    def policy(self, url):
        endpoint, body = policy_endpoint(url)
        return self.read(endpoint, json.dumps(body) if body is not None else None)

    def role(self, name):
        require(re.fullmatch(r"(?:roles/[A-Za-z0-9_.]+|projects/[a-z0-9-]+/roles/[A-Za-z0-9_.]+)", name), "unsupported role identity")
        return self.read("https://iam.googleapis.com/v1/" + name)

    def verify(self, required, deployer, project_number):
        project = self.policy(PROJECT_URL)
        member = "serviceAccount:" + deployer
        for url, permissions in required:
            policies = [project] if url == PROJECT_URL else [project, self.policy(url)]
            proven = allow_permissions(policies, self.role, member)
            for permission in permissions:
                # Troubleshooter is read-only and handles inherited bindings,
                # conditional grants and deny policies.
                # UNKNOWN/permission-denied responses never imply access.
                body = json.dumps({"accessTuple": {"principal": deployer,
                    "fullResourceName": full_resource(url, project_number), "permission": permission}})
                verdict = self.read("https://policytroubleshooter.googleapis.com/v3/iam:troubleshoot", body)
                granted = (verdict.get("overallAccessState") == "CAN_ACCESS" and
                           (permission in proven or verdict.get("allowPolicyExplanation", {}).get("allowAccessState") == "ALLOW_ACCESS_STATE_GRANTED"))
                print(permission, "granted" if granted else "missing or unknown")
                require(granted,
                        "production deploy identity lacks proven permission " + permission)


def run(plan, root=ROOT):
    require(os.environ.get("GITHUB_EVENT_NAME") == "pull_request" and same_repo(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())),
            "PR plans require a same-repository pull_request")
    require(selected(plan), "no Terraform inputs selected")
    targets = scopes(plan, root)
    require(targets, "Terraform change has no registered plan scope")
    prod = root / "terraform/envs/prod"
    version = json.loads(terraform("version", "-json", directory=prod))
    require(version["terraform_version"] == TOOLCHAIN["terraform"], "PR Terraform version differs from pin")
    # Do not use PR-authored TF_CLI_ARGS or TF_LOG to leak the state to logs.
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("TF_VAR_", "TF_CLI_ARGS", "TF_LOG"))}
    terraform("init", "-input=false", "-lockfile=readonly", directory=prod, environment=environment)
    variables = deployed_variables(json.loads(terraform("show", "-json", directory=prod, environment=environment)))
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    reader = PolicyReader(token)
    project_number = reader.read("https://cloudresourcemanager.googleapis.com/v1/projects/" + PROJECT)["projectNumber"]
    deployer = os.environ["PRODUCTION_TERRAFORM_SERVICE_ACCOUNT"]
    require(re.fullmatch(r"[a-z0-9-]+@" + PROJECT + r"\.iam\.gserviceaccount\.com", deployer), "invalid production deploy identity")
    work = Path(os.environ["RUNNER_TEMP"]) / "pr-terraform"
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as temporary:
        for scope in targets:
            path = Path(temporary) / "plan.tfplan"
            variables_path = Path(temporary) / "variables.tfvars.json"
            variables_path.write_text(json.dumps({**scope["variables"], **variables}))
            for resource in scope["resources"]:
                address(resource)
            terraform("plan", "-lock=false", "-input=false", "-no-color", "-out=" + str(path),
                      "-var-file=" + str(variables_path), *("-target=" + r for r in scope["resources"]),
                      directory=prod, environment=environment)
            saved = json.loads(terraform("show", "-json", str(path), directory=prod, environment=environment))
            blocked = blocked_changes(saved, allowed_exact=scope["exact"], allowed_patterns=scope["patterns"],
                                      block_deletes=scope["block_deletes"], delete_only=scope["delete_only"],
                                      job_image=(scope["job_image"][0], variables[scope["job_image"][1]]) if scope["job_image"] else None,
                                      expected_config=variables.get("wdpa_monthly_image_config_digest"))
            require(not blocked, "PR saved plan violates registered resource/action scope")
            required = plan_checks(saved, project_number=project_number)
            reader.verify([*probe_call_checks(required), *scope["required"], *required], deployer, project_number)
            for row in saved.get("resource_changes", []):
                print(row["address"], "/".join(row["change"]["actions"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("select", "run"))
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if args.mode == "select":
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write("pr_terraform=" + str(selected(plan)).lower() + "\n")
        return
    try:
        run(plan)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        # Even exception text can contain provider-returned secrets. All plan
        # failures are fatal; the PR log contains only this fixed diagnostic.
        parser.exit(1, "Read-only PR plan/allowlist/permission check failed; resource values withheld.\n")


if __name__ == "__main__":
    main()
