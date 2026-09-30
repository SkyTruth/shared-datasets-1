from pathlib import Path
import unittest

from ingestion.common import publication as p
from scripts.export_feature_id_org_evidence import export_evidence
from test_publication import publication_temp_directory
from test_reset_fence import ControlFixture


class OrganizationEvidenceTests(unittest.TestCase):
    def test_exports_full_policy_details_without_requiring_or_approving_a_fence(self):
        reader = ControlFixture()
        # Evidence is useful before quiescence. These live states must not be read.
        reader.responses[reader.scheduler_urls[0]]["state"] = "ENABLED"
        reader.responses[reader.account_url]["accounts"][0]["disabled"] = False
        with publication_temp_directory() as tmp:
            output = Path(tmp) / "organization.json"
            report = export_evidence(reader, output)
            result = p.strict_json(output.read_bytes())
            self.assertEqual(report["sha256"], p.digest(output.read_bytes()))
            self.assertFalse(report["reset_authorized"])
            self.assertTrue(all(policy["rules"] for policy in result["deny_policies"].values()))
            self.assertTrue(result["custom_roles"][0]["includedPermissions"])
            self.assertFalse(any(url in reader.scheduler_urls or url == reader.account_url for url, _, _ in reader.calls))
            before = output.read_bytes()
            with self.assertRaisesRegex(p.PublicationError, "already exists"):
                export_evidence(reader, output)
            self.assertEqual(output.read_bytes(), before)

    def test_denied_incomplete_or_wrong_ancestry_never_produces_success_evidence(self):
        for case in ("denied", "missing_deny_detail", "wrong_ancestry"):
            with self.subTest(case=case), publication_temp_directory() as tmp:
                reader = ControlFixture()
                if case == "wrong_ancestry":
                    reader.responses[reader.project_url]["parent"] = "organizations/other"
                elif case == "missing_deny_detail":
                    del reader.responses[f"https://iam.googleapis.com/v2/{reader.deny_names[1]}"]
                else:
                    def denied(_resource):
                        raise p.PublicationError("writer-fence read failed (403)")
                    reader.policy = denied
                output = Path(tmp) / "organization.json"
                with self.assertRaises((p.PublicationError, KeyError)):
                    export_evidence(reader, output)
                self.assertFalse(output.exists())
