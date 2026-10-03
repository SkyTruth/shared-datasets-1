"""Immutable WDPA build outputs, staged once and published without rebuilding.

The staging identity can only create objects under PREFIX. The production
identity downloads exact generations and passes verified files to the existing
owned publisher; it never invokes source processing or translation generation.
"""

from __future__ import annotations

from dataclasses import asdict
import os
import re

from google.cloud import storage

from ingestion.common import publication as p
from ingestion.wdpa_monthly.translations import LOCALES
from scripts import release_feature_model as model

BUCKET = "skytruth-shared-datasets-1"
PREFIX = "_scratch/wdpa-builds/"
EXECUTION = r"wdpa-processing-validation-[a-z0-9]+"
ROLES = ("fgb", "pmtiles", "metadata", "schema", "metadata_translations")


def artifact_paths(outputs):
    return {
        **{role: getattr(outputs, role) for role in ROLES},
        **{
            f"metadata_{locale}": outputs.localized_metadata[locale]
            for locale in LOCALES
        },
    }


def check_reference(ref, *, execution=None):
    p.require(
        isinstance(ref, dict) and set(ref) == {"uri", "generation", "size", "sha256"},
        "invalid staged object reference",
    )
    bucket, name = p.split_uri(ref["uri"])
    prefix = PREFIX + (execution + "/" if execution else "")
    p.require(
        bucket == BUCKET
        and name.startswith(prefix)
        and ".." not in name.split("/")
        and "//" not in name,
        "staged object is outside the build prefix",
    )
    if execution is None:
        execution = name.removeprefix(PREFIX).split("/", 1)[0]
    p.require(re.fullmatch(EXECUTION, execution) is not None, "invalid build execution")
    p.require(
        re.fullmatch(
            PREFIX
            + EXECUTION
            + r"/(?:build-bundle\.json|wdpa-(?:marine|terrestrial)/[a-z0-9_.-]+)",
            name,
        )
        is not None,
        "invalid build object path",
    )
    p.require(
        type(ref["generation"]) is int and ref["generation"] > 0,
        "staged generation must be pinned",
    )
    p.require(
        type(ref["size"]) is int and ref["size"] > 0, "staged object must be nonempty"
    )
    p.require(
        re.fullmatch(r"[0-9a-f]{64}", str(ref["sha256"])) is not None,
        "staged SHA256 is required",
    )
    return name


class BuildStager:
    def __init__(self, client, execution):
        p.require(
            re.fullmatch(EXECUTION, execution or "") is not None,
            "a real build execution is required",
        )
        self.bucket = client.bucket(BUCKET)
        self.execution = execution
        self.prefix = PREFIX + execution + "/"

    @classmethod
    def from_runtime(cls):
        return cls(
            storage.Client(project="shared-datasets-1"),
            os.environ["CLOUD_RUN_EXECUTION"],
        )

    def upload(self, name, path):
        from ingestion.wdpa_monthly.run import sha256_file

        sha = sha256_file(path)
        blob = self.bucket.blob(self.prefix + name)
        blob.cache_control = "no-store"
        blob.metadata = {"sha256": sha, "build_execution": self.execution}
        blob.upload_from_filename(str(path), if_generation_match=0, checksum="crc32c")
        ref = {
            "uri": f"gs://{BUCKET}/{blob.name}",
            "generation": int(blob.generation),
            "size": path.stat().st_size,
            "sha256": sha,
        }
        check_reference(ref, execution=self.execution)
        return ref

    def stage_asset(self, asset, outputs, fields):
        facts = asdict(outputs)
        for role in (*ROLES, "manifest", "localized_metadata"):
            facts.pop(role)
        refs = {}
        for role, path in artifact_paths(outputs).items():
            ref = self.upload(f"{asset.slug}/{path.name}", path)
            if role in outputs.sha256:
                p.require(
                    ref["sha256"] == outputs.sha256[role],
                    "artifact changed after validation",
                )
            refs[role] = ref
        return {
            "outputs": facts,
            "artifacts": refs,
            "source_fields": [asdict(field) for field in fields],
        }

    def commit(self, report, workdir):
        from scripts.wdpa_processing_gate import check_build, source_digest

        p.require(
            not check_build(report, require_bundle=False),
            "complete build did not pass acceptance",
        )
        p.require(
            set(report["staged_assets"]) == {"wdpa-marine", "wdpa-terrestrial"},
            "both retained realm bundles are required",
        )
        manifest = {
            "schema_version": 1,
            "report": report,
            "assets": report["staged_assets"],
        }
        check_bundle(manifest, producer_source=source_digest())
        path = workdir / "build-bundle.json"
        path.write_bytes(p.canonical(manifest))
        return self.upload("build-bundle.json", path)


def download(client, ref, target, *, execution=None):
    from ingestion.wdpa_monthly.run import sha256_file

    name = check_reference(ref, execution=execution)
    p.require(not target.exists(), "refusing to replace a local bundle file")
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = client.bucket(BUCKET).blob(name, generation=ref["generation"])
    blob.download_to_filename(
        str(target), if_generation_match=ref["generation"], checksum="crc32c"
    )
    p.require(
        target.stat().st_size == ref["size"] and sha256_file(target) == ref["sha256"],
        "staged artifact bytes do not match the approved reference",
    )


def load_bundle(client, ref, workdir, *, producer_source):
    p.require(ref["size"] <= 1024 * 1024, "build descriptor is too large")
    path = workdir / "build-bundle.json"
    download(client, ref, path)
    bundle = p.strict_json(path.read_bytes())
    check_bundle(bundle, producer_source=producer_source)
    check_reference(ref, execution=bundle["report"]["cloud_execution"])
    p.require(
        ref["uri"].endswith("/build-bundle.json"),
        "expected the committed build descriptor",
    )
    return bundle


def check_bundle(bundle, *, producer_source):
    from scripts.wdpa_processing_gate import check_build

    p.require(
        set(bundle) == {"schema_version", "report", "assets"}
        and bundle["schema_version"] == 1,
        "unsupported build descriptor",
    )
    report = bundle["report"]
    p.require(
        re.fullmatch(r"[0-9a-f]{64}", producer_source) is not None
        and report["source_tree_sha256"] == producer_source,
        "bundle uses a different processing source",
    )
    p.require(
        not check_build(report, require_bundle=False),
        "bundle is not a complete passing build",
    )
    execution = report["cloud_execution"]
    p.require(
        bundle["assets"] == report["staged_assets"]
        and set(bundle["assets"]) == {"wdpa-marine", "wdpa-terrestrial"},
        "build descriptor is incomplete",
    )
    expected_roles = {*ROLES, *(f"metadata_{locale}" for locale in LOCALES)}
    for slug, asset in bundle["assets"].items():
        p.require(
            set(asset) == {"outputs", "artifacts", "source_fields"}
            and set(asset["artifacts"]) == expected_roles,
            "incomplete retained artifact roles",
        )
        outputs = asset["outputs"]
        summary = report["assets"][slug]
        p.require(
            set(outputs["sha256"]) == expected_roles | {"csv"}
            and outputs["sha256"]["csv"] == outputs["sha256"]["metadata_translations"],
            "validated artifact hashes are incomplete",
        )
        p.require(
            outputs["row_count"] == summary["rows"]
            and outputs["next_generated_feature_id"]
            == summary["next_generated_feature_id"]
            and outputs["sha256"] == summary["artifact_sha256"],
            "retained bundle differs from its measured report",
        )
        for role, item in asset["artifacts"].items():
            name = check_reference(item, execution=execution)
            p.require(
                name.startswith(PREFIX + execution + "/" + slug + "/"),
                "artifact belongs to another realm",
            )
            p.require(
                item["sha256"] == outputs["sha256"][role],
                "retained artifact hash differs from validation",
            )


def promote(client, publisher, ref, workdir):
    """Consume the approved build; owned publication retains recovery semantics."""
    from ingestion.wdpa_monthly import run as wdpa

    bundle = load_bundle(
        client,
        ref,
        workdir,
        producer_source=os.environ["WDPA_ACCEPTED_BUILD_SOURCE_SHA256"],
    )
    report = bundle["report"]
    run_date = wdpa.parse_run_date(report["run_date"])
    # Check every predecessor before the first new canonical write. Existing
    # successful realms remain committed; an interrupted owner resumes its receipt.
    resumed, committed = {}, {}
    for asset in wdpa.ASSETS:
        resumed[asset.slug] = publisher.resume(asset)
        committed[asset.slug] = publisher.load_successful_run_record(asset, run_date)
        if resumed[asset.slug] is None:
            state = publisher.state(asset).value
            p.require(state["active"] is None, "another publication owns the asset")
            facts = bundle["assets"][asset.slug]["outputs"]
            p.require(
                facts["identity_baseline_snapshot"]
                == state["current"]["latest_manifest"],
                "staged build has a stale identity baseline",
            )
            p.require(
                facts["previous_generated_feature_id"]
                == state["reserved_next_feature_id"],
                "staged build has a stale allocation counter",
            )
            p.require(
                facts["previous_release"] == state["current"]["release"],
                "staged build has a stale predecessor release",
            )
            if committed[asset.slug]:
                record, _info = committed[asset.slug]
                p.require(
                    record["release_date"] == run_date.isoformat()
                    and record["source_version"] == wdpa.source_version_for(run_date)
                    and record["source"]
                    == wdpa.build_source_url(wdpa.DEFAULT_SOURCE_URL_TEMPLATE, run_date)
                    and record["identity_contract"]
                    == facts["identity_contract"]
                    == wdpa.CONTRACT_ID
                    and record["row_count"] == facts["row_count"]
                    and facts["next_generated_feature_id"]
                    == state["reserved_next_feature_id"],
                    "committed release differs from the frozen identity/source contract",
                )
            else:
                publisher.assert_no_partial_release(asset, run_date)
    # Verify every needed file before the first new publication. Hashing large
    # files is measured and uses the same scratch cache-pressure control.
    from ingestion.wdpa_monthly.resources import PhaseProfiler

    profiler = PhaseProfiler(
        workdir, versions=report["native_versions"], scratch_root=workdir.parent
    )
    all_paths = {}
    with profiler.phase("promotion:download"):
        for asset in wdpa.ASSETS:
            if resumed[asset.slug] is not None or committed[asset.slug]:
                continue
            paths = {}
            for role, item in bundle["assets"][asset.slug]["artifacts"].items():
                path = workdir / asset.slug / item["uri"].rsplit("/", 1)[1]
                download(client, item, path, execution=report["cloud_execution"])
                paths[role] = path
            all_paths[asset.slug] = paths
    records = []
    for asset in wdpa.ASSETS:
        if resumed[asset.slug] is not None:
            records.append(resumed[asset.slug])
            continue
        if committed[asset.slug]:
            records.append(
                {
                    "asset_slug": asset.slug,
                    "status": "skipped",
                    "reason": "monthly source already published",
                    "release_index": publisher.record_existing_successful_release(
                        asset, run_date
                    ),
                }
            )
            continue
        staged = bundle["assets"][asset.slug]
        directory = workdir / asset.slug
        paths = all_paths[asset.slug]
        facts = dict(staged["outputs"])
        facts["identity_baseline_snapshot"] = model.GeneratedIdentitySnapshot(
            **facts["identity_baseline_snapshot"]
        )
        outputs = wdpa.AssetOutputs(
            **facts,
            **{role: paths[role] for role in ROLES},
            manifest=directory / "unused-build-manifest.json",
            localized_metadata={
                locale: paths[f"metadata_{locale}"] for locale in LOCALES
            },
        )
        with profiler.phase(f"{asset.slug}:promote"):
            records.append(
                wdpa.publish_asset(
                    publisher=publisher,
                    asset=asset,
                    outputs=outputs,
                    run_date=run_date,
                    source_url=wdpa.build_source_url(
                        wdpa.DEFAULT_SOURCE_URL_TEMPLATE, run_date
                    ),
                    source_version=wdpa.source_version_for(run_date),
                    source_fields=tuple(
                        wdpa.FieldSpec(**field) for field in staged["source_fields"]
                    ),
                )
            )
        for path in paths.values():
            path.unlink()
    return records
