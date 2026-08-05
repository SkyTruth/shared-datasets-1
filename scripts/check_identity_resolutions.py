#!/usr/bin/env python3
"""Check reviewed feature-identity decisions against the previous release.

Which *action* a decision may use is not a judgement call. Given a decision's
identity key and the previous release, one fact settles it: either the key
already owns a feature_id or it does not.

    key already owns a feature_id  -> keep_previous_key_mapping (or, deliberately,
                                      assign_new_feature_id).
                                      reuse_previous_feature_id would hand the
                                      record a different feature's ID and merge
                                      two records.
    key is new                     -> reuse_previous_feature_id (or, deliberately,
                                      assign_new_feature_id).
                                      keep_previous_key_mapping is meaningless:
                                      there is no mapping to keep.

Both rules are decided by the data, so this runs in CI on the pull request that
adds the decisions, before a job spends an hour rediscovering it. The remaining
choice is semantic -- is this the same feature? -- and stays with the reviewer.

Reads only the published metadata sidecar for each asset's latest release; it
writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import release_feature_model  # noqa: E402

DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_PROJECT = "shared-datasets-1"
RESOLUTIONS_DIR = Path("catalog/feature-identity-resolutions")


def decision_files(repo_root: Path, only: Iterable[str] = ()) -> list[Path]:
    directory = repo_root / RESOLUTIONS_DIR
    wanted = {slug.strip() for slug in only if slug.strip()}
    files = sorted(path for path in directory.glob("*.json"))
    if wanted:
        files = [path for path in files if path.stem in wanted]
    return files


def asset_root_from_catalog(repo_root: Path, asset_slug: str) -> str:
    """Resolve an asset's bucket prefix from the catalog's canonical path."""
    import csv

    catalog = repo_root / "catalog/shared-datasets-catalog.csv"
    with catalog.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("asset_slug") != asset_slug:
                continue
            canonical = (row.get("canonical_path") or "").strip()
            if not canonical.startswith("gs://"):
                raise SystemExit(f"{asset_slug}: catalog canonical_path is not a gs:// URI")
            object_name = canonical[len("gs://"):].partition("/")[2]
            return object_name.rsplit("/latest/", 1)[0] if "/latest/" in object_name else object_name
    raise SystemExit(f"{asset_slug}: no catalog row; add the asset doc before its decisions")


def load_previous_feature_ids(
    *,
    bucket_name: str,
    project: str,
    asset_root: str,
    asset_slug: str,
) -> dict[tuple[str, ...], str]:
    from google.cloud import storage

    client = storage.Client(project=project)
    bucket = client.bucket(bucket_name)
    object_name = f"{asset_root}/latest/{asset_slug}.metadata.ndjson.gz"
    blob = bucket.get_blob(object_name)
    if blob is None:
        raise SystemExit(
            f"{asset_slug}: no published metadata sidecar at gs://{bucket_name}/{object_name}; "
            "cannot check decisions against a previous release"
        )
    records = release_feature_model.read_metadata_sidecar_bytes(
        blob.download_as_bytes(),
        label=f"gs://{bucket_name}/{object_name}",
    )
    mapping: dict[tuple[str, ...], str] = {}
    for record in records:
        key = release_feature_model.identity_key_from_record(record)
        feature_id = str(record.get("feature_id") or "").strip()
        if key and feature_id:
            mapping[key] = feature_id
    return mapping


def check_file(
    path: Path,
    *,
    previous_feature_ids: dict[tuple[str, ...], str],
) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    asset_slug = payload.get("asset_slug")
    problems: list[str] = []
    for index, decision in enumerate(payload.get("decisions") or [], start=1):
        action = str(decision.get("action") or "").strip()
        if action not in release_feature_model.IDENTITY_RESOLUTION_ACTIONS:
            problems.append(f"{path.name} decision {index}: unsupported action {action!r}")
            continue
        try:
            identity_key = release_feature_model.normalize_identity_key(
                decision.get("new_identity_key"), label="new_identity_key"
            )
        except release_feature_model.ReleaseFeatureModelError as exc:
            problems.append(f"{path.name} decision {index}: {exc}")
            continue
        ambiguity = release_feature_model.IdentityAmbiguity(
            ambiguity_type=release_feature_model.IDENTITY_AMBIGUITY_TYPE_UNKNOWN,
            identity_key=identity_key,
            geometry_hash=str(decision.get("new_geometry_hash") or ""),
            properties_hash=str(decision.get("new_properties_hash") or ""),
            matching_geometry_feature_ids=tuple(decision.get("matching_geometry_feature_ids") or ()),
            matching_properties_feature_ids=tuple(decision.get("matching_properties_feature_ids") or ()),
        )
        reuse_feature_id = decision.get("reuse_feature_id")
        problem = release_feature_model.identity_decision_legality(
            action=action,
            identity_key=identity_key,
            reuse_feature_id=None if reuse_feature_id is None else str(reuse_feature_id).strip(),
            ambiguity=ambiguity,
            previous_feature_ids=previous_feature_ids,
        )
        if problem:
            problems.append(f"{path.name} decision {index} ({asset_slug}, release {decision.get('release')}): {problem}")
    return problems


def summarize(path: Path, previous_feature_ids: dict[tuple[str, ...], str]) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    known = 0
    for decision in payload.get("decisions") or []:
        counts[str(decision.get("action"))] = counts.get(str(decision.get("action")), 0) + 1
        try:
            key = release_feature_model.normalize_identity_key(
                decision.get("new_identity_key"), label="new_identity_key"
            )
        except release_feature_model.ReleaseFeatureModelError:
            continue
        if key in previous_feature_ids:
            known += 1
    total = sum(counts.values())
    actions = ", ".join(f"{action}={count}" for action, count in sorted(counts.items()))
    return f"{path.name}: {total} decision(s) [{actions}]; {known} key(s) already own a feature_id"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument(
        "--asset-slug",
        action="append",
        default=[],
        help="Check only these asset slugs. Defaults to every decisions file.",
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "Apply the same rule using each decision's declared "
            f"{release_feature_model.DECLARED_PREVIOUS_FEATURE_ID_FIELD} instead of reading the "
            "bucket. Used on pull requests, where the job later verifies the declarations."
        ),
    )
    args = parser.parse_args(argv)

    files = decision_files(args.repo_root, args.asset_slug)
    if not files:
        print("No feature-identity decision files to check.")
        return 0

    problems: list[str] = []
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        asset_slug = str(payload.get("asset_slug") or path.stem)
        if asset_slug != path.stem:
            problems.append(f"{path.name}: asset_slug {asset_slug!r} does not match the file name")
            continue
        decisions = payload.get("decisions") or []
        if args.offline:
            missing = [
                index
                for index, decision in enumerate(decisions, start=1)
                if release_feature_model.DECLARED_PREVIOUS_FEATURE_ID_FIELD not in decision
            ]
            if missing:
                problems.append(
                    f"{path.name}: decisions "
                    + ", ".join(str(index) for index in missing)
                    + f" are missing {release_feature_model.DECLARED_PREVIOUS_FEATURE_ID_FIELD}, so the "
                    "action cannot be checked without bucket access"
                )
                continue
            previous_feature_ids = release_feature_model.declared_previous_feature_ids(decisions)
        else:
            asset_root = asset_root_from_catalog(args.repo_root, asset_slug)
            previous_feature_ids = load_previous_feature_ids(
                bucket_name=args.bucket,
                project=args.project,
                asset_root=asset_root,
                asset_slug=asset_slug,
            )
        print(summarize(path, previous_feature_ids))
        problems.extend(check_file(path, previous_feature_ids=previous_feature_ids))

    if problems:
        print("\nDecisions whose action is ruled out by the previous release:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("\nEvery decision uses an action its identity key's state allows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
