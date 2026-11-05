"""Require the native CI fixtures to pass, while keeping local skips optional."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from xml.etree import ElementTree


REQUIRED_TESTS = (
    (
        "tests.test_raster_standards.RasterCogIntegrationTests",
        "test_tiny_cog_validates_with_metadata",
    ),
    (
        "tests.test_wdpa_monthly.WdpaMonthlyIntegrationTests",
        "test_fixture_zip_builds_mixed_geometry_outputs",
    ),
    (
        "tests.test_sea_ice_daily.SeaIceDailyIntegrationTests",
        "test_synthetic_raster_builds_fgb_and_pmtiles",
    ),
    (
        "tests.test_eamlis_monthly.EamlisMonthlyIntegrationTests",
        "test_fixture_geojson_builds_stable_fgb_output",
    ),
    (
        "tests.test_eamlis_monthly.EamlisMonthlyIntegrationTests",
        "test_keyed_builder_preserves_colocated_problems_and_area_only_record",
    ),
    (
        "tests.test_wdpa_disk_processing",
        "test_normalized_store_matches_old_export_semantically",
    ),
    (
        "tests.test_wdpa_disk_processing",
        "test_complete_discovery_cli_normalizes_both_realms_and_reuses_spools",
    ),
    (
        "tests.test_wdpa_disk_processing",
        "test_metadata_checkpoint_reuses_stage_and_rejects_changed_spool",
    ),
    (
        "tests.test_mpatlas_monthly",
        "test_mixed_geometry_bundle_has_source_ids_valid_tiles_and_stored_hashes",
    ),
)


def check_junit(report: Path, *, native: bool) -> None:
    cases = list(ElementTree.parse(report).iter("testcase"))
    if not cases:
        raise ValueError("test collection is empty")
    for case in cases:
        identity = (case.get("classname"), case.get("name"))
        if not all(identity):
            raise ValueError("test result is missing its case identity")
        if case.find("failure") is not None or case.find("error") is not None:
            raise ValueError(f"failed test: {'.'.join(identity)}")
        if case.find("skipped") is not None and (native or identity not in REQUIRED_TESTS):
            raise ValueError(f"unexpected skipped test: {'.'.join(identity)}")
    if native:
        required_results(cases)


def check_results(report: Path) -> None:
    required_results(list(ElementTree.parse(report).iter("testcase")))


def required_results(cases) -> None:
    errors = []
    for classname, name in REQUIRED_TESTS:
        matches = [
            case
            for case in cases
            if case.get("classname") == classname and case.get("name") == name
        ]
        identity = f"{classname}.{name}"
        if len(matches) != 1:
            errors.append(
                f"{identity}: expected exactly one result, found {len(matches)}"
            )
            continue
        for outcome in ("skipped", "failure", "error"):
            if matches[0].find(outcome) is not None:
                errors.append(
                    f"{identity}: {outcome}; a passing native execution is required"
                )
    if errors:
        raise ValueError("\n".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report", type=Path, help="JUnit XML from the native pytest run"
    )
    args = parser.parse_args()
    try:
        check_results(args.report)
    except (OSError, ElementTree.ParseError, ValueError) as exc:
        print(f"Native geospatial test requirement failed:\n{exc}", file=sys.stderr)
        return 1
    print(f"All {len(REQUIRED_TESTS)} required native geospatial tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
