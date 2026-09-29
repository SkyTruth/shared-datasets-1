from __future__ import annotations

from copy import deepcopy
import datetime as dt
from pathlib import Path
import unittest
from unittest import mock
from urllib.parse import quote

from ingestion.common import publication as p, reset_fence as f
from test_publication import publication_temp_directory


class ControlFixture(f.GoogleControlReader):
    """Documented REST responses, including paginated accounts and deny details."""

    def __init__(self):
        self.responses, self.calls = {}, []
        self.project_url = f"https://cloudresourcemanager.googleapis.com/v3/projects/{f.PROJECT}"
        self.responses[self.project_url] = {"name": f"projects/{f.PROJECT_NUMBER}", "parent": f"organizations/{f.ORGANIZATION}"}
        self.account_url = f"https://iam.googleapis.com/v1/projects/{f.PROJECT}/serviceAccounts"
        accounts = [{"email": email, "uniqueId": str(index), "projectId": f.PROJECT, "disabled": email in f.OLD_ACCOUNTS}
                    for index, email in enumerate((*f.OLD_ACCOUNTS, f.RESET_ACCOUNT, f"{f.PROJECT_NUMBER}-compute@developer.gserviceaccount.com"), 1)]
        self.responses[self.account_url] = {"accounts": accounts[:2], "nextPageToken": "more"}
        self.responses[(self.account_url, "more")] = {"accounts": accounts[2:]}
        for account in accounts:
            self.responses[f"{self.account_url}/{quote(account['email'], safe='')}:getIamPolicy"] = {"version": 3, "etag": "account-policy"}
        self.scheduler_urls, self.execution_urls = [], []
        for job in f.JOBS:
            parent = f"projects/{f.PROJECT}/locations/{f.REGION}/jobs/{job}"
            scheduler = f"https://cloudscheduler.googleapis.com/v1/{parent}"
            executions = f"https://run.googleapis.com/v2/{parent}/executions"
            self.responses[scheduler] = {"state": "PAUSED"}
            self.responses[executions] = {"executions": [{"completionTime": "2026-09-29T00:00:00Z", "reconciling": False}]}
            self.scheduler_urls.append(scheduler)
            self.execution_urls.append(executions)
        storage = f"https://storage.googleapis.com/storage/v1/b/{f.BUCKET}"
        self.responses[storage + "/managedFolders"] = {"items": [{"name": "protected/example/"}]}
        self.responses[storage + "/managedFolders/protected%2Fexample%2F/iam"] = {"etag": "folder-policy"}
        self.responses[storage + "/iam"] = {"version": 3, "etag": "bucket-policy"}
        pool_base = f"https://iam.googleapis.com/v1/projects/{f.PROJECT_NUMBER}/locations/global/workloadIdentityPools"
        self.responses[pool_base] = {"workloadIdentityPools": [{"name": pool_base.removeprefix("https://iam.googleapis.com/v1/") + "/reset"}]}
        self.responses[pool_base + "/reset/providers"] = {"workloadIdentityPoolProviders": [{"name": "github", "state": "ACTIVE"}]}
        self.deny_names = []
        for resource in (f"projects/{f.PROJECT_NUMBER}", f"organizations/{f.ORGANIZATION}"):
            self.responses[f"https://cloudresourcemanager.googleapis.com/v3/{resource}:getIamPolicy"] = {"version": 3, "etag": resource}
            deny = "policies/" + quote("cloudresourcemanager.googleapis.com/" + resource, safe="") + "/denypolicies"
            name = deny + "/block-old-writers"
            self.responses[f"https://iam.googleapis.com/v2/{deny}"] = {"policies": [{"name": name}]}
            self.responses[f"https://iam.googleapis.com/v2/{name}"] = {"name": name, "rules": [{"denyRule": {"deniedPermissions": ["storage.googleapis.com/objects.create"]}}]}
            self.deny_names.append(name)
            self.responses[f"https://iam.googleapis.com/v1/{resource}/roles"] = {"roles": [{"name": resource + "/roles/custom", "includedPermissions": ["storage.objects.get"]}]}

    def read(self, url, *, body=None, params=None):
        self.calls.append((url, deepcopy(body), deepcopy(params)))
        key = (url, params["pageToken"]) if params and "pageToken" in params else url
        return deepcopy(self.responses[key])


class ResetFenceTests(unittest.TestCase):
    def test_snapshot_covers_inherited_and_indirect_policy_inputs(self):
        reader = ControlFixture()
        snapshot = reader.collect()
        self.assertEqual(len(snapshot["accounts"]), 5)
        self.assertEqual(len(snapshot["allow_policies"]), 2)
        self.assertEqual(snapshot["managed_folders"]["protected/example/"]["etag"], "folder-policy")
        self.assertEqual(len(snapshot["federation"]), 1)
        self.assertTrue(all(policy["rules"] for policies in snapshot["deny_policies"].values() for policy in policies.values()))
        self.assertTrue(all(roles[0]["includedPermissions"] for roles in snapshot["custom_roles"].values()))
        account_reads = [call for call in reader.calls if "/serviceAccounts/" in call[0]]
        # IAM v1 puts options in the query; Resource Manager v3 uses the body.
        self.assertTrue(all(body == {} and params == {"options.requestedPolicyVersion": 3} for _, body, params in account_reads))

    def test_old_account_enabled_missing_reset_or_wrong_ancestry_refuses(self):
        for case in ("old_enabled", "reset_disabled", "reset_missing", "ancestry"):
            with self.subTest(case=case):
                reader = ControlFixture()
                if case == "old_enabled":
                    reader.responses[reader.account_url]["accounts"][0]["disabled"] = False
                elif case == "ancestry":
                    reader.responses[reader.project_url]["parent"] = "folders/unknown"
                else:
                    rows = reader.responses[(reader.account_url, "more")]["accounts"]
                    if case == "reset_missing":
                        rows[:] = [row for row in rows if row["email"] != f.RESET_ACCOUNT]
                    else:
                        next(row for row in rows if row["email"] == f.RESET_ACCOUNT)["disabled"] = True
                with self.assertRaises(p.PublicationError):
                    reader.collect()

    def test_schedules_and_every_execution_page_must_be_quiescent(self):
        for case in ("schedule", "pending", "running", "reconciling"):
            with self.subTest(case=case):
                reader = ControlFixture()
                if case == "schedule":
                    reader.responses[reader.scheduler_urls[1]]["state"] = "ENABLED"
                else:
                    url = reader.execution_urls[1]
                    reader.responses[url]["nextPageToken"] = "late"
                    execution = {} if case == "pending" else {"startTime": "2026-09-29T00:00:00Z"}
                    if case == "reconciling":
                        execution.update(completionTime="2026-09-29T00:01:00Z", reconciling=True)
                    reader.responses[(url, "late")] = {"executions": [execution]}
                with self.assertRaises(p.PublicationError):
                    reader.collect()

    def test_inaccessible_or_redirected_reads_and_broken_pagination_refuse(self):
        for status in (302, 403, 404, 500):
            session = mock.Mock()
            session.request.return_value.status_code = status
            with self.assertRaises(p.PublicationError):
                f.GoogleControlReader(session).read("https://iam.googleapis.com/v2/policies")
            self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        for token in ("more", 0, True):
            reader = ControlFixture()
            reader.responses[(reader.account_url, "more")]["nextPageToken"] = token
            with self.assertRaises(p.PublicationError):
                reader.collect()

    def test_matching_snapshot_is_required_but_never_self_authorizes(self):
        reader = ControlFixture()
        snapshot = reader.collect()
        digest = p.digest(p.canonical(snapshot))
        with publication_temp_directory() as tmp:
            registry = Path(tmp) / "fences.json"
            entry = {"snapshot": snapshot, "expires_at": "2099-01-01T00:00:00Z", "review_note": "Synthetic reviewed fixture; not production evidence."}
            value = {"schema_version": 1, "fences": {digest: entry}}
            registry.write_bytes(p.canonical(value))
            f.check_live_fence(reader, digest, registry=registry)
            reader.responses[f"https://cloudresourcemanager.googleapis.com/v3/projects/{f.PROJECT_NUMBER}:getIamPolicy"]["etag"] = "changed"
            with self.assertRaisesRegex(p.PublicationError, "differ"):
                f.check_live_fence(reader, digest, registry=registry)
            with self.assertRaisesRegex(p.PublicationError, "expired"):
                f.approved_fence(digest, registry=registry, now=dt.datetime(2100, 1, 1, tzinfo=dt.UTC))
            entry["snapshot"]["accounts"].clear()
            registry.write_bytes(p.canonical(value))
            with self.assertRaisesRegex(p.PublicationError, "digest changed"):
                f.approved_fence(digest, registry=registry)
        with self.assertRaisesRegex(p.PublicationError, "RESET_HELD"):
            f.approved_fence(digest)
