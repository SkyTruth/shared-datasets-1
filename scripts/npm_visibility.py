"""Confirm accepted npm bytes without publishing again or weakening verification."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import operational_retry as retry
from scripts import sdk_release_authorization as release


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "shared-datasets-npm-visibility-v1"
TRANSPORT_REASONS = {"HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "connection_reset",
                     "connection_refused", "dns_temporary", "request_timeout"}


class VisibilityPending(RuntimeError):
    pass


def inspect(candidate_path: Path, candidate: dict, root: Path) -> None:
    timeout = retry.request_timeout(60)
    # The child emits explicit transport facts only after validating local
    # bytes. A verifier crash, timeout, nonzero exit, or malformed result is
    # fatal; none can be mistaken for successful publication or absence.
    output = subprocess.check_output(
        ["node", str(root / "api/typescript/scripts/release-policy.mjs"), "visibility", str(candidate_path),
         str(max(1, int(min(30, timeout * 0.9) * 1000)))],
        cwd=root / "api/typescript", text=True, timeout=timeout,
        env={**os.environ, "GITHUB_OUTPUT": ""},
    )
    value = json.loads(output)
    release.require(isinstance(value, dict) and set(value) == {"schema", "name", "version", "integrity", "status", "reason"}
                    and value["schema"] == SCHEMA
                    and all(value[key] == candidate[key] for key in ("name", "version", "integrity")),
                    "npm visibility verifier returned incompatible or wrong-package facts")
    if value["status"] == "visible" and value["reason"] == "verified_integrity":
        return
    if (value["status"] == "absent" and value["reason"] == "HTTP 404"
            or value["status"] == "transient" and value["reason"] in TRANSPORT_REASONS):
        raise VisibilityPending(value["reason"])
    raise ValueError("npm visibility verifier returned unsupported status/reason")


def confirm(candidate_path: Path, *, root: Path = ROOT) -> None:
    candidate_path = candidate_path.resolve()
    candidate = release.local_candidate(candidate_path, root)
    with retry.operation(profile=retry.UNATTENDED):
        retry.run_read(lambda: inspect(candidate_path, candidate, root),
                       classify=lambda error: str(error) if isinstance(error, VisibilityPending) else None,
                       label="npm accepted-publication visibility")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--accepted-publication", action="store_true",
                        help="caller verified publication succeeded, or exact bytes were already present")
    args = parser.parse_args()
    if not args.accepted_publication:
        parser.error("propagation waits require confirmed accepted publication")
    try:
        confirm(args.candidate)
    except VisibilityPending as error:
        parser.exit(1, "Registry has not confirmed the exact tested package integrity within the shared recovery budget "
                    f"({error}); inspect the existing publication before recovery\n")
    except (OSError, ValueError, KeyError, release.DeploymentError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"NPM_VISIBILITY_NOT_VERIFIED: {error}\n")
    print("Verified accepted npm publication retained the exact tested package integrity.")


if __name__ == "__main__":
    main()
