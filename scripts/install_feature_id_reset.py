#!/usr/bin/env python3
"""Install an immutable reviewed reset only inside the protected workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingestion.common import publication as p
from ingestion.common.reset_controls import GoogleControlReader, PROJECT, PUBLISHER_ACCOUNT, DEPLOYER_ACCOUNT
from ingestion.common.reset_installation import install_reset, validate_reset_plan
from scripts import dataset_mutation_authorization as auth


def authorized_plan(directory: Path, digest: str, *, api, env):
    auth.require_production_context(env)
    envelope = auth.verified_envelope(api, directory, digest, env)
    p.require(envelope["outcome"] == "mutation" and set(envelope["document"]) == {"plan_version", "finalization_version", "publish"},
              "reset requires one immutable publish plan")
    plan = envelope["document"]["publish"]
    validate_reset_plan(plan)
    return envelope, plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--check-only", action="store_true", help="Verify immutable review authority before authentication; never installs.")
    args = parser.parse_args(argv)
    api = auth.GitHub()
    envelope, plan = authorized_plan(args.directory, args.expected_sha256, api=api, env=dict(os.environ))
    if args.check_only:
        print("Reviewed reset plan verified; paused/drained job checks still required.")
        return 0

    # Import credential/storage packages only after the immutable authorization
    # boundary. Reuse the protected publisher and deployer; no new IAM grants.
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud import storage
    from ingestion.common.publication_gcs import GcsStore

    credentials, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    p.require(getattr(credentials, "service_account_email", None) == PUBLISHER_ACCOUNT, "approved publisher identity is required")
    control_path = os.environ.get("RESET_CONTROL_CREDENTIALS")
    p.require(bool(control_path), "protected deployer credentials are required for job checks")
    control_credentials, _ = google.auth.load_credentials_from_file(control_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    p.require(getattr(control_credentials, "service_account_email", None) == DEPLOYER_ACCOUNT, "protected deployer identity is required for job checks")
    reader = GoogleControlReader(AuthorizedSession(control_credentials))

    def guard():
        auth.revalidate(api, envelope)
        reader.check_quiescent(plan["asset_slug"])

    result = install_reset(GcsStore(storage.Client(project=PROJECT, credentials=credentials)), plan,
                           authorization=auth.identity_digests(envelope), check_authority_and_jobs=guard)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
