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
from scripts.terraform_target_contracts import resource_index
from scripts.tested_image_bundle import image_consumers

REPOSITORY = "SkyTruth/shared-datasets-1"
TF_COMMANDS = frozenset({"version", "init", "show", "plan"})


def scopes(plan, root=ROOT):
    """Resolve CI-selected executors and manual-only changed resource owners."""
    deployments = registry(root, section="deployments")
    workflows = {deployments[name]["workflow"] for name in plan["deployments"]}
    changed = set(plan["changed_paths"] or [])
    declarations = {name for revision in {None, plan.get("base")} for path, rows in resource_index(root, revision).items()
                    if path in changed and Path(path).parent.as_posix() == "terraform/envs/prod" for name in rows}
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


def selected(plan, root=ROOT):
    paths = plan["changed_paths"]
    candidate = paths is None or any(p.startswith("terraform/") and not p.endswith(".md")
                                   or p == ".github/terraform-targets.json" for p in paths)
    if candidate and not scopes(plan, root):
        print("::notice::Terraform inputs have no registered production scope; live PR planning skipped. Static IaC checks still run.")
        return False
    return candidate


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
    if completed.returncode:
        raise PlanFailure("Terraform " + command + " failed (attribute diagnostics withheld)")
    return completed.stdout


def deployed_variables(state):
    """Read existing immutable images and non-secret settings without state writes."""
    values = {}
    images = image_consumers()

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


class PolicyReader:
    def __init__(self, token):
        self.token = token

    @cache
    def read(self, url, body=None):
        request = urllib.request.Request(url, data=body.encode() if body is not None else None,
                                         headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    def verify(self, required, deployer, project_number):
        for url, permissions in required:
            for permission in permissions:
                # Troubleshooter is read-only and handles inherited bindings,
                # conditional grants and deny policies.
                # UNKNOWN/permission-denied responses never imply access.
                body = json.dumps({"accessTuple": {"principal": deployer,
                    "fullResourceName": full_resource(url, project_number), "permission": permission}})
                verdict = self.read("https://policytroubleshooter.googleapis.com/v3/iam:troubleshoot", body)
                granted = (verdict.get("overallAccessState") == "CAN_ACCESS" and
                           verdict.get("allowPolicyExplanation", {}).get("allowAccessState") == "ALLOW_ACCESS_STATE_GRANTED")
                print(permission, "granted" if granted else "missing or unknown")
                if not granted:
                    raise PlanFailure("production deploy identity lacks proven permission " + permission
                                      + " on " + full_resource(url, project_number))


class PlanFailure(ValueError):
    """Only intentionally value-free diagnostics may reach the PR log."""


def run(plan, root=ROOT):
    require(os.environ.get("GITHUB_EVENT_NAME") == "pull_request" and same_repo(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())),
            "PR plans require a same-repository pull_request")
    if not selected(plan, root):
        return
    targets = scopes(plan, root)
    require(targets, "Terraform change has no registered plan scope")
    excluded = registry(root, section="pr_plan_exclusions")
    for scope in targets:
        if scope["name"] in excluded:
            print("::notice::Live PR plan excluded: " + scope["name"] + ": " + excluded[scope["name"]])
    targets = [scope for scope in targets if scope["name"] not in excluded]
    if not targets:
        return
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
            try:
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
                if blocked:
                    resources = sorted({address(row["address"]) for row in saved.get("resource_changes", [])})
                    raise PlanFailure("saved plan violates resource/action/image policy; resources " + ", ".join(resources))
                required = plan_checks(saved, project_number=project_number)
                reader.verify([*probe_call_checks(required), *scope["required"], *required], deployer, project_number)
                for row in saved.get("resource_changes", []):
                    print(row["address"], "/".join(row["change"]["actions"]))
            except PlanFailure as error:
                raise PlanFailure(scope["name"] + ": " + str(error)) from None
            except (ValueError, KeyError, OSError, subprocess.SubprocessError):
                raise PlanFailure(scope["name"] + ": Terraform plan or permission validation failed; attribute diagnostics withheld") from None


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
    except PlanFailure as error:
        parser.exit(1, str(error) + "\n")
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        # Unclassified external diagnostics can contain provider-returned secrets.
        parser.exit(1, "Read-only PR plan/allowlist/permission check failed; resource values withheld.\n")


if __name__ == "__main__":
    main()
