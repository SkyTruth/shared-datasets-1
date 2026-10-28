#!/usr/bin/env python3
"""Inspect held EAMLIS bytes, load the private staging catalog, or prepare a PR plan.

Only the protected approved-dataset workflow can resolve a production hold.
Preview preparation is not publication authority and never rebuilds dataset bytes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingestion.eamlis_monthly import source_quality as loss


def prepare_plan(client):
    guard, value, review = loss.inspection(client)
    loss.require(guard.state["owner"] is None, "wait for the current check to finish before preparing review")
    promotions = loss.expected_promotions(value)
    for item in promotions:
        bucket, name = item["destination_uri"][5:].split("/", 1)
        from google.api_core.exceptions import NotFound
        blob = client.bucket(bucket).blob(name)
        try:
            blob.reload()
            generation = str(blob.generation)
        except NotFound:
            generation = ""
        loss.require(not generation or "/latest/" in name, "candidate dated release already exists; inspect partial publication")
        item["destination_generation"] = generation
    plan = {"asset_slug": loss.SLUG, "proposal_id": "source-loss-" + value["candidate_id"],
            "promotions": promotions, "source_loss_review": review}
    loss.validate_candidate_plan(client, plan)
    from ingestion.eamlis_monthly import identity_state
    identity_state.validate_candidate(guard, value)
    return plan


def download_preview(client, directory, candidate_id):
    """Publisher/read identity downloads exact inputs; no preview or production writes."""
    directory.mkdir(parents=True, exist_ok=False)
    guard, value, review = loss.inspection(client)
    loss.require(value["candidate_id"] == candidate_id, "candidate changed; inspect the current hold")
    releases = []
    if guard.before:
        from scripts import release_feature_model as model
        prior = guard.before["release"]
        files = [dict(f) for f in prior.get("files", [])]
        loss.require(any(f["path"].endswith(".manifest.json") for f in files), "baseline lacks complete staging comparison artifacts")
        manifest_ref = next(f for f in files if f["path"].endswith(".manifest.json"))
        manifest = json.loads(loss.pinned(client, manifest_ref))
        model.validate_release_manifest(manifest, expected_asset_slug=loss.SLUG,
                                        expected_release=prior["date"], require_generations=True)
        artifacts = {a["path"]: a for a in manifest["artifacts"]}
        # Release indexes legitimately omit hashes for extra locale/CSV files.
        # The checksum-verified canonical manifest owns their byte evidence.
        for f in files:
            if f["path"] == manifest_ref["path"]:
                continue
            a = artifacts[f["path"]]
            loss.require(str(a["generation"]) == str(f["generation"]) and a["size"] == f["size"],
                         "baseline manifest and release index disagree")
            f["sha256"] = a["sha256"]
        releases.append({"date": prior["date"], "rows": prior["rows"],
                         "source_version": "Published baseline", "files": files})
    releases.append({"date": value["release"], "rows": value["geometry_count"],
        "source_version": "HELD candidate " + candidate_id,
        "files": [{k: v for k, v in f.items() if k != "suffix"} |
                  {"filename": loss.SLUG + f["suffix"]} for f in value["files"] if f["role"] != "run_record"]})
    for n, release in enumerate(releases):
        local = directory / str(n)
        local.mkdir()
        for f in release["files"]:
            filename = f.get("filename", f["path"].rsplit("/", 1)[1])
            loss.require(filename.startswith(loss.SLUG + ".") and Path(filename).name == filename, "invalid preview artifact filename")
            data = loss.pinned(client, f)
            (local / filename).write_bytes(data)
            f["filename"] = filename
            f["sha256"] = loss.digest(data)
            f["size"] = len(data)
    (directory / "preview.json").write_bytes(loss.encode({"schema_version": 1, "review": review, "releases": releases}))


def upload_preview(client, directory):
    """Preview identity only: never reads or writes production during upload."""
    from google.api_core.exceptions import NotFound
    from ingestion.common import release_index
    from ingestion.common.runtime import content_type_for
    snapshot = json.loads((directory / "preview.json").read_bytes())
    loss.normalize_review(snapshot["review"])
    bucket = client.bucket(loss.PREVIEW_BUCKET)
    index_data, index_ref = loss.read(bucket, loss.INDEX)
    index = json.loads(index_data) if index_data is not None else release_index.empty_release_index(loss.SLUG)
    results = []
    for n, release in enumerate(snapshot["releases"]):
        refs = []
        manifest_file = None
        # A prepared manifest is rewritten only for preview paths/generations;
        # FGB, tiles, canonical and localized metadata stay byte-identical.
        for f in release["files"]:
            path = directory / str(n) / f["filename"]
            data = path.read_bytes()
            loss.require(len(data) == f["size"] and loss.digest(data) == f["sha256"], "downloaded preview bytes changed")
            if f["filename"].endswith(".manifest.json"):
                manifest_file = (path, json.loads(data))
                continue
            name = f"{loss.ROOT}/releases/{release['date']}/{f['filename']}"
            blob = bucket.blob(name)
            try:
                blob.reload()
                expected = int(blob.generation)
            except NotFound:
                expected = 0
            blob.cache_control = loss.NO_CACHE
            blob.upload_from_filename(path, content_type=content_type_for(path), if_generation_match=expected)
            fmt = release_index.FORMAT_EXTENSIONS.get("." + f["filename"].split(".", 1)[1], path.suffix[1:])
            if ".metadata." in f["filename"] and f["filename"].endswith(".ndjson.gz"):
                fmt = "metadata"
            refs.append({"path": f"gs://{bucket.name}/{name}", "generation": int(blob.generation),
                         "size": f["size"], "sha256": f["sha256"], "format": fmt})
        loss.require(manifest_file is not None, "preview requires a complete release manifest")
        path, manifest = manifest_file
        by_name = {r["path"].rsplit("/", 1)[1]: r for r in refs}
        for artifact in manifest["artifacts"]:
            filename = artifact["path"].rsplit("/", 1)[1]
            artifact.pop("latest_path", None)
            artifact.pop("latest_generation", None)
            artifact["path"] = f"gs://{bucket.name}/{loss.ROOT}/releases/{release['date']}/{filename}"
            if artifact["role"] != "manifest":
                artifact.update({k: by_name[filename][k] for k in ("path", "generation", "size", "sha256")})
        data = loss.encode(manifest)
        ref = loss.put(bucket, f"{loss.ROOT}/releases/{release['date']}/{path.name}", data,
                       generation=current_generation(bucket, f"{loss.ROOT}/releases/{release['date']}/{path.name}"))
        refs.append({**ref, "format": "manifest"})
        entry = {"date": release["date"], "rows": release["rows"], "files": refs,
                 "source_version": release["source_version"],
                 "release_path": f"gs://{bucket.name}/{loss.ROOT}/releases/{release['date']}/"}
        index["releases"] = [r for r in index["releases"] if r["date"] != entry["date"]] + [entry]
        index["latest_release"] = entry
        index["updated_at"] = loss.now()
        results.append(entry)
    loss.put(bucket, loss.INDEX, loss.encode(index), generation=int(index_ref["generation"]) if index_ref else 0)
    (directory / "preview-results.json").write_bytes(loss.encode(results))
    return results


def current_generation(bucket, name):
    from google.api_core.exceptions import NotFound
    blob = bucket.blob(name)
    try:
        blob.reload()
        return int(blob.generation)
    except NotFound:
        return 0


def collect_preview_indexes(client, directory):
    directory.mkdir(parents=True, exist_ok=False)
    for blob in client.list_blobs(loss.PREVIEW_BUCKET, prefix="_catalog/releases/"):
        name = blob.name.removeprefix("_catalog/releases/")
        loss.require(re.fullmatch(r"[a-z0-9-]+\.json", name), "unexpected preview release-index path")
        data = blob.download_as_bytes(if_generation_match=int(blob.generation))
        (directory / name).write_bytes(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    plan = sub.add_parser("prepare-plan")
    plan.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    download = sub.add_parser("download-preview")
    download.add_argument("--candidate-id", required=True)
    download.add_argument("--directory", type=Path, required=True)
    upload = sub.add_parser("upload-preview")
    upload.add_argument("--directory", type=Path, required=True)
    indexes = sub.add_parser("collect-preview-indexes")
    indexes.add_argument("--directory", type=Path, required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--plan-json", type=Path, required=True)
    args = parser.parse_args(argv)
    from google.cloud import storage
    client = storage.Client(project="shared-datasets-1")
    if args.command == "status":
        guard = loss.Guard(client.bucket(loss.BUCKET))
        print(json.dumps(guard.load()[0], indent=2))
    elif args.command == "prepare-plan":
        from scripts import reviewed_dataset_plan
        document = {"plan_version": 1, "finalization_version": reviewed_dataset_plan.FINALIZATION_VERSION,
                    "publish": prepare_plan(client)}
        reviewed_dataset_plan.write_document(document, repo_root=args.repo_root)
        print(reviewed_dataset_plan.render_document(document))
    elif args.command == "download-preview":
        loss.require(re.fullmatch(r"[0-9a-f]{64}", args.candidate_id), "invalid candidate ID")
        download_preview(client, args.directory, args.candidate_id)
    elif args.command == "upload-preview":
        print(json.dumps(upload_preview(client, args.directory), indent=2))
    elif args.command == "collect-preview-indexes":
        collect_preview_indexes(client, args.directory)
    else:
        loss.finish_promotion(client, json.loads(args.plan_json.read_bytes()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
