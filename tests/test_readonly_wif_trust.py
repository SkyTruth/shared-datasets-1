"""Exact existing-child repair: real HTTP/CLI boundaries, no general update."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
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
from scripts import readonly_wif_trust as trust
from scripts import setup_grant_proof as proof
from scripts import wif_setup_permissions as wif
from scripts.terraform_plan_permissions import plan_checks
from test_logging_setup_permissions import PRINCIPAL, TOKEN
from test_terraform_plan_permissions import plan, row
from test_wif_setup_permissions import NUMBER, POOL, WifHttp

PROVIDER = POOL + "/providers/" + trust.PROVIDER_ID
CREATE_EXPIRY = datetime(2026, 10, 22, 2, tzinfo=timezone.utc)
TRUST_EXPIRY = datetime(2026, 10, 22, 6, tzinfo=timezone.utc)
TRUST_BEFORE = TRUST_EXPIRY - timedelta(seconds=1)


def provider(condition=None):
    return {"name": PROVIDER, "state": "ACTIVE", "disabled": False,
            "displayName": trust.DISPLAY, "description": trust.DESCRIPTION,
            "attributeMapping": copy.deepcopy(trust.MAPPING),
            "attributeCondition": condition or trust.trust_condition(trust.WORKFLOWS[:2]),
            "oidc": {"issuerUri": trust.ISSUER}}


def change():
    before = {"project": trust.PROJECT, "workload_identity_pool_id": "github",
              "workload_identity_pool_provider_id": trust.PROVIDER_ID,
              "attribute_condition": trust.trust_condition(trust.WORKFLOWS[:2]),
              "attribute_mapping": copy.deepcopy(trust.MAPPING), "disabled": False,
              "oidc": [{"issuer_uri": trust.ISSUER, "allowed_audiences": []}]}
    after = {**before, "attribute_condition": trust.trust_condition()}
    return row("google_iam_workload_identity_pool_provider", ["update"], after,
               before=before, address=trust.ADDRESS)


class TrustHttp(WifHttp):
    def __init__(self):
        super().__init__()
        self.provider = provider()
        self.provider_error = None
        self.roles.pop(wif.CREATOR_ROLE)
        self.roles[trust.ROLE] = {"name": trust.ROLE, "stage": "GA", "includedPermissions": [trust.UPDATE]}
        self.policy["bindings"][-1] = {"role": trust.ROLE,
                                      "members": ["serviceAccount:" + PRINCIPAL],
                                      "condition": trust.condition()}

    def __call__(self, request, timeout):
        parsed = urlsplit(request.full_url)
        if parsed.path == "/v1/" + PROVIDER:
            self.calls.append(request)
            self.assert_timeout(timeout)
            assert request.method == "GET"
            assert parse_qs(parsed.query) == {"fields": [
                "name,state,disabled,displayName,description,attributeCondition,attributeMapping,oidc"]}
            if self.provider_error:
                code, status = self.provider_error
                raise HTTPError(request.full_url, code, "metadata failure", {},
                                io.BytesIO(json.dumps({"error": {"code": code, "status": status}}).encode()))
            return io.BytesIO(json.dumps(self.provider).encode())
        return super().__call__(request, timeout)


class ReadonlyTrustTests(unittest.TestCase):
    def verify(self, transport, *, now=TRUST_BEFORE, permissions=(wif.GET,)):
        with (patch.dict(os.environ, {"TERRAFORM_SERVICE_ACCOUNT": PRINCIPAL}),
              patch.object(live.urllib.request, "urlopen", transport)):
            return trust.verify_provider(trust.PROVIDER_URL, permissions, TOKEN,
                                         lambda url, **kwargs: live.read_json(url, TOKEN, **kwargs), now=now)

    def test_exact_old_to_desired_uses_real_child_and_installed_update_only_grant(self):
        transport = TrustHttp()
        self.assertEqual(self.verify(transport), [])
        self.assertEqual(self.verify(transport, permissions=(wif.GET, trust.UPDATE)), [])
        self.assertFalse(any(call.full_url.endswith(":testIamPermissions") for call in transport.calls))
        self.assertTrue(any(call.full_url == proof.TOKENINFO for call in transport.calls))
        self.assertEqual(live.checks("readonly-wif-trust"), [(trust.PROVIDER_URL, (wif.GET,))])

    def test_completed_noop_needs_no_expired_update_or_principal_grant(self):
        transport = TrustHttp()
        transport.provider = provider(trust.trust_condition())
        transport.roles = {}
        transport.policy = {}
        self.assertEqual(self.verify(transport, now=TRUST_EXPIRY + timedelta(days=365)), [])
        self.assertFalse(any(call.full_url == proof.TOKENINFO for call in transport.calls))
        with self.assertRaisesRegex(ValueError, "stale"):
            self.verify(transport, permissions=(wif.GET, trust.UPDATE))
        # Re-entering the separate IAM bootstrap after expiry is an ordinary
        # no-op. It never requests the expiring provider-update permission.
        bootstrap = plan(
            row("google_project_iam_custom_role", ["no-op"],
                {"project": trust.PROJECT, "role_id": trust.ROLE.rsplit("/", 1)[1]}),
            row("google_project_iam_member", ["no-op"],
                {"project": trust.PROJECT, "role": trust.ROLE, "condition": [trust.condition()]}))
        self.assertEqual(plan_checks(bootstrap, project_number=NUMBER, target="iam-bootstrap"), [])
        self.assertNotIn(trust.UPDATE, {p for _, permissions in live.checks("iam-bootstrap") for p in permissions})

    def test_fixed_expiry_does_not_slide(self):
        self.assertEqual(trust.EXPIRES, "2026-10-22T06:00:00Z")
        self.assertEqual(self.verify(TrustHttp(), now=TRUST_EXPIRY - timedelta(microseconds=1)), [])
        for now in (TRUST_EXPIRY, TRUST_EXPIRY + timedelta(seconds=1)):
            with self.subTest(now=now), self.assertRaisesRegex(ValueError, "expired"):
                self.verify(TrustHttp(), now=now)

    def test_old_update_grant_cannot_authorize_new_proposal_even_before_old_expiry(self):
        for now in (CREATE_EXPIRY - timedelta(seconds=1), CREATE_EXPIRY + timedelta(hours=1)):
            transport = TrustHttp()
            transport.policy["bindings"][-1]["condition"] = {
                "title": "readonly-trust-update-expires",
                "description": "Expire temporary read-only provider update authority at 2026-10-22 02:00 UTC.",
                "expression": "request.time < timestamp('2026-10-22T02:00:00Z')",
            }
            with self.subTest(now=now), self.assertRaisesRegex(RuntimeError, "live binding does not exactly match"):
                self.verify(transport, now=now)

    def test_update_window_does_not_extend_original_provider_create_cap(self):
        self.assertEqual(wif.EXPIRES, "2026-10-22T02:00:00Z")
        self.assertEqual(self.verify(TrustHttp(), now=CREATE_EXPIRY), [])
        transport = WifHttp()
        with (patch.dict(os.environ, {"TERRAFORM_SERVICE_ACCOUNT": PRINCIPAL}),
              patch.object(live.urllib.request, "urlopen", transport),
              self.assertRaisesRegex(RuntimeError, "expired")):
            wif.verify_provider(wif.PROVIDER_URL, (wif.GET,), TOKEN,
                                lambda url, **kwargs: live.read_json(url, TOKEN, **kwargs), now=CREATE_EXPIRY)

    def test_actual_child_identity_and_unchanged_configuration_are_required(self):
        for mutation in (
            lambda t: t.project.update(projectId="foreign"),
            lambda t: t.pool.update(name=POOL + "-foreign"),
            lambda t: t.provider.update(name=PROVIDER + "-foreign"),
            lambda t: t.provider.update(disabled=True),
            lambda t: t.provider.update(state="DELETED"),
            lambda t: t.provider.update(displayName="other"),
            lambda t: t.provider.update(description="other"),
            lambda t: t.provider["attributeMapping"].update(**{"google.subject": "assertion.actor"}),
            lambda t: t.provider["oidc"].update(issuerUri="https://other.invalid"),
            lambda t: t.provider["oidc"].update(allowedAudiences=["other"]),
            lambda t: t.provider["oidc"].update(jwksJson="{}"),
            lambda t: t.provider.update(attributeCondition="true"),
        ):
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, RuntimeError)):
                transport = TrustHttp()
                mutation(transport)
                self.verify(transport)

    def test_missing_child_never_enters_create_or_unsupported_permission_fallback(self):
        for status in ((404, "NOT_FOUND"), (403, "PERMISSION_DENIED"), (500, "INTERNAL")):
            transport = TrustHttp()
            transport.provider_error = status
            with self.subTest(status=status), self.assertRaises(proof.ProbeHTTPError):
                self.verify(transport)
            self.assertFalse(any(call.full_url.endswith(":testIamPermissions") or call.full_url == proof.TOKENINFO
                                 for call in transport.calls))

    def test_wrong_principal_or_broader_update_grant_cannot_authorize_repair(self):
        for mutation in (
            lambda t: t.policy["bindings"][-1].pop("condition"),
            lambda t: t.policy["bindings"][-1]["condition"].update(expression="true"),
            lambda t: t.policy["bindings"][-1]["members"].append("serviceAccount:other@example.com"),
            lambda t: t.roles[trust.ROLE]["includedPermissions"].append(wif.CREATE),
            lambda t: t.roles[trust.ROLE].update(deleted=True),
            lambda t: t.identity.update(email="delegated@example.com"),
            lambda t: t.identity.pop("email_verified"),
        ):
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                transport = TrustHttp()
                mutation(transport)
                self.verify(transport)

    def test_saved_plan_derives_only_exact_existing_child_get_and_update(self):
        required = dict(plan_checks(plan(change()), project_number=NUMBER, target="readonly-wif-trust"))
        self.assertEqual(required, {trust.PROVIDER_URL: tuple(sorted((wif.GET, trust.UPDATE)))})
        self.assertEqual(plan_checks(plan(), project_number=NUMBER, target="readonly-wif-trust"), [])

    def test_saved_plan_rejects_replacement_unknowns_extra_changes_and_other_fields(self):
        mutations = [
            lambda c: c["change"].update(actions=["create"]),
            lambda c: c["change"].update(actions=["delete", "create"]),
            lambda c: c["change"].update(actions=["delete"]),
            lambda c: c.update(address=trust.ADDRESS + "_other"),
            lambda c: c["change"].update(after_unknown={"attribute_condition": True}),
            lambda c: c["change"]["before"].update(attribute_condition="true"),
            lambda c: c["change"]["after"].update(attribute_condition="true"),
        ]
        for field, value in (("attribute_mapping", {}), ("oidc", []), ("disabled", True),
                             ("project", "foreign"), ("workload_identity_pool_id", "other"),
                             ("workload_identity_pool_provider_id", "other")):
            mutations.append(lambda c, field=field, value=value: c["change"]["after"].update({field: value}))
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                current = copy.deepcopy(change())
                mutate(current)
                plan_checks(plan(current), project_number=NUMBER, target="readonly-wif-trust")
        with self.assertRaisesRegex(ValueError, "at most"):
            plan_checks(plan(change(), row("google_project_iam_member", ["update"], {})),
                        project_number=NUMBER, target="readonly-wif-trust")

    def test_cli_reads_saved_json_and_scoped_token_without_gcloud_fallback(self):
        transport = TrustHttp()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps(plan(change())))
            with (patch.dict(os.environ, {"TERRAFORM_SERVICE_ACCOUNT": PRINCIPAL, proof.TOKEN_ENV: TOKEN}),
                  patch.object(live.urllib.request, "urlopen", transport),
                  patch.object(live.subprocess, "check_output", return_value=NUMBER) as command,
                  patch("sys.argv", ["deployment_permissions.py", "--target", "readonly-wif-trust", "--plan-json", str(path)]),
                  patch.object(trust, "datetime") as clock):
                clock.now.return_value = TRUST_BEFORE
                clock.fromisoformat.side_effect = __import__("datetime").datetime.fromisoformat
                self.assertIsNone(live.main())
                command.assert_called_once_with(["gcloud", "projects", "describe", trust.PROJECT,
                                                 "--format=value(projectNumber)"], text=True)


if __name__ == "__main__":
    unittest.main()
