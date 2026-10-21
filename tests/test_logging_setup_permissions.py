"""Supported Logging API resources, token identity and exact installed grants."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from scripts import deployment_permissions as live
from scripts import logging_setup_permissions as logging_setup

PROJECT = "shared-datasets-1"
PRINCIPAL = f"shared-datasets-terraform@{PROJECT}.iam.gserviceaccount.com"
ROLE = f"projects/{PROJECT}/roles/sharedDatasetsOperationalLogViewManager"
UNIQUE_ID = "100000000000000000001"
TOKEN = "test-token+/="
PARENT_PERMISSIONS = {"logging.buckets.get", "logging.views.create", "logging.views.list"}
VIEW_PERMISSIONS = {"logging.views.get", "logging.views.update", "logging.views.getIamPolicy", "logging.views.setIamPolicy"}
BUCKETS = ("_Default", "_Required")


def condition():
    return {
        "title": "operational-view-buckets-only",
        "description": "No access to views in other Logging buckets.",
        "expression": " || ".join(
            f"(resource.name == 'projects/{PROJECT}/locations/global/buckets/{bucket}' || "
            f"resource.name.startsWith('projects/{PROJECT}/locations/global/buckets/{bucket}/views/'))"
            for bucket in BUCKETS
        ),
    }


class LoggingHttp:
    def __init__(self):
        self.calls = []
        self.identity = {"aud": UNIQUE_ID, "expires_in": "3599", "email": PRINCIPAL, "email_verified": "true", "scope": " ".join(logging_setup.TOKEN_SCOPES)}
        self.account = {"name": f"projects/{PROJECT}/serviceAccounts/{PRINCIPAL}", "email": PRINCIPAL, "uniqueId": UNIQUE_ID}
        self.role = {"name": ROLE, "stage": "GA", "includedPermissions": sorted(PARENT_PERMISSIONS | VIEW_PERMISSIONS)}
        self.policy = {"version": 3, "bindings": [{"role": ROLE, "members": ["serviceAccount:" + PRINCIPAL], "condition": condition()}]}
        self.view_pages = [{"views": [{"name": f"projects/{PROJECT}/locations/global/buckets/{{bucket}}/views/_AllLogs"}]}]

    def __call__(self, request, timeout):
        self.calls.append(request)
        self.assert_timeout(timeout)
        parsed = urlsplit(request.full_url)
        query = parse_qs(parsed.query)
        raw = request.data
        if parsed.hostname == "storage.googleapis.com" and parsed.path.endswith("/iam/testPermissions"):
            result = {"permissions": query["permissions"]}
        elif parsed.path.endswith(":testIamPermissions"):
            permissions = json.loads(raw)["permissions"]
            if parsed.hostname == "logging.googleapis.com" and not set(permissions) <= VIEW_PERMISSIONS:
                payload = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Permission logging.buckets.get is not valid for this resource."}}
                raise HTTPError(request.full_url, 400, "invalid resource permission", {}, io.BytesIO(json.dumps(payload).encode()))
            result = {"permissions": permissions}
        elif request.full_url == "https://oauth2.googleapis.com/tokeninfo":
            assert request.method == "POST"
            assert parse_qs(raw.decode()) == {"access_token": [TOKEN]}
            assert not request.has_header("Authorization")
            result = self.identity
        elif parsed.hostname == "iam.googleapis.com":
            assert request.method == "GET"
            if "/serviceAccounts/" in parsed.path:
                assert parsed.path == f"/v1/projects/{PROJECT}/serviceAccounts/{PRINCIPAL}"
                result = self.account
            else:
                assert parsed.path == "/v1/" + ROLE
                result = self.role
        elif parsed.path.endswith(":getIamPolicy"):
            assert request.method == "POST"
            assert json.loads(raw) == {"options": {"requestedPolicyVersion": 3}}
            result = self.policy
        elif parsed.hostname == "logging.googleapis.com":
            assert request.method == "GET"
            bucket = parsed.path.split("/buckets/")[1].split("/")[0]
            assert bucket in BUCKETS
            if parsed.path.endswith("/views"):
                assert query["fields"] == ["views(name),nextPageToken"]
                index = int(query.get("pageToken", ["0"])[0])
                result = json.loads(json.dumps(self.view_pages[index]).replace("{bucket}", bucket))
            else:
                assert query == {"fields": ["name"]}
                result = {"name": f"projects/{PROJECT}/locations/global/buckets/{bucket}"}
        else:
            raise AssertionError("unexpected probe URL: " + request.full_url)
        return io.BytesIO(json.dumps(result).encode())

    @staticmethod
    def assert_timeout(timeout):
        assert timeout == 30


class LoggingPermissionTests(unittest.TestCase):
    def verify(self, transport):
        with patch.dict(os.environ, {"TERRAFORM_SERVICE_ACCOUNT": PRINCIPAL}), patch.object(live.urllib.request, "urlopen", transport):
            live.verify("operational-observer-setup", lambda url, permissions: live.request(url, permissions, TOKEN), attempts=1)

    def test_initial_setup_http_requests_match_actual_resource_contract(self):
        transport = LoggingHttp()
        self.verify(transport)
        self.assertTrue(any(call.full_url.endswith(":getIamPolicy") for call in transport.calls))
        for call in transport.calls:
            if call.full_url.startswith("https://logging.googleapis.com/") and call.full_url.endswith(":testIamPermissions"):
                self.assertTrue(set(json.loads(call.data)["permissions"]) <= VIEW_PERMISSIONS)
        self.assertEqual({p for _, values in live.checks("operational-observer-setup") for p in values} & (PARENT_PERMISSIONS | VIEW_PERMISSIONS), PARENT_PERMISSIONS | VIEW_PERMISSIONS)

    def test_metadata_enumeration_reads_every_page_without_log_entries(self):
        transport = LoggingHttp()
        transport.view_pages = [{"views": [], "nextPageToken": "1"}, {"views": [{"name": f"projects/{PROJECT}/locations/global/buckets/{{bucket}}/views/_AllLogs"}]}]
        self.verify(transport)
        lists = [call for call in transport.calls if urlsplit(call.full_url).path.endswith("/views")]
        self.assertEqual(len(lists), 4)
        self.assertEqual(sum("pageToken=1" in call.full_url for call in lists), 2)
        self.assertFalse(any("entries" in call.full_url or "logs/" in call.full_url for call in transport.calls))

    def test_wrong_or_missing_installed_authority_never_passes(self):
        mutations = [
            ("wrong principal", lambda value: value.identity.update(aud="100000000000000000002")),
            ("missing principal", lambda value: value.identity.pop("aud")),
            ("missing scoped email", lambda value: value.identity.pop("email")),
            ("unverified email", lambda value: value.identity.update(email_verified=False)),
            ("missing verification", lambda value: value.identity.pop("email_verified")),
            ("missing identity scope", lambda value: value.identity.update(scope="https://www.googleapis.com/auth/cloud-platform")),
            ("wrong account email", lambda value: value.account.update(email="other@" + PROJECT + ".iam.gserviceaccount.com")),
            ("wrong account name", lambda value: value.account.update(name="projects/foreign/serviceAccounts/" + PRINCIPAL)),
            ("disabled account", lambda value: value.account.update(disabled=True)),
            ("wrong optional email", lambda value: value.identity.update(email="other@" + PROJECT + ".iam.gserviceaccount.com")),
            ("expired token", lambda value: value.identity.update(expires_in=0)),
            ("missing binding", lambda value: value.policy.update(bindings=[])),
            ("wrong policy version", lambda value: value.policy.update(version=1)),
            ("wrong member", lambda value: value.policy["bindings"][0].update(members=["serviceAccount:other@" + PROJECT + ".iam.gserviceaccount.com"])),
            ("missing condition", lambda value: value.policy["bindings"][0].pop("condition")),
            ("broader condition", lambda value: value.policy["bindings"][0]["condition"].update(expression="true")),
            ("wrong role", lambda value: value.role.update(name="projects/other/roles/sharedDatasetsOperationalLogViewManager")),
            ("deleted role", lambda value: value.role.update(deleted=True)),
            ("inactive role", lambda value: value.role.update(stage="DISABLED")),
            ("missing creation permission", lambda value: value.role["includedPermissions"].remove("logging.views.create")),
            ("broader role", lambda value: value.role["includedPermissions"].append("logging.views.delete")),
        ]
        for label, mutation in mutations:
            with self.subTest(label=label):
                transport = LoggingHttp()
                mutation(transport)
                with self.assertRaises(RuntimeError):
                    self.verify(transport)

    def test_authorized_party_does_not_replace_audience_or_verified_email(self):
        transport = LoggingHttp()
        transport.identity.update(azp="delegating-service-account-unique-id", expires_in=3599)
        self.verify(transport)
        transport.identity.update(email_verified=True)
        self.verify(transport)
        transport.identity.update(aud="wrong", azp=UNIQUE_ID)
        with self.assertRaisesRegex(RuntimeError, "does not identify"):
            self.verify(transport)

    def test_delegated_same_audience_token_requires_exact_verified_principal(self):
        for email in (None, "delegated-user@skytruth.org"):
            transport = LoggingHttp()
            transport.identity.update(azp=UNIQUE_ID)
            if email is None:
                transport.identity.pop("email")
            else:
                transport.identity["email"] = email
            with self.subTest(email=email), self.assertRaisesRegex(RuntimeError, "verified email"):
                self.verify(transport)

    def test_saved_plan_cli_uses_same_parent_proof_and_explicit_scoped_token(self):
        value = {"format_version": "1.2", "resource_changes": [{"address": 'google_logging_log_view.operational_observations["_Default"]', "type": "google_logging_log_view", "change": {"actions": ["create"], "after": {"parent": "projects/" + PROJECT, "location": "global", "bucket": "_Default", "name": "shared-datasets-operations"}}}]}
        transport = LoggingHttp()
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / "saved.tfplan.json"
            saved.write_text(json.dumps(value))
            with patch("sys.argv", ["deployment_permissions.py", "--target", "operational-observer-setup", "--plan-json", str(saved)]), patch.dict(os.environ, {"TERRAFORM_SERVICE_ACCOUNT": PRINCIPAL, logging_setup.TOKEN_ENV: TOKEN}), patch.object(live.subprocess, "check_output", return_value="123456789") as gcloud, patch.object(live.urllib.request, "urlopen", transport), patch("builtins.print") as output:
                live.main()
            gcloud.assert_called_once_with(["gcloud", "projects", "describe", PROJECT, "--format=value(projectNumber)"], text=True)
            output.assert_called_once_with("Saved-plan mutation permissions and declared post-apply operations verified.")
        self.assertTrue(any(call.full_url.endswith(":getIamPolicy") for call in transport.calls))
        self.assertFalse(any(call.full_url.endswith("projects/" + PROJECT + ":testIamPermissions") for call in transport.calls))

    def test_logging_cli_never_falls_back_to_email_less_gcloud_token(self):
        with patch("sys.argv", ["deployment_permissions.py", "--target", "operational-observer-setup"]), patch.dict(os.environ, {logging_setup.TOKEN_ENV: ""}), patch.object(live.subprocess, "check_output") as gcloud, patch.object(live, "request") as request, self.assertRaises(SystemExit):
            live.main()
        gcloud.assert_not_called()
        request.assert_not_called()

    def test_incomplete_or_foreign_metadata_enumeration_never_passes(self):
        for pages in ([{"views": [], "nextPageToken": "0"}], [{"views": [{"name": "projects/other/locations/global/buckets/_Default/views/foreign"}]}], [{"views": "unavailable"}]):
            transport = LoggingHttp()
            transport.view_pages = copy.deepcopy(pages)
            with self.subTest(pages=pages), self.assertRaises(RuntimeError):
                self.verify(transport)

    def test_denied_bucket_read_or_failed_later_page_remains_a_failure(self):
        for failed_path in ("/buckets/_Default?", "pageToken=1"):
            transport = LoggingHttp()
            transport.view_pages = [{"views": [], "nextPageToken": "1"}, {"views": []}]
            def failing(request, timeout):
                if failed_path in request.full_url:
                    payload = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "denied"}}
                    raise HTTPError(request.full_url, 403, "denied", {}, io.BytesIO(json.dumps(payload).encode()))
                return transport(request, timeout)
            with self.subTest(failed_path=failed_path), self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                self.verify(failing)
            self.assertFalse(any(call.full_url.endswith(":getIamPolicy") for call in transport.calls))

    def test_tokeninfo_error_never_exposes_token_in_url_or_diagnostic(self):
        transport = LoggingHttp()
        def failing(request, timeout):
            if request.full_url == logging_setup.TOKENINFO:
                self.assertNotIn(TOKEN, request.full_url)
                message = "invalid " + TOKEN + " encoded " + request.data.decode().split("=", 1)[1]
                payload = {"error": {"code": 400, "message": message}}
                raise HTTPError(request.full_url, 400, "bad token", {}, io.BytesIO(json.dumps(payload).encode()))
            return transport(request, timeout)
        with self.assertRaisesRegex(RuntimeError, "HTTP 400") as failure:
            self.verify(failing)
        self.assertNotIn(TOKEN, str(failure.exception))
        self.assertIn("[REDACTED]", str(failure.exception))

    def test_empty_view_list_is_a_complete_read_and_no_log_payloads_are_requested(self):
        transport = LoggingHttp()
        transport.view_pages = [{}]
        self.verify(transport)
        self.assertEqual(sum(urlsplit(call.full_url).path.endswith("/views") for call in transport.calls), 2)

    def test_missing_permission_on_existing_view_is_not_replaced_by_role_proof(self):
        transport = LoggingHttp()
        def missing(request, timeout):
            if "/views/_AllLogs:testIamPermissions" in request.full_url:
                return io.BytesIO(json.dumps({"permissions": ["logging.views.get"]}).encode())
            return transport(request, timeout)
        with self.assertRaisesRegex(RuntimeError, "logging.views.update"):
            self.verify(missing)
