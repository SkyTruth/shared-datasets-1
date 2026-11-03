"""File adapters for complete vector, table and non-feature publication discovery."""
from __future__ import annotations

from contextlib import closing
import csv
import hashlib
from pathlib import Path
import tempfile

from ingestion.common import feature_metadata as fm, publication_readiness as readiness
from ingestion.common.identity_index import DiskIdentityPlan, DiskIdentityRecords
from ingestion.common.runtime import sha256_file
from scripts import release_feature_model as model


def input_digest(path):
    if not path.is_dir():
        return sha256_file(path)
    digest = hashlib.sha256()
    for member in sorted(path.rglob("*")):
        if member.is_symlink():
            raise ValueError("source tree must not contain symlinks")
        if member.is_file():
            digest.update(member.relative_to(path).as_posix().encode() + b"\0" + sha256_file(member).encode() + b"\n")
    return digest.hexdigest()


def source_features(path, format_name):
    if format_name == "geojsonseq":
        yield from fm.iter_geojsonseq(path)
    elif format_name == "geojson":
        yield from fm.iter_geojson_features(path)
    elif format_name == "csv":
        with path.open(encoding="utf-8", newline="") as stream:
            for properties in csv.DictReader(stream):
                yield {"type": "Feature", "geometry": None, "properties": properties}
    else:
        raise ValueError("identity discovery requires normalized geojsonseq, geojson or csv")


def _load_targets(config, stack, review_inputs):
    """Read the explicit full batch; every row count and source hash is checked."""
    targets = []
    for item in config["assets"]:
        missing = {"source", "source_sha256", "format", "identity_strategy"} - item.keys()
        if missing:
            raise ValueError("missing discovery fields: " + ", ".join(sorted(missing)))
        pins = dict(review_inputs)
        source = Path(item["source"]).resolve()
        source_sha = input_digest(source)
        if source_sha != item["source_sha256"]:
            raise ValueError("source checksum differs: " + str(source))
        strategy = item["identity_strategy"]
        if strategy not in {"generated", "source_field", "not_applicable"}:
            raise ValueError("unsupported identity_strategy")
        baseline = None
        baseline_hash = None
        if strategy == "generated":
            raw = item.get("baseline", {})
            if not isinstance(raw, dict) or not raw:
                raise ValueError("generated discovery requires an explicit baseline")
            if raw.get("genesis") is True:
                if set(raw) != {"genesis", "contract_id"}:
                    raise ValueError("explicit genesis must not invent previous records or sequence state")
                baseline = model.GeneratedIdentityBaseline.genesis(contract_id=raw["contract_id"])
            else:
                missing = {"records", "sha256", "release", "next_feature_id", "contract_id"} - raw.keys()
                if missing:
                    raise ValueError("missing baseline fields: " + ", ".join(sorted(missing)))
                records_path = Path(raw["records"]).resolve()
                baseline_hash = sha256_file(records_path)
                pins[str(records_path)] = baseline_hash
                if baseline_hash != raw["sha256"]:
                    raise ValueError("baseline checksum differs")
                # A disk index prevents a large sidecar from becoming a Python tuple.
                index = DiskIdentityRecords(Path(stack.enter_context(tempfile.TemporaryDirectory(
                    prefix="baseline-", dir=config["work_dir"]))) / "records.sqlite")
                stack.callback(index.close)
                validation = model.validate_sidecar_records(model.read_metadata_sidecar(records_path),
                    expected_asset_slug=item["asset_slug"], expected_release=raw["release"], identity_index=index)
                if not validation.valid:
                    raise ValueError("invalid baseline: " + "; ".join(validation.errors))
                baseline = model.GeneratedIdentityBaseline(index.seal(), raw["next_feature_id"], raw["release"],
                    contract_id=raw["contract_id"],
                    snapshot=model.GeneratedIdentitySnapshot(**raw["snapshot"]) if raw.get("snapshot") else None)
        fields = tuple(item.get("source_fields", ()))
        if strategy == "source_field" and len(fields) != 1:
            raise ValueError("source_field discovery requires exactly one field")
        if strategy == "not_applicable" and item["format"] not in {"cog", "zarr"}:
            raise ValueError("only explicit cog/zarr artifacts may omit feature identity discovery")
        if strategy == "not_applicable" and not item.get("identity_not_applicable_reason"):
            raise ValueError("non-feature artifacts require an identity_not_applicable_reason")
        if strategy != "not_applicable" and (type(item.get("expected_rows")) is not int or item["expected_rows"] <= 0):
            raise ValueError("complete identity discovery requires independent positive expected_rows")

        def prepare(directory, item=item, source=source, strategy=strategy, fields=fields):
            if strategy == "not_applicable":
                return {"identity_not_applicable_reason": item["identity_not_applicable_reason"], "rows": None}
            count = 0
            with closing(DiskIdentityPlan(directory / "identity.sqlite")) as plan:
                seen = set()
                for count, feature in enumerate(source_features(source, item["format"]), 1):
                    if strategy == "generated":
                        plan.add(count, feature, source_fields=fields,
                            excluded_properties=item.get("excluded_properties", ()))
                    else:
                        value = model.source_field_feature_id(source_field=fields[0],
                            source_value=feature["properties"].get(fields[0]))
                        if value in seen:
                            raise ValueError("duplicate source feature ID: " + value)
                        seen.add(value)
                plan.db.commit()
            if count != item["expected_rows"]:
                raise ValueError(f"incomplete source: read {count} of {item['expected_rows']} expected rows")
            if input_digest(source) != item["source_sha256"]:
                raise ValueError("source changed during discovery")
            return {"rows": count, "identity_strategy": strategy}

        decisions = item.get("decisions", [])
        if isinstance(decisions, str):
            path = Path(decisions).resolve()
            pins[str(path)] = sha256_file(path)
            decisions = model.read_identity_resolution_decisions(path,
                asset_slug=item["asset_slug"], release=config["release"])
        targets.append(readiness.Target(item["asset_slug"], config["release"],
            {"source": str(source), "source_sha256": source_sha, "baseline_sha256": baseline_hash,
             **({"original_source_sha256": item["original_source_sha256"]} if "original_source_sha256" in item else {}),
             "definition": {k: v for k, v in item.items() if k != "decisions"}},
            prepare, baseline=baseline, decisions=tuple(decisions),
            match_properties=item.get("match_properties", True), blockers=tuple(item.get("blockers", ())),
            review_inputs=pins))
    return targets


def load_targets(config, stack, review_inputs):
    if not isinstance(config.get("assets"), list) or not config["assets"] or not config.get("release"):
        raise ValueError("discovery requires a release and a nonempty asset list")
    targets = []
    for item in config["assets"]:
        if not isinstance(item, dict) or not isinstance(item.get("asset_slug"), str):
            raise ValueError("every discovery asset requires an asset_slug")
        try:
            targets.extend(_load_targets({**config, "assets": [item]}, stack, review_inputs))
        except (ValueError, RuntimeError, OSError) as exc:
            message = str(exc)
            def failed_prepare(_directory, message=message):
                raise ValueError(message)
            targets.append(readiness.Target(item["asset_slug"], config["release"],
                {"invalid_input": message}, failed_prepare))
    return targets


def run(config, *, review_inputs=None):
    from contextlib import ExitStack

    directory = Path(config["work_dir"]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    readiness.write_json(directory / "readiness.json", {"schema": readiness.SCHEMA, "state": "INCOMPLETE"})
    with ExitStack() as stack:
        report = readiness.discover_batch(load_targets(config, stack, review_inputs or {}), directory)
    return report
