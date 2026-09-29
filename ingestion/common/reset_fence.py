"""Read-only live checks for a separately reviewed reset writer fence.

Matching policy bytes is drift detection, not an IAM policy analyzer. A reviewer
must first establish effective writer exclusion (including inherited/group and
administrative paths) and approve the snapshot in the checked-in registry.
No snapshot is approved by default. No API here changes cloud configuration.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import re
from urllib.parse import quote

from ingestion.common import publication as p

PROJECT = "shared-datasets-1"
PROJECT_NUMBER = "12695949518"
ORGANIZATION = "471193686670"
BUCKET = "skytruth-shared-datasets-1"
REGION = "us-central1"
RESET_ACCOUNT = f"feature-id-reset@{PROJECT}.iam.gserviceaccount.com"
OLD_ACCOUNTS = tuple(f"{name}@{PROJECT}.iam.gserviceaccount.com" for name in
                     ("wdpa-monthly-job", "sea-ice-daily-job", "shared-datasets-publisher"))
JOBS = ("wdpa-monthly", "sea-ice-daily")
REGISTRY = Path(__file__).resolve().parents[2] / "catalog/feature-id-reset-fences.json"


class GoogleControlReader:
    def __init__(self, session):
        self.session = session

    def read(self, url, *, body=None, params=None):
        # URLs are constructed below, never supplied by a plan or registry.
        response = self.session.request("GET" if body is None else "POST", url,
                                        json=body, params=params, timeout=60, allow_redirects=False)
        p.require(response.status_code == 200, f"writer-fence read failed ({response.status_code}): {url}")
        result = p.strict_json(response.content)
        p.require(isinstance(result, dict), "writer-fence response must be an object")
        return result

    def pages(self, url, field, *, params=None):
        rows, seen = [], set()
        query = dict(params or {})
        while True:
            page = self.read(url, params=query)
            values = page.get(field, [])
            p.require(isinstance(values, list) and all(isinstance(row, dict) for row in values), "invalid writer-fence page")
            rows.extend(values)
            token = page.get("nextPageToken")
            if token is None or token == "":
                return rows
            p.require(isinstance(token, str) and token not in seen, "writer-fence pagination did not advance")
            seen.add(token)
            query["pageToken"] = token

    def policy(self, resource):
        return self.read(f"https://cloudresourcemanager.googleapis.com/v3/{resource}:getIamPolicy",
                         body={"options": {"requestedPolicyVersion": 3}})

    def deny_policies(self, resource):
        prefix = f"policies/{quote('cloudresourcemanager.googleapis.com/' + resource, safe='')}/denypolicies"
        rows = self.pages(f"https://iam.googleapis.com/v2/{prefix}", "policies")
        result = {}
        for row in rows:
            name = row.get("name", "")
            p.require(name.startswith(prefix + "/") and "/" not in name.removeprefix(prefix + "/"), "foreign deny-policy name")
            p.require(name not in result, "duplicate deny policy")
            # List omits rules. Retrieve every complete policy separately.
            result[name] = self.read(f"https://iam.googleapis.com/v2/{name}")
        return result

    def collect(self):
        project = self.read(f"https://cloudresourcemanager.googleapis.com/v3/projects/{PROJECT}")
        p.require(project.get("name") == f"projects/{PROJECT_NUMBER}" and project.get("parent") == f"organizations/{ORGANIZATION}",
                  "project ancestry changed; writer-fence coverage must be reviewed again")
        accounts = {}
        base = f"https://iam.googleapis.com/v1/projects/{PROJECT}/serviceAccounts"
        for account in self.pages(base, "accounts"):
            email = account.get("email", "")
            p.require(isinstance(email, str) and re.fullmatch(r"[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.gserviceaccount\.com", email) and account.get("projectId") == PROJECT and email not in accounts, "invalid/duplicate project service account")
            # Keep stable identity/status; omit display labels and key material.
            accounts[email] = {"unique_id": account["uniqueId"], "disabled": account.get("disabled", False),
                               "policy": self.read(f"{base}/{quote(email, safe='')}:getIamPolicy", body={}, params={"options.requestedPolicyVersion": 3})}
        for email in OLD_ACCOUNTS:
            p.require(email in accounts and accounts[email]["disabled"] is True, f"old writer account must be disabled: {email}")
        p.require(RESET_ACCOUNT in accounts and accounts[RESET_ACCOUNT]["disabled"] is False, "dedicated reset account must be enabled")

        # Dynamic execution lists are checked for quiescence, not fingerprinted:
        # completed historical executions may age out without changing the fence.
        for job in JOBS:
            parent = f"projects/{PROJECT}/locations/{REGION}/jobs/{job}"
            scheduler = self.read(f"https://cloudscheduler.googleapis.com/v1/{parent}")
            p.require(scheduler.get("state") == "PAUSED", f"scheduler must be paused: {job}")
            for execution in self.pages(f"https://run.googleapis.com/v2/{parent}/executions", "executions"):
                p.require(bool(execution.get("completionTime")) and not execution.get("reconciling", False), f"execution is still running or pending: {job}")

        storage = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}"
        folders = {}
        for folder in self.pages(storage + "/managedFolders", "items"):
            name = folder.get("name", "")
            p.require(isinstance(name, str) and bool(name) and name not in folders, "invalid/duplicate managed folder")
            folders[name] = self.read(f"{storage}/managedFolders/{quote(name, safe='')}/iam", params={"optionsRequestedPolicyVersion": 3})

        resources = (f"projects/{PROJECT_NUMBER}", f"organizations/{ORGANIZATION}")
        pools = {}
        pool_base = f"https://iam.googleapis.com/v1/projects/{PROJECT_NUMBER}/locations/global/workloadIdentityPools"
        for pool in self.pages(pool_base, "workloadIdentityPools"):
            name = pool["name"]
            p.require(name.startswith(f"projects/{PROJECT_NUMBER}/locations/global/workloadIdentityPools/") and name not in pools,
                      "invalid/duplicate workload identity pool")
            pools[name] = {"pool": pool, "providers": sorted(self.pages(f"https://iam.googleapis.com/v1/{name}/providers", "workloadIdentityPoolProviders"), key=lambda row: row["name"])}

        return {"schema_version": 1, "project": PROJECT, "bucket": BUCKET, "organization": ORGANIZATION,
                "accounts": accounts, "managed_folders": folders, "federation": pools,
                "bucket_policy": self.read(storage + "/iam", params={"optionsRequestedPolicyVersion": 3}),
                "allow_policies": {name: self.policy(name) for name in resources},
                "deny_policies": {name: self.deny_policies(name) for name in resources},
                "custom_roles": {name: sorted(self.pages(f"https://iam.googleapis.com/v1/{name}/roles", "roles", params={"view": "FULL"}), key=lambda row: row["name"]) for name in resources}}


def approved_fence(digest: str, *, registry: Path = REGISTRY, now: dt.datetime | None = None):
    value = p.strict_json(registry.read_bytes())
    p.keys(value, {"schema_version", "fences"}, "reset fence registry")
    p.require(type(value["schema_version"]) is int and value["schema_version"] == 1 and isinstance(value["fences"], dict), "invalid reset fence registry")
    p.require(p.hash_value(digest) and digest in value["fences"], "RESET_HELD: no reviewed writer fence is registered for this plan")
    entry = value["fences"][digest]
    p.keys(entry, {"snapshot", "expires_at", "review_note"}, "reviewed reset fence")
    p.require(isinstance(entry["review_note"], str) and bool(entry["review_note"].strip()), "writer-fence review evidence is required")
    p.require(p.digest(p.canonical(entry["snapshot"])) == digest, "reviewed writer-fence snapshot digest changed")
    expires = dt.datetime.fromisoformat(entry["expires_at"].replace("Z", "+00:00"))
    p.require(expires.tzinfo is not None and (now or dt.datetime.now(dt.UTC)) < expires, "reviewed writer fence has expired")
    return entry["snapshot"]


def check_live_fence(reader: GoogleControlReader, digest: str, *, registry: Path = REGISTRY):
    expected = approved_fence(digest, registry=registry)
    p.require(reader.collect() == expected, "live writer controls differ from the reviewed fence")
