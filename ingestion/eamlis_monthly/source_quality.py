"""EAMLIS source-quality holds. A held candidate never grants publication authority.

This module is stdlib-only at import time: reviewed-plan validation also runs
before application dependencies are installed. GCS exceptions are imported at
the storage boundary. All control changes use generation preconditions.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


SLUG = "eamlis-abandoned-mine-land-inventory"
ROOT = "300-infrastructure-industrial/320-mining/" + SLUG
BUCKET = "skytruth-shared-datasets-1"
PREVIEW_BUCKET = BUCKET + "-preview"
CONTROL = f"_catalog/ingestion-holds/{SLUG}.json"
INDEX = f"_catalog/releases/{SLUG}.json"
POLICY_FILE = Path(__file__).resolve().parents[2] / "catalog/ingestion-quality.json"
NO_CACHE = "no-cache, max-age=0, must-revalidate"
REVIEW_VERSION = 1


class SourceLossError(RuntimeError):
    """Invalid state, stale review, or conflicting execution; no publication allowed."""


def require(condition, message):
    if not condition:
        raise SourceLossError(message)


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def policy():
    value = json.loads(POLICY_FILE.read_text())
    require(value["schema_version"] == 1, "unsupported ingestion quality policy")
    limit = value["assets"][SLUG]["maximum_geometry_count_drop_percent"]
    require(type(limit) is int and 0 <= limit < 100, "invalid geometry-count loss threshold")
    return limit


def control_target(name):
    return name == CONTROL or name.startswith("_catalog/ingestion-holds/")


def read(bucket, name):
    from google.api_core.exceptions import NotFound
    blob = bucket.blob(name)
    try:
        blob.reload()
    except NotFound:
        return None, None
    generation = int(blob.generation)
    data = blob.download_as_bytes(if_generation_match=generation)
    return data, {"path": f"gs://{bucket.name}/{name}", "generation": str(generation),
                  "size": len(data), "sha256": digest(data)}


def pinned(client, ref):
    bucket, name = ref["path"][5:].split("/", 1)
    blob = client.bucket(bucket).blob(name, generation=int(ref["generation"]))
    data = blob.download_as_bytes(if_generation_match=int(ref["generation"]))
    require(len(data) == ref["size"] and digest(data) == ref["sha256"], "candidate bytes changed or are corrupt")
    return data


def put(bucket, name, data, *, generation):
    blob = bucket.blob(name)
    blob.cache_control = NO_CACHE
    blob.upload_from_string(data, content_type="application/json", if_generation_match=generation)
    return {"path": f"gs://{bucket.name}/{name}", "generation": str(blob.generation),
            "size": len(data), "sha256": digest(data)}


def immutable_file(bucket, name, path):
    """Retries may reuse identical staged bytes, never overwrite them."""
    from google.api_core.exceptions import PreconditionFailed
    from ingestion.common.runtime import content_type_for
    data = path.read_bytes()
    blob = bucket.blob(name)
    try:
        blob.upload_from_filename(path, content_type=content_type_for(path), if_generation_match=0)
    except PreconditionFailed:
        existing, _ = read(bucket, name)
        require(existing == data, "immutable candidate object already has different bytes")
        blob.reload()
    return {"path": f"gs://{bucket.name}/{name}", "generation": str(blob.generation),
            "size": len(data), "sha256": digest(data), "content_type": content_type_for(path)}


def baseline(bucket):
    data, ref = read(bucket, INDEX)
    release = json.loads(data).get("latest_release") if data else None
    if release is None:
        from google.api_core.exceptions import NotFound
        try:
            bucket.blob(f"{ROOT}/latest/{SLUG}.fgb").reload()
        except NotFound:
            return None
        raise SourceLossError("published EAMLIS exists without its counted baseline; repair the index before retrying")
    require(type(release.get("rows")) is int and release["rows"] > 0, "published baseline has no verified geometry count")
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", release.get("date", "")), "invalid published baseline date")
    return {"release": release, "token": digest(encode(release)), "index": ref}


def baseline_token(value):
    return value["token"] if value else None


def execution_completed(owner):
    """Only Google's terminal execution evidence permits reclaiming a dead claim.

    https://cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions
    An absent, expired, unauthorized or still-running execution is not proof.
    """
    require(re.fullmatch(r"eamlis-monthly-[a-z0-9-]+", owner), "unrecognized interrupted claim; reviewed recovery is required")
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    job = "projects/shared-datasets-1/locations/us-central1/jobs/eamlis-monthly"
    name = f"{job}/executions/{owner}"
    with AuthorizedSession(credentials) as session:
        response = session.get("https://run.googleapis.com/v2/" + name, timeout=30)
        response.raise_for_status()
        value = response.json()
    require(value.get("name") == name and value.get("job") == job, "execution identity differs from claim")
    return bool(value.get("completionTime")) and not value.get("reconciling", False) and value.get("runningCount", 0) == 0


def validate_control(state):
    from ingestion.eamlis_monthly import identity_state
    require(isinstance(state, dict) and type(state.get("schema_version")) is int
            and state["schema_version"] in {1, 2}, "invalid source-loss control version")
    fields = {"schema_version", "asset_slug", "owner", "hold"}
    if state["schema_version"] == 2:
        fields.add("identity")
    require(set(state) == fields and state["asset_slug"] == SLUG, "invalid source-loss control state")
    require(state["owner"] is None or isinstance(state["owner"], str), "invalid publication claim")
    if state["hold"] is not None:
        h = state["hold"]
        require(isinstance(h, dict) and h.get("id") and h.get("notification") in
                {"pending", "sending", "sent", "failed", "unknown"}, "invalid source-loss hold")
    if state["schema_version"] == 2:
        identity_state.validate(state["identity"])
    return state


class Guard(AbstractContextManager):
    """Serialize the asset's checks/publications and retain holds across executions.

    There is no timeout-based lock stealing. Reclaiming a completed Cloud Run
    execution preserves (or creates) a hold; it never authorizes publication.
    """
    def __init__(self, bucket, *, owner=None):
        self.bucket = bucket
        self.owner = owner or os.environ.get("CLOUD_RUN_EXECUTION") or str(uuid.uuid4())
        self.state = None
        self.generation = 0
        self.before = None

    def load(self):
        data, ref = read(self.bucket, CONTROL)
        if data is None:
            return {"schema_version": 1, "asset_slug": SLUG, "owner": None, "hold": None}, 0
        return validate_control(json.loads(data)), int(ref["generation"])

    def save(self):
        validate_control(self.state)
        ref = put(self.bucket, CONTROL, encode(self.state), generation=self.generation)
        self.generation = int(ref["generation"])

    def __enter__(self):
        self.state, self.generation = self.load()
        from ingestion.eamlis_monthly import identity_state
        if self.state["schema_version"] == 1:
            # An incomplete installation must not change the captured legacy
            # generation: an exact reviewed retry can still finish its CAS.
            self.before = baseline(self.bucket)
            identity_state.selected_ledger(self)
        interrupted = self.state["owner"]
        if interrupted is not None:
            require(execution_completed(interrupted), "EAMLIS has an active or interrupted source-quality claim; inspect it before retrying")
        self.state["owner"] = self.owner
        self.save()
        try:
            self.before = baseline(self.bucket)
            identity_state.selected_ledger(self)
        except Exception:
            self.state["owner"] = None
            self.save()
            raise
        if interrupted is not None:
            observation = {"geometry_count": None, "interrupted_execution": interrupted, "checked_at": now()}
            self.block("interrupted_execution", observation)
        return self

    def __exit__(self, *_exc):
        self.state["owner"] = None
        self.save()

    @property
    def held(self):
        return self.state["hold"] is not None

    def block(self, reason, observation):
        if not self.held:
            identity = {"baseline": self.before, "first_observation": observation, "reason": reason}
            self.state["hold"] = {"id": digest(encode(identity)), "baseline": self.before,
                "reason": reason, "created_at": now(), "notification": "pending", "candidate": None}
        self.state["hold"]["observation"] = observation
        self.save()

    def assess(self, count, *, selection, previous_selection=None):
        require(type(count) is int and count >= 0, "invalid geometry count")
        observation = {"geometry_count": count, "selection": selection, "checked_at": now(),
                       "maximum_drop_percent": policy()}
        if count == 0:
            self.block("empty_source", observation)
        elif self.before and count * 100 < self.before["release"]["rows"] * (100 - policy()):
            self.block("geometry_count_drop", observation)
        elif previous_selection is not None and selection != previous_selection:
            self.block("source_selection_changed", observation)
        elif self.held:
            self.state["hold"]["observation"] = observation
            self.save()
        return self.held

    def attach(self, ref):
        require(self.held, "cannot attach a candidate without a hold")
        self.state["hold"]["candidate"] = ref
        self.save()

    def check_publication(self):
        require(not self.held, "source-quality hold forbids automatic publication")
        require(baseline_token(baseline(self.bucket)) == baseline_token(self.before), "published baseline changed during extraction")
        self.require_owner()

    def require_owner(self):
        state, generation = self.load()
        require(generation == self.generation and state["owner"] == self.owner, "source-quality claim changed")

    def notify(self):
        from scripts import slack_notify
        require(self.held, "cannot alert without a persisted hold")
        hold = self.state["hold"]
        if hold["notification"] != "pending":
            return
        hold["notification"] = "sending"
        self.save()  # Claim before external delivery. A crash cannot duplicate the post.
        old = hold["baseline"]["release"]["rows"] if hold["baseline"] else None
        count = hold["observation"].get("geometry_count")
        loss = f"{100 * (old - count) / old:.2f}%" if old and count is not None else "unavailable"
        candidate = hold["candidate"]
        body = (f"Automatic EAMLIS publishing is held. Review the published baseline before resuming.\n"
                f"Geometry records: {old} → {count}; drop: {loss}; reason: {hold['reason']}.\n"
                f"Hold: `{hold['id']}`\n"
                f"Inspect: `uv run python scripts/eamlis_source_loss_review.py status`\n")
        if candidate:
            candidate_id = candidate["path"].split("/")[-2].removeprefix("source-loss-")
            body += (f"Candidate: `{candidate_id}`\n"
                     "Staging catalog: run the *Inspect held EAMLIS candidate* GitHub workflow with that ID.\n"
                     "https://github.com/SkyTruth/shared-datasets-1/actions/workflows/eamlis-held-candidate-preview.yml\n")
        else:
            body += "No valid candidate bundle is available; inspect the recorded source failure.\n"
        try:
            slack_notify.notify(title="EAMLIS source-quality review required", body=body, status="decision", strict=True)
        except Exception:
            hold["notification"] = "unknown"  # A lost response does not establish nondelivery.
            self.save()
            raise
        hold["notification"] = "sent"
        self.save()

    def record(self):
        return {"asset_slug": SLUG, "status": "held", "reason": self.state["hold"]["reason"],
                "hold": self.state["hold"], "control_path": f"gs://{self.bucket.name}/{CONTROL}"}


def normalize_review(value):
    fields = {"schema_version", "hold_id", "candidate", "baseline_token"}
    require(isinstance(value, dict) and set(value) == fields and value["schema_version"] == REVIEW_VERSION,
            "invalid source-loss review")
    require(re.fullmatch(r"[0-9a-f]{64}", value["hold_id"]) is not None, "invalid hold ID")
    require(value["baseline_token"] is None or re.fullmatch(r"[0-9a-f]{64}", value["baseline_token"]), "invalid baseline token")
    ref = value["candidate"]
    require(isinstance(ref, dict) and set(ref) == {"path", "generation", "size", "sha256"}, "invalid candidate reference")
    require(re.fullmatch(rf"gs://{BUCKET}/_scratch/pending-publishes/{SLUG}/source-loss-[0-9a-f]{{64}}/candidate.json", ref["path"]),
            "review candidate is outside EAMLIS staging")
    require(re.fullmatch(r"[1-9][0-9]*", str(ref["generation"])) and type(ref["size"]) is int and ref["size"] > 0
            and re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]), "invalid candidate byte evidence")
    return value


def assert_review(guard, review):
    normalize_review(review)
    require(guard.held, "source-quality hold is no longer active")
    h = guard.state["hold"]
    require(review["hold_id"] == h["id"] and review["candidate"] == h["candidate"], "held candidate changed; obtain a new review")
    require(review["baseline_token"] == baseline_token(h["baseline"]) == baseline_token(guard.before),
            "published baseline changed; obtain a new review")


def candidate(client, ref):
    data = pinned(client, ref)
    value = json.loads(data)
    require(value["schema_version"] == 1 and value["asset_slug"] == SLUG, "invalid EAMLIS candidate")
    require(type(value["geometry_count"]) is int and value["geometry_count"] > 0, "empty candidates cannot be approved")
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["release"]), "invalid candidate date")
    prefix = ref["path"].removesuffix("candidate.json")
    require(prefix.endswith(f"source-loss-{value['candidate_id']}/"), "candidate identity differs from immutable path")
    require(all(f["path"].startswith(prefix + "objects/") for f in value["files"]), "candidate file escapes immutable bundle")
    roles = [f["role"] for f in value["files"]]
    require(len(roles) == len(set(roles)) and {"fgb", "pmtiles", "metadata", "schema", "manifest", "run_record"} <= set(roles),
            "candidate has missing or duplicated artifacts")
    for f in value["files"]:
        require(re.fullmatch(r"fgb|pmtiles|metadata|schema|manifest|run_record|extra-[0-9]+", f["role"]), "invalid candidate role")
        if f["role"] != "run_record":
            require(re.fullmatch(r"\.[a-z0-9.-]+", f["suffix"]), "invalid candidate suffix")
        expected_suffix = {"fgb": ".fgb", "pmtiles": ".pmtiles", "metadata": ".metadata.ndjson.gz",
                           "schema": ".schema.json", "manifest": ".manifest.json", "run_record": None}
        if f["role"] in expected_suffix:
            require(f["suffix"] == expected_suffix[f["role"]], "candidate artifact role has the wrong suffix")
    require(len({f["suffix"] for f in value["files"]}) == len(roles), "candidate has duplicate suffixes")
    return value


def inspection(client):
    guard = Guard(client.bucket(BUCKET))
    guard.state, guard.generation = guard.load()
    guard.before = baseline(guard.bucket)
    require(guard.held, "EAMLIS has no active source-quality hold")
    ref = guard.state["hold"]["candidate"]
    require(ref is not None, "hold has no valid candidate; inspect source errors and collect a valid download")
    review = {"schema_version": REVIEW_VERSION, "hold_id": guard.state["hold"]["id"],
              "candidate": ref, "baseline_token": baseline_token(guard.state["hold"]["baseline"])}
    assert_review(guard, review)
    return guard, candidate(client, ref), review


def expected_promotions(value, *, bucket=BUCKET):
    root = f"gs://{bucket}/{ROOT}"
    items = []
    for f in value["files"]:
        targets = ([f"{root}/runs/{value['release']}.json"] if f["role"] == "run_record" else
                   [f"{root}/{directory}/{SLUG}{f['suffix']}" for directory in (f"releases/{value['release']}", "latest")])
        for target in targets:
            items.append({"source_uri": f["path"], "source_generation": str(f["generation"]),
                          "destination_uri": target, "content_type": f["content_type"], "cache_control": NO_CACHE})
    return items


def validate_candidate_plan(client, plan):
    review = normalize_review(plan["source_loss_review"])
    require(plan["asset_slug"] == SLUG, "source-quality review is only supported for EAMLIS")
    value = candidate(client, review["candidate"])
    require(value["hold_id"] == review["hold_id"] and value["baseline_token"] == review["baseline_token"],
            "candidate belongs to another hold or baseline")
    expected = expected_promotions(value)
    require(len(plan["promotions"]) == len(expected), "review must publish exactly the complete held bundle")
    actual = {p["destination_uri"]: p for p in plan["promotions"]}
    require(len(actual) == len(expected), "duplicate candidate destination")
    for item in expected:
        require(item["destination_uri"] in actual and all(actual[item["destination_uri"]].get(k) == v for k, v in item.items()),
                "reviewed promotion differs from held candidate")
    require(not plan.get("release_index_asset_slugs"), "source-quality review cannot rebuild unrelated assets")
    for f in value["files"]:
        pinned(client, f)  # Verify the complete bundle before the first canonical copy.
    return value


def touches_asset(plan):
    touches = False
    for item in plan.get("promotions", []):
        uri = item["destination_uri"]
        require(not control_target(uri.split("/", 3)[-1]), "source-quality control requires its own reviewed resolver")
        if uri.startswith(f"gs://{BUCKET}/{ROOT}/") or uri == f"gs://{BUCKET}/{INDEX}":
            touches = True
    return touches


def protected_authority(plan):
    """A flag or Slack/chat message cannot manufacture production authority."""
    from scripts import dataset_mutation_authorization as auth
    auth.require_production_context(dict(os.environ))
    envelope = auth.verified_envelope(auth.GitHub(), Path("authorization"), os.environ["EXPECTED_SHA256"], dict(os.environ))
    require(envelope["outcome"] == "mutation" and envelope["document"].get("publish") == plan
            and "delete" not in envelope["document"], "source-quality approval must match the immutable reviewed PR")
    return auth.identity_digests(envelope)["proposal_key"]


def begin_promotion(client, plan):
    if not touches_asset(plan):
        require("source_loss_review" not in plan, "source-quality review has no EAMLIS destinations")
        return
    # The claim spans copies, finalization and index rebuild. A failed workflow
    # retains it; an unrelated execution cannot clear or steal its authority.
    owner = protected_authority(plan)
    guard = Guard(client.bucket(BUCKET), owner=owner)
    guard.state, guard.generation = guard.load()
    resuming = guard.state["owner"] == owner
    if resuming:
        current = baseline(guard.bucket)
        guard.before = guard.state["hold"]["baseline"] if guard.held else current
        if guard.held:
            value = candidate(client, plan["source_loss_review"]["candidate"])
            require(baseline_token(current) == baseline_token(guard.before) or
                    current and current["release"]["date"] == value["release"] and
                    current["release"]["rows"] == value["geometry_count"], "baseline changed outside interrupted promotion")
    else:
        guard.__enter__()
    try:
        if guard.held:
            require("source_loss_review" in plan, "EAMLIS publishing is held; review its exact candidate")
            assert_review(guard, plan["source_loss_review"])
            validate_candidate_plan(client, plan)
            from ingestion.eamlis_monthly import identity_state
            identity_state.validate_candidate(guard, candidate(client, plan["source_loss_review"]["candidate"]))
        else:
            require("source_loss_review" not in plan, "source-quality hold was already resolved")
            from ingestion.eamlis_monthly import identity_state
            identity_state.require_metadata_only(guard, plan)
    except Exception:
        if not resuming:
            guard.__exit__(None, None, None)
        raise


def already_promoted(client, promotion, plan, *, require_finalized=False):
    """Resume only byte-proven results of this exact approved PR transaction."""
    from google.api_core.exceptions import NotFound
    from scripts import finalize_promoted_release_metadata as finalizer
    bucket, name = promotion["destination_uri"][5:].split("/", 1)
    destination = client.bucket(bucket).blob(name)
    try:
        destination.reload()
    except NotFound:
        return False
    if destination.content_type != promotion["content_type"] or destination.cache_control != promotion["cache_control"]:
        return False
    sb, sn = promotion["source_uri"][5:].split("/", 1)
    source = client.bucket(sb).blob(sn, generation=int(promotion["source_generation"]))
    raw = source.download_as_bytes(if_generation_match=int(promotion["source_generation"]))
    current = destination.download_as_bytes(if_generation_match=int(destination.generation))
    metadata = name.endswith(".manifest.json") or "/runs/" in name and name.endswith(".json")
    if current == raw and (not require_finalized or not metadata):
        return True
    # Finalization changes only generation evidence in JSON. Recompute its
    # complete expected output; never ignore arbitrary JSON fields/hashes.
    try:
        if name.endswith(".manifest.json"):
            expected = finalizer.finalized_manifest_payload(json.loads(raw),
                stat=lambda uri: finalizer.stat_blob(client, uri),
                maybe_stat=lambda uri: finalizer.maybe_stat_blob(client, uri))
        elif "/runs/" in name and name.endswith(".json"):
            manifests = {p["destination_uri"]: finalizer.stat_blob(client, p["destination_uri"], include_sha256=True)
                         for p in plan["promotions"] if p["destination_uri"].endswith(".manifest.json")}
            expected = finalizer.finalized_run_record_payload(json.loads(raw),
                stat=lambda uri: finalizer.stat_blob(client, uri), manifest_infos=manifests)
        else:
            return False
    except finalizer.FinalizeReleaseMetadataError:
        return False  # A not-yet-copied object is not a committed result.
    return current == encode(expected)


def finish_promotion(client, plan):
    if not touches_asset(plan):
        return
    owner = protected_authority(plan)
    guard = Guard(client.bucket(BUCKET), owner=owner)
    guard.state, guard.generation = guard.load()
    require(guard.state["owner"] == owner, "source-quality publication claim changed")
    if "source_loss_review" in plan:
        review = normalize_review(plan["source_loss_review"])
        hold = guard.state["hold"]
        require(hold is not None and hold["id"] == review["hold_id"] and hold["candidate"] == review["candidate"],
                "held candidate changed during promotion")
        value = candidate(client, review["candidate"])
        current = baseline(guard.bucket)
        require(current and current["release"]["date"] == value["release"]
                and current["release"]["rows"] == value["geometry_count"], "approved release index is not current")
        # Verify complete expected finalized JSON as well as unchanged data
        # bytes and metadata before a hold can disappear.
        for item in plan["promotions"]:
            require(already_promoted(client, item, plan, require_finalized=True),
                    "published bytes or metadata differ from approved candidate")
        expected_paths = {f"gs://{BUCKET}/{ROOT}/releases/{value['release']}/{SLUG}{f['suffix']}"
                          for f in value["files"] if f["role"] != "run_record"}
        indexed = current["release"]["files"]
        require({f["path"] for f in indexed} == expected_paths, "published release index has an incomplete bundle")
        for ref in indexed:
            name = ref["path"][len(f"gs://{BUCKET}/"):]
            blob = guard.bucket.blob(name)
            blob.reload()
            require(str(blob.generation) == str(ref["generation"]) and int(blob.size) == ref["size"],
                    "published release index has stale generations")
        manifest_data, _ = read(guard.bucket, f"{ROOT}/latest/{SLUG}.manifest.json")
        from scripts import release_feature_model as model
        manifest = json.loads(manifest_data)
        model.validate_release_manifest(manifest, expected_asset_slug=SLUG, expected_release=value["release"], require_generations=True)
        require(manifest["validation"]["feature_count"] == value["geometry_count"], "published manifest count differs from approval")
        if guard.state.get("identity") is not None:
            from ingestion.eamlis_monthly import identity_state
            identity_state.activate(guard)
        guard.state["hold"] = None
    else:
        require(not guard.held, "cannot clear an unreviewed hold")
    guard.state["owner"] = None
    guard.save()


def stage_candidate(guard, *, output, source, release, source_inputs, translations, extra_files, extract):
    """Build once and retain all exact promotion candidates outside canonical data."""
    from ingestion.common import feature_metadata
    files = {role: getattr(output, role) for role in ("fgb", "pmtiles", "metadata", "schema")}
    files.update({f"extra-{n}": path for n, (_suffix, path) in enumerate(extra_files)})
    hashes = {role: digest(path.read_bytes()) for role, path in files.items()}
    seed = {"builder_version": 1, "release": release, "baseline_token": baseline_token(guard.before),
            "source_fingerprint": source.fingerprint, "hashes": hashes}
    if output.identity_metadata.get("contract_id"):
        seed["identity"] = output.identity_metadata
    candidate_id = digest(encode(seed))
    prefix = f"_scratch/pending-publishes/{SLUG}/source-loss-{candidate_id}/"
    staged = {role: immutable_file(guard.bucket, prefix + "objects/" + path.name, path)
              for role, path in files.items()}
    core_roles = ("fgb", "pmtiles", "metadata", "schema")
    suffixes = {role: "." + role for role in core_roles}
    suffixes["metadata"] = ".metadata.ndjson.gz"
    suffixes["schema"] = ".schema.json"
    suffixes.update({f"extra-{n}": suffix for n, (suffix, _path) in enumerate(extra_files)})
    root = f"gs://{guard.bucket.name}/{ROOT}"
    release_info = {role: {**staged[role], "generation": int(staged[role]["generation"]),
                           "path": f"{root}/releases/{release}/{SLUG}{suffixes[role]}"}
                    for role in files}
    latest_info = {role: {**ref, "path": f"{root}/latest/{SLUG}{suffixes[role]}"} for role, ref in release_info.items()}
    manifest = feature_metadata.final_manifest_payload(
        asset_slug=SLUG, release=release, bucket_name=guard.bucket.name, asset_root=ROOT,
        sha256_by_role={role: hashes[role] for role in core_roles}, schema=output.schema_payload,
        source_inputs=source_inputs,
        identity=output.identity_metadata,
        geometry_hash_algorithm=output.geometry_hash_algorithm,
        feature_count=output.row_count,
        release_blob_info_by_role={r: release_info[r] for r in core_roles},
        latest_blob_info_by_role={r: latest_info[r] for r in core_roles},
        manifest_release_path=f"{root}/releases/{release}/{SLUG}.manifest.json",
        manifest_latest_path=f"{root}/latest/{SLUG}.manifest.json", translations=translations,
        extra_artifacts=[{"role": r, "format": "metadata", **release_info[r],
                          "latest_path": latest_info[r]["path"], "latest_generation": latest_info[r]["generation"],
                          "sha256": hashes[r]} for r in files if r.startswith("extra-")])
    feature_metadata.write_manifest(manifest, output.manifest)
    files["manifest"] = output.manifest
    suffixes["manifest"] = ".manifest.json"
    staged["manifest"] = immutable_file(guard.bucket, prefix + "objects/" + output.manifest.name, output.manifest)
    record = {"schema_version": 1, "record_version": 1, "asset_slug": SLUG, "run_date": release,
        "release_date": release, "status": "success", "source": source.layer_url,
        "source_version": source.fingerprint_hash, "source_fingerprint": source.fingerprint,
        "source_fingerprint_hash": source.fingerprint_hash,
        "source_stats": {"feature_count": output.row_count, "max_date_revised": source.stats.max_date_revised,
                         "max_objectid": source.stats.max_objectid},
        "release_path": f"{root}/releases/{release}/", "row_count": output.row_count,
        "release_paths": [{**ref, "path": f"{root}/releases/{release}/{SLUG}{suffixes[role]}"} for role, ref in staged.items()],
        "latest_paths": [{**ref, "path": f"{root}/latest/{SLUG}{suffixes[role]}"} for role, ref in staged.items()],
        "sha256": {r: ref["sha256"] for r, ref in staged.items()},
        "notes": "Prepared held candidate; publication requires its exact reviewed PR plan."}
    record_path = output.manifest.parent / "run-record.json"
    if output.identity_metadata.get("contract_id"):
        record["identity_contract"] = output.identity_metadata["contract_id"]
    record_path.write_bytes(encode(record))
    staged["run_record"] = immutable_file(guard.bucket, prefix + "objects/run-record.json", record_path)
    raw = immutable_file(guard.bucket, prefix + "source/source.geojson", extract.geojson)
    value = {"schema_version": 1, "asset_slug": SLUG, "release": release,
        "candidate_id": candidate_id, "hold_id": guard.state["hold"]["id"],
        "baseline_token": baseline_token(guard.before), "geometry_count": output.row_count,
        "source_input": raw, "source_fingerprint": source.fingerprint,
        "files": [{**ref, "role": role, "suffix": suffixes.get(role)} for role, ref in staged.items()]}
    path = output.manifest.parent / "candidate.json"
    path.write_bytes(encode(value))
    ref = immutable_file(guard.bucket, prefix + "candidate.json", path)
    ref.pop("content_type")
    guard.attach(ref)
    return ref
