#!/usr/bin/env python3
"""Export missing organization policy evidence; no cloud writes or reset authority."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingestion.common import publication as p
from ingestion.common.reset_fence import GoogleControlReader, ORGANIZATION, PROJECT, PROJECT_NUMBER


def export_evidence(reader: GoogleControlReader, output: Path) -> dict:
    p.require(not output.exists() and not output.is_symlink(), "evidence output already exists; choose a new file")
    started = dt.datetime.now(dt.UTC).isoformat()
    project = reader.read(f"https://cloudresourcemanager.googleapis.com/v3/projects/{PROJECT}")
    resource = f"organizations/{ORGANIZATION}"
    p.require(project.get("name") == f"projects/{PROJECT_NUMBER}" and project.get("parent") == resource,
              "project ancestry changed; review organization evidence scope")
    # This intentionally omits runtime/fence checks: an administrator can export
    # policy evidence before old writers have been disabled. It cannot approve it.
    value = {"schema_version": 1, "kind": "feature_id_organization_evidence", "project": PROJECT,
             "organization": ORGANIZATION, "started_at": started, "allow_policy": reader.policy(resource),
             "deny_policies": reader.deny_policies(resource),
             "custom_roles": sorted(reader.pages(f"https://iam.googleapis.com/v1/{resource}/roles", "roles",
                                                  params={"view": "FULL"}), key=lambda row: row["name"])}
    value["completed_at"] = dt.datetime.now(dt.UTC).isoformat()
    encoded = p.canonical(value)
    # Collect every required response before creating the output. Exclusive open
    # also refuses an output created by another process during collection.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(encoded)
    return {"output": str(output), "sha256": p.digest(encoded), "bytes": len(encoded),
            "organization": ORGANIZATION, "reset_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    with AuthorizedSession(credentials) as session:
        report = export_evidence(GoogleControlReader(session), args.output)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
