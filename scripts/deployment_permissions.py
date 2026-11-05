#!/usr/bin/env python3
"""Read-only permission probes for the authenticated protected deployer.

Only trusted main runs this code. A grant declared in Terraform is not live
readiness. Safe permission reads may retry for propagation; mutations never do.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.terraform_plan_permissions import plan_checks, probe_call_checks

from scripts.terraform_target_contracts import registry

PROJECT = "shared-datasets-1"


def checks(target):
    """Expand reviewed probe references; unknown targets never inherit authority."""
    probes = registry(section="permission_probes")
    def expand(name, ancestors=()):
        if name in ancestors:
            raise ValueError("Cyclic deployment permission probes")
        rows = probes[name]
        if (not isinstance(rows, list) or not rows or any(
                not isinstance(row, str) and (not isinstance(row, list) or len(row) != 2
                or not isinstance(row[0], str) or not row[0].startswith("https://")
                or not isinstance(row[1], list) or not row[1] or not all(isinstance(p, str) and p for p in row[1])) for row in rows)):
            raise ValueError("Invalid deployment permission probe")
        return [check for row in rows
                for check in (expand(row, (*ancestors, name)) if isinstance(row, str) else [(row[0], tuple(row[1]))])]
    return expand(target)


MONITORING_PERMISSIONS = checks("monitoring-alerts")[0][1]
PROJECT_PERMISSIONS = checks("job-runtime")[0][1]
SECRET_PERMISSIONS = checks("translation-bootstrap")[0][1]


def request(url, permissions, token):
    raw = json.dumps({"permissions": list(permissions)}).encode()
    req = urllib.request.Request(url, data=None if "storage.googleapis.com" in url else raw, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="GET" if "storage.googleapis.com" in url else "POST")
    try:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    payload = json.load(response)
                break
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 2 or isinstance(error, urllib.error.HTTPError) and not 500 <= error.code < 600:
                    raise
                time.sleep(5 * (attempt + 1))
    except urllib.error.HTTPError as error:
        # Capture structured Google error diagnostics, never request headers or
        # an incomplete body that might contain a truncated credential value.
        body = error.read(16 * 1024 + 1)
        diagnostic = 'response body unavailable, unstructured or over 16 KiB'
        if len(body) <= 16 * 1024:
            try:
                vendor = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                vendor = None
            if isinstance(vendor, dict) and isinstance(vendor.get('error'), dict):
                safe = {key: vendor['error'][key] for key in ('code', 'status', 'message', 'details') if key in vendor['error']}
                encoded_token = json.dumps(token, ensure_ascii=True)[1:-1]
                diagnostic = json.dumps(safe, ensure_ascii=True).replace(encoded_token, '[REDACTED]')[:4096]
        raise RuntimeError(f'permission probe HTTP {error.code}: {url}; {diagnostic}') from error
    actual = payload.get("permissions", [])
    if not isinstance(actual, list) or not all(isinstance(value, str) for value in actual):
        raise RuntimeError("invalid testIamPermissions response")
    return sorted(set(permissions) - set(actual))


def verify_checks(required, probe, *, attempts=7, pause=time.sleep):
    # Calling Compute testIamPermissions without its list prerequisite returns
    # 403 before it can report the actual resource-operation permissions. Keep
    # these phases ordered; success in the first phase cannot satisfy the second.
    for phase, checks in (("permission-probe call", probe_call_checks(required)), ("resource operation", required)):
        for attempt in range(attempts):
            missing = [(url, probe(url, permissions)) for url, permissions in checks]
            missing = [(url, permissions) for url, permissions in missing if permissions]
            if not missing:
                break
            if attempt + 1 < attempts:
                pause(10)
        else:
            detail = "; ".join(f"{url}: {', '.join(permissions)}" for url, permissions in missing)
            raise RuntimeError(f"deployment identity is not ready for {phase} after bounded propagation checks: {detail}")


def verify(target, probe, *, attempts=7, pause=time.sleep):
    verify_checks(checks(target), probe, attempts=attempts, pause=pause)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(registry(section="permission_probes")))
    parser.add_argument("--plan-json", type=Path, help="JSON from the exact saved, allowlisted plan to be applied")
    parser.add_argument("--static-base", help="Check changed HCL against apply scopes and permission manifests without credentials")
    parser.add_argument("--static-head", default="HEAD")
    args = parser.parse_args()
    if args.static_base:
        from scripts.terraform_plan_permissions import static_checks
        static_checks(Path.cwd(), args.static_base, args.static_head)
        print("Changed HCL apply scopes and permission contracts verified; live IAM and saved plans remain protected checks.")
        return
    if not (args.target or args.plan_json):
        parser.error("--target or --plan-json is required")
    if args.target == "artifact-registry-images" and args.plan_json:
        parser.error("image operations require the repository permission probe, not Terraform plan permissions")
    if args.plan_json:
        plan = json.loads(args.plan_json.read_text())
        project_number = subprocess.check_output(["gcloud", "projects", "describe", PROJECT, "--format=value(projectNumber)"], text=True).strip()
        required = plan_checks(plan, project_number=project_number, target=args.target)
    else:
        required = checks(args.target)
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    if not token:
        parser.error("authenticated deployment identity has no access token")
    verify_checks(required, lambda url, permissions: request(url, permissions, token))
    if args.plan_json:
        print("Saved-plan mutation permissions and declared post-apply operations verified.")
    else:
        print(f"{args.target}: deployment permission prerequisites verified.")


if __name__ == "__main__":
    main()
