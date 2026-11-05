#!/usr/bin/env python3
"""Build synthetic release bundles using each installed ingestion toolchain."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from importlib import import_module
from pathlib import Path
import sys
import subprocess

sys.path.insert(0, str(Path.cwd()))
from scripts import release_feature_model as model


def sea_ice_build(workdir):
    """The installed-image and local smoke use the same complete synthetic build."""
    from ingestion.sea_ice_daily import run as sea_ice

    source = workdir / "source.tif"
    sea_ice.run_command([
        "gdal_create", "-of", "GTiff", "-outsize", "4", "4", "-bands", "1",
        "-burn", "3", "-ot", "Byte", "-a_srs", "EPSG:3857",
        "-a_ullr", "-100000", "100000", "100000", "-100000", str(source),
    ])
    outputs = sea_ice.build_outputs(
        baseline=model.GeneratedIdentityBaseline.genesis(contract_id="test-v1"),
        source_tif=source, source_date=dt.date(2026, 4, 28), workdir=workdir,
    )
    if outputs.row_count <= 0 or not all(path.is_file() for path in (outputs.fgb, outputs.pmtiles, outputs.metadata, outputs.schema)):
        raise RuntimeError("Small fixture did not produce its nonempty release bundle")
    validation = model.validate_sidecar_records(
        model.read_metadata_sidecar(outputs.metadata),
        expected_asset_slug=sea_ice.ASSET.slug, expected_release="2026-04-29",
    )
    if not validation.valid or validation.feature_count != outputs.row_count:
        raise RuntimeError("Small fixture metadata/count validation failed")
    if sea_ice.feature_count(outputs.fgb) != outputs.row_count:
        raise RuntimeError("Small fixture FlatGeobuf count differs")
    return outputs


def wdpa_build(work):
    for module in ("ingestion.wdpa_monthly.automatic", "scripts.feature_metadata_localization", "scripts.translation_local_io"):
        import_module(module)
    from ingestion.wdpa_monthly import translations
    from scripts.feature_metadata_translation_reuse import COLUMNS, build_memory
    source = work / "source.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[x, 1], [x+0.1, 1], [x+0.1, 1.1], [x, 1.1], [x, 1]]]},
         "properties": {**dict.fromkeys(translations.FIELDS, f"fixture-{x}"), "SITE_PID": str(x), "SITE_ID": x, "MARINE": realm, "ISO3": "IND"}}
        for x, realm in ((1, "1"), (2, "0"))
    ]}))
    metadata, translations_csv, memory = work / "old.metadata.ndjson.gz", work / "translations.csv", work / "translations.sqlite"
    model.write_metadata_sidecar([], metadata)
    translations_csv.write_text(",".join(COLUMNS) + "\n")
    build_memory(database=memory, sources=[dict(asset_slug="wdpa-marine", release="2026-03-01",
        canonical_sidecar=str(metadata), translation_source=str(translations_csv))],
        fields=translations.FIELDS, locales=translations.LOCALES, source_key_fields=["SITE_PID"])
    output = work / "build"
    subprocess.run([sys.executable, "scripts/local_wdpa_sample.py", "--source", str(source),
        "--genesis", "--translation-memory", str(memory), "--run-date", "2026-04-01",
        "--workdir", str(output)], check=True, timeout=180)
    report = json.loads((output / "benchmark.json").read_text())
    assert report["state"] == "succeeded" and report["contracts_verified"]
    assert {slug: row["rows"] for slug, row in report["assets"].items()} == {"wdpa-marine": 1, "wdpa-terrestrial": 1}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--sea-ice-only", action="store_true")
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=False)
    if args.sea_ice_only:
        output = sea_ice_build(args.workdir)
        report = {"rows": output.row_count, "artifact_sha256": output.sha256}
    else:
        report = wdpa_build(args.workdir)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
