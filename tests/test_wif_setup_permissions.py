"""Real probe HTTP/CLI serialization for the reviewed WIF setup proposal."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from scripts import deployment_permissions as live
from scripts import setup_grant_proof as proof
from scripts import wif_setup_permissions as wif
from scripts.terraform_plan_permissions import plan_checks
from test_logging_setup_permissions import LoggingHttp, PRINCIPAL, TOKEN
from test_terraform_plan_permissions import plan, row

NUMBER = "123456789"
POOL = f"projects/{NUMBER}/locations/global/workloadIdentityPools/github"
PROVIDER = POOL + "/providers/github-operational-observer"
BEFORE = datetime(2026, 10, 22, 1, 59, 59, tzinfo=timezone.utc)
EXPIRY = datetime(2026, 10, 22, 2, tzinfo=timezone.utc)


class WifHttp(LoggingHttp):
    def __init__(self):
        super().__init__()
        self.provider = None
        self.provider_error = (404, "NOT_FOUND")
        self.parent_create = True
        self.project = {"projectId": wif.PROJECT, "projectNumber": NUMBER}
        self.pool = {"name": POOL, "state": "ACTIVE"}
        self.roles = {
            name: {"name": name, "stage": "GA", "includedPermissions": [permission]}
            for name, permission in (
                (wif.READER_ROLE, wif.GET),
                (wif.CREATOR_ROLE, wif.CREATE),
            )
        }
        self.policy["bindings"] += [
            {"role": wif.READER_ROLE, "members": ["serviceAccount:" + PRINCIPAL]},
            {
                "role": wif.CREATOR_ROLE,
                "members": ["serviceAccount:" + PRINCIPAL],
                "condition": wif.condition(),
            },
        ]

    def __call__(self, request, timeout):
        parsed = urlsplit(request.full_url)
        if (
            parsed.hostname == "cloudresourcemanager.googleapis.com"
            and request.method == "GET"
        ):
            self.calls.append(request)
            assert parse_qs(parsed.query) == {"fields": ["projectId,projectNumber"]}
            return io.BytesIO(json.dumps(self.project).encode())
        if parsed.hostname == "iam.googleapis.com":
            if parsed.path == "/v1/" + wif.POOL and request.method == "GET":
                self.calls.append(request)
                assert parse_qs(parsed.query) == {"fields": ["name,state,disabled"]}
                return io.BytesIO(json.dumps(self.pool).encode())
            if parsed.path == "/v1/" + PROVIDER:
                self.calls.append(request)
                assert request.method == "GET" and parse_qs(parsed.query) == {
                    "fields": ["name,state,disabled"]
                }
                if self.provider is not None:
                    return io.BytesIO(json.dumps(self.provider).encode())
                code, status = self.provider_error
                raise HTTPError(
                    request.full_url,
                    code,
                    "provider metadata response",
                    {},
                    io.BytesIO(
                        json.dumps({"error": {"code": code, "status": status}}).encode()
                    ),
                )
            name = parsed.path.removeprefix("/v1/")
            if name in self.roles:
                self.calls.append(request)
                assert request.method == "GET"
                return io.BytesIO(json.dumps(self.roles[name]).encode())
            if request.full_url == wif.POOL_URL + ":testIamPermissions":
                self.calls.append(request)
                permissions = json.loads(request.data)["permissions"]
                if wif.CREATE in permissions:
                    assert permissions == [wif.CREATE]
                    return io.BytesIO(
                        json.dumps(
                            {"permissions": permissions if self.parent_create else []}
                        ).encode()
                    )
        return super().__call__(request, timeout)


class WifSetupTests(unittest.TestCase):
    def verify(self, transport, *, now=BEFORE, permissions=(wif.GET,)):
        with (
            patch.dict(os.environ, {"TERRAFORM_SERVICE_ACCOUNT": PRINCIPAL}),
            patch.object(live.urllib.request, "urlopen", transport),
        ):
            return wif.verify_provider(
                wif.PROVIDER_URL,
                permissions,
                TOKEN,
                lambda url, **kwargs: live.read_json(url, TOKEN, **kwargs),
                now=now,
            )

    def test_absent_provider_requires_exact_reviewed_roles_and_real_parent_create(self):
        transport = WifHttp()
        self.assertEqual(self.verify(transport), [])
        calls = transport.calls
        self.assertTrue(
            any(
                call.full_url.startswith(
                    "https://iam.googleapis.com/v1/" + PROVIDER + "?"
                )
                for call in calls
            )
        )
        probes = [
            json.loads(call.data)["permissions"]
            for call in calls
            if call.full_url.endswith(":testIamPermissions")
        ]
        self.assertEqual(probes, [[wif.CREATE]])
        self.assertFalse(
            any(
                "/providers/" in call.full_url
                and call.full_url.endswith(":testIamPermissions")
                for call in calls
            )
        )
        transport.parent_create = False
        self.assertEqual(self.verify(transport), [wif.CREATE])

    def test_existing_provider_uses_exact_child_get_after_create_expiry(self):
        transport = WifHttp()
        transport.provider = {"name": PROVIDER, "state": "ACTIVE"}
        transport.roles = {}
        transport.policy = {}
        transport.parent_create = False
        self.assertEqual(self.verify(transport, now=EXPIRY + timedelta(days=365)), [])
        self.assertFalse(
            any(
                call.full_url.endswith(":testIamPermissions")
                or call.full_url == proof.TOKENINFO
                for call in transport.calls
            )
        )

    def test_fixed_expiry_boundary_never_slides_or_uses_other_create_grants(self):
        for now in (EXPIRY, EXPIRY + timedelta(seconds=1)):
            with (
                self.subTest(now=now),
                self.assertRaisesRegex(RuntimeError, "has expired"),
            ):
                self.verify(WifHttp(), now=now)
        self.assertEqual(
            self.verify(WifHttp(), now=EXPIRY - timedelta(microseconds=1)), []
        )

    def test_wrong_missing_or_broader_grants_and_principal_are_rejected(self):
        cases = [
            lambda t: t.policy["bindings"][-1].pop("condition"),
            lambda t: t.policy["bindings"][-1]["condition"].update(expression="true"),
            lambda t: t.policy["bindings"][-1]["condition"].update(
                expression="request.time < timestamp('2099-01-01T00:00:00Z')"
            ),
            lambda t: t.policy["bindings"][-1]["members"].append(
                "serviceAccount:other@shared-datasets-1.iam.gserviceaccount.com"
            ),
            lambda t: t.roles[wif.CREATOR_ROLE]["includedPermissions"].append(
                "iam.workloadIdentityPoolProviders.update"
            ),
            lambda t: t.roles[wif.READER_ROLE].update(deleted=True),
            lambda t: t.identity.update(email="delegated-user@example.com"),
            lambda t: t.identity.pop("email_verified"),
        ]
        for mutation in cases:
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                transport = WifHttp()
                mutation(transport)
                self.verify(transport)

    def test_only_structured_not_found_can_authorize_initial_create_checks(self):
        for error in (
            (403, "PERMISSION_DENIED"),
            (500, "INTERNAL"),
            (404, "PERMISSION_DENIED"),
        ):
            transport = WifHttp()
            transport.provider_error = error
            with self.subTest(error=error), self.assertRaises(proof.ProbeHTTPError):
                self.verify(transport)
            self.assertFalse(
                any(
                    call.full_url.endswith(":testIamPermissions")
                    for call in transport.calls
                )
            )

    def test_canonical_project_pool_and_exact_child_cannot_be_substituted(self):
        for mutation in (
            lambda t: t.project.update(projectId="foreign"),
            lambda t: t.pool.update(name=POOL.replace(NUMBER, "999999")),
            lambda t: t.pool.update(disabled=True),
            lambda t: setattr(
                t, "provider", {"name": PROVIDER + "-other", "state": "ACTIVE"}
            ),
            lambda t: setattr(t, "provider", {"name": PROVIDER, "state": "DELETED"}),
        ):
            transport = WifHttp()
            mutation(transport)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                self.verify(transport)

    def test_saved_create_plan_is_rejected_if_provider_appeared_after_plan(self):
        transport = WifHttp()
        transport.provider = {"name": PROVIDER, "state": "ACTIVE"}
        with self.assertRaisesRegex(RuntimeError, "plan is stale"):
            self.verify(transport, permissions=(wif.GET, wif.CREATE))

    def test_saved_plan_cli_serializes_exact_create_and_scoped_identity_token(self):
        provider = {
            "project": wif.PROJECT,
            "workload_identity_pool_id": "github",
            "workload_identity_pool_provider_id": "github-operational-observer",
        }
        value = plan(
            row("google_iam_workload_identity_pool_provider", ["create"], provider)
        )
        transport = WifHttp()
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / "plan.json"
            saved.write_text(json.dumps(value))
            clock = Mock(wraps=datetime)
            clock.now.return_value = BEFORE
            with (
                patch(
                    "sys.argv",
                    [
                        "deployment_permissions.py",
                        "--target",
                        "operational-observer-setup",
                        "--plan-json",
                        str(saved),
                    ],
                ),
                patch.dict(
                    os.environ,
                    {"TERRAFORM_SERVICE_ACCOUNT": PRINCIPAL, proof.TOKEN_ENV: TOKEN},
                ),
                patch.object(
                    live.subprocess, "check_output", return_value=NUMBER
                ) as gcloud,
                patch.object(live.urllib.request, "urlopen", transport),
                patch.object(wif, "datetime", clock),
                patch("builtins.print"),
            ):
                live.main()
            gcloud.assert_called_once_with(
                [
                    "gcloud",
                    "projects",
                    "describe",
                    wif.PROJECT,
                    "--format=value(projectNumber)",
                ],
                text=True,
            )
        for action in ("update", "delete"):
            with (
                self.subTest(action=action),
                self.assertRaisesRegex(ValueError, "separately reviewed authority"),
            ):
                plan_checks(
                    plan(
                        row(
                            "google_iam_workload_identity_pool_provider",
                            [action],
                            copy.deepcopy(provider),
                        )
                    ),
                    project_number=NUMBER,
                )
