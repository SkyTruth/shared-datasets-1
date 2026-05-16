#!/usr/bin/env python3
"""Build and validate local vector publish artifacts.

This helper prepares upload-ready files only. Use scripts/gcs_asset.py for the
actual Cloud Storage mutation so generation preconditions stay centralized.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pmtiles_zoom import (  # noqa: E402
    DEFAULT_MAXZOOM_CAP,
    PMTILES_LOW_MAXZOOM_THRESHOLD,
    FgbProfile,
    ZoomRecommendation,
    profile_fgb,
    profile_payload,
    recommend_maxzoom,
    validate_detail_hint,
)

WORK_ROOT_ENV = "SHARED_DATASETS_WORKDIR"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DEFAULT_TIPPECANOE_ARGS = ("--no-feature-limit", "--no-tile-size-limit", "--drop-rate=1")
SYNTHETIC_PMTILES_PROPERTY = "source_layer"
GENERATED_GROUP_ID_COLUMN = "shared_datasets_group_id"
PROPERTY_STRIPPING_TIPPECANOE_ARGS = {"--exclude-all"}
POINT_DROPPING_TIPPECANOE_ARGS = {
    "--cluster-distance",
    "--coalesce-densest-as-needed",
    "--drop-densest-as-needed",
    "--drop-fraction-as-needed",
    "--drop-lines",
    "--drop-polygons",
    "--drop-smallest-as-needed",
}
STANDARD_PMTILES_MAXZOOM = 8
AUTO_MAXZOOM = "auto"


@dataclass(frozen=True)
class VectorBuildPlan:
    source: str
    asset_slug: str
    layer_name: str
    work_dir: str
    output_dir: str
    group_id_source_path: str | None
    group_id_input_path: str | None
    fgb_path: str
    tippecanoe_input_path: str
    mbtiles_path: str
    pmtiles_path: str
    pmtiles_engine: str
    pmtiles_bin: str
    ogr2ogr_bin: str
    tippecanoe_bin: str
    tool_paths: dict[str, str]
    tool_versions: dict[str, str]
    minzoom: int
    maxzoom: int | None
    maxzoom_mode: str
    maxzoom_reason: str | None
    source_resolution_meters: float | None
    source_scale_denominator: float | None
    pmtiles_maxzoom: int | None
    pmtiles_maxzoom_reason: str | None
    pmtiles_detail_hint: str | None
    pmtiles_profile_path: str
    tile_simplify: float | None
    tippecanoe_extra_args: tuple[str, ...]
    required_properties: tuple[str, ...]
    group_id_fields: tuple[str, ...]
    group_id_token_length: int | None
    group_id_fail_on_ambiguous_geometry: bool
    title: str
    description: str
    commands: list[list[str]]


@dataclass(frozen=True)
class VectorValidationResult:
    fgb_path: str
    pmtiles_path: str
    valid: bool
    errors: tuple[str, ...]
    pmtiles_verify: str | None = None
    decoded_feature_count: int | None = None
    decoded_property_keys: tuple[str, ...] = ()
    decoded_tile: str | None = None
    decoded_z0_feature_count: int | None = None
    decoded_z0_property_keys: tuple[str, ...] = ()
    point_retention_valid: bool | None = None
    pmtiles_profile_path: str | None = None
    profile: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None
    required_properties: tuple[str, ...] = ()


def default_work_dir(asset_slug: str, *, root: Path | None = None) -> Path:
    """Return the repo-standard local work directory for generated vector files."""
    base = root or Path(os.environ.get(WORK_ROOT_ENV) or tempfile.gettempdir())
    return base / "shared-datasets-1" / "vector-assets" / asset_slug


def slug_to_layer_name(asset_slug: str) -> str:
    return asset_slug.replace("-", "_")


def validate_asset_slug(asset_slug: str) -> None:
    if not SLUG_PATTERN.fullmatch(asset_slug):
        raise ValueError(f"asset slug must be lowercase kebab-case: {asset_slug!r}")


def tippecanoe_option_value(args: Sequence[str], index: int) -> str | None:
    arg = args[index]
    if "=" in arg:
        return arg.split("=", 1)[1]
    if index + 1 < len(args) and not args[index + 1].startswith("-"):
        return args[index + 1]
    return None


def validate_tippecanoe_args(
    args: Sequence[str],
    *,
    allow_point_dropping: bool = False,
    required_properties: Sequence[str] = (),
) -> None:
    required = {property_name for property_name in required_properties if property_name}
    included_properties: set[str] = set()
    for index, arg in enumerate(args):
        option = arg.split("=", 1)[0]
        if option in PROPERTY_STRIPPING_TIPPECANOE_ARGS:
            raise ValueError(
                f"{option} strips all feature properties from PMTiles and breaks the catalog "
                "feature inspector. Use narrower --exclude/--include filters or add compact "
                "synthetic properties instead."
            )
        if option in {"--exclude", "-x"}:
            value = tippecanoe_option_value(args, index)
            if value in required:
                raise ValueError(f"{option} would strip required PMTiles property {value!r}")
        if option in {"--include", "-y"}:
            value = tippecanoe_option_value(args, index)
            if value:
                included_properties.add(value)
        if option == "--drop-rate" and arg != "--drop-rate=1" and not allow_point_dropping:
            raise ValueError(
                "--drop-rate values other than 1 can drop point features. Pass "
                "--allow-point-dropping only for a documented exception."
            )
        if option in POINT_DROPPING_TIPPECANOE_ARGS and not allow_point_dropping:
            raise ValueError(
                f"{option} can drop or alter point features. Pass --allow-point-dropping "
                "only for a documented exception."
            )
    if included_properties and not required.issubset(included_properties):
        missing = ", ".join(sorted(required - included_properties))
        raise ValueError(f"--include would omit required PMTiles propert{'y' if len(required - included_properties) == 1 else 'ies'}: {missing}")


def parse_maxzoom(value: int | str | None) -> tuple[int | None, str]:
    if value is None:
        return None, AUTO_MAXZOOM
    if isinstance(value, int):
        return value, "manual"
    normalized = value.strip().lower()
    if normalized == AUTO_MAXZOOM:
        return None, AUTO_MAXZOOM
    try:
        return int(normalized), "manual"
    except ValueError as exc:
        raise ValueError("maxzoom must be an integer or 'auto'") from exc


def validate_manual_maxzoom(
    *,
    maxzoom: int,
    minzoom: int,
    maxzoom_reason: str | None,
    allow_low_maxzoom: bool,
    allow_high_maxzoom: bool,
) -> None:
    if maxzoom < 0 or maxzoom < minzoom:
        raise ValueError("zoom range must satisfy 0 <= minzoom <= maxzoom")
    if not maxzoom_reason:
        raise ValueError("manual PMTiles maxzoom requires --maxzoom-reason")
    if maxzoom < PMTILES_LOW_MAXZOOM_THRESHOLD and not allow_low_maxzoom:
        raise ValueError(
            f"PMTiles maxzoom below {PMTILES_LOW_MAXZOOM_THRESHOLD} requires "
            "--allow-low-maxzoom and a documented --maxzoom-reason"
        )
    if maxzoom > DEFAULT_MAXZOOM_CAP and not allow_high_maxzoom:
        raise ValueError(
            f"PMTiles maxzoom above {DEFAULT_MAXZOOM_CAP} requires "
            "--allow-high-maxzoom and a documented --maxzoom-reason"
        )


def validate_explicit_pmtiles_maxzoom(
    *,
    pmtiles_maxzoom: int | None,
    pmtiles_maxzoom_reason: str | None,
    allow_high_maxzoom: bool,
    allow_low_maxzoom: bool,
) -> None:
    if pmtiles_maxzoom is None:
        return
    if not pmtiles_maxzoom_reason:
        raise ValueError("pmtiles_maxzoom requires pmtiles_maxzoom_reason")
    if pmtiles_maxzoom < 0:
        raise ValueError("pmtiles_maxzoom must be non-negative")
    if pmtiles_maxzoom < 6 and not allow_low_maxzoom:
        raise ValueError("pmtiles_maxzoom below 6 requires --allow-low-maxzoom")
    if pmtiles_maxzoom > DEFAULT_MAXZOOM_CAP and not allow_high_maxzoom:
        raise ValueError(f"pmtiles_maxzoom above {DEFAULT_MAXZOOM_CAP} requires --allow-high-maxzoom")


def path_is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def ensure_local_output_path(path: Path, *, label: str, allow_repo_output: bool = False) -> None:
    if path_is_under(path, REPO_ROOT) and not allow_repo_output:
        raise ValueError(
            f"{label} must be outside the repository. Use --work-dir under system temp "
            "or pass --allow-repo-output only for tiny intentional fixtures."
        )


def require_executable(name: str) -> str:
    resolved = shutil.which(name) if not Path(name).exists() else name
    if not resolved:
        raise FileNotFoundError(f"Required executable not found: {name}")
    return resolved


def executable_path(name: str) -> str:
    resolved = shutil.which(name) if not Path(name).exists() else name
    return resolved or f"unresolved: {name}"


def executable_version(command: Sequence[str]) -> str:
    if os.environ.get("SHARED_DATASETS_SKIP_TOOL_VERSION_PROBES") == "1":
        return "not probed; SHARED_DATASETS_SKIP_TOOL_VERSION_PROBES=1 was set"
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    return (completed.stdout or "").strip().splitlines()[0] if completed.stdout else "unknown"


def tool_versions(*, ogr2ogr_bin: str, tippecanoe_bin: str, pmtiles_bin: str) -> dict[str, str]:
    return {
        "ogr2ogr": executable_version([ogr2ogr_bin, "--version"]),
        "tippecanoe": executable_version([tippecanoe_bin, "--version"]),
        "pmtiles": executable_version([pmtiles_bin, "version"]),
    }


def tool_paths(*, ogr2ogr_bin: str, tippecanoe_bin: str, pmtiles_bin: str) -> dict[str, str]:
    return {
        "ogr2ogr": executable_path(ogr2ogr_bin),
        "tippecanoe": executable_path(tippecanoe_bin),
        "pmtiles": executable_path(pmtiles_bin),
    }


def build_plan(
    *,
    source: Path,
    asset_slug: str,
    layer_name: str | None = None,
    source_layer: str | None = None,
    work_dir: Path | None = None,
    output_dir: Path | None = None,
    minzoom: int = 0,
    maxzoom: int | str | None = AUTO_MAXZOOM,
    maxzoom_reason: str | None = None,
    source_resolution_meters: float | None = None,
    source_scale_denominator: float | None = None,
    pmtiles_maxzoom: int | None = None,
    pmtiles_maxzoom_reason: str | None = None,
    pmtiles_detail_hint: str | None = None,
    tile_simplify: float | None = None,
    title: str | None = None,
    description: str | None = None,
    ogr2ogr_bin: str = "ogr2ogr",
    tippecanoe_bin: str = "tippecanoe",
    pmtiles_bin: str = "pmtiles",
    pmtiles_engine: str = "tippecanoe",
    tippecanoe_extra_args: Sequence[str] = (),
    required_properties: Sequence[str] = (),
    group_id_fields: Sequence[str] = (),
    group_id_token_length: int | None = None,
    group_id_fail_on_ambiguous_geometry: bool = False,
    allow_repo_output: bool = False,
    allow_low_maxzoom: bool = False,
    allow_high_maxzoom: bool = False,
    allow_point_dropping: bool = False,
) -> VectorBuildPlan:
    validate_asset_slug(asset_slug)
    resolved_maxzoom, maxzoom_mode = parse_maxzoom(maxzoom)
    if minzoom < 0:
        raise ValueError("zoom range must satisfy 0 <= minzoom <= maxzoom")
    if resolved_maxzoom is not None:
        validate_manual_maxzoom(
            maxzoom=resolved_maxzoom,
            minzoom=minzoom,
            maxzoom_reason=maxzoom_reason,
            allow_low_maxzoom=allow_low_maxzoom,
            allow_high_maxzoom=allow_high_maxzoom,
        )
    validate_explicit_pmtiles_maxzoom(
        pmtiles_maxzoom=pmtiles_maxzoom,
        pmtiles_maxzoom_reason=pmtiles_maxzoom_reason,
        allow_low_maxzoom=allow_low_maxzoom,
        allow_high_maxzoom=allow_high_maxzoom,
    )
    if source_resolution_meters is not None and source_resolution_meters <= 0:
        raise ValueError("source resolution must be positive")
    if source_scale_denominator is not None and source_scale_denominator <= 0:
        raise ValueError("source scale denominator must be positive")
    pmtiles_detail_hint = validate_detail_hint(pmtiles_detail_hint)
    if tile_simplify is not None and tile_simplify <= 0:
        raise ValueError("tile simplification tolerance must be positive")
    if pmtiles_engine not in {"tippecanoe", "gdal-mbtiles"}:
        raise ValueError("pmtiles engine must be 'tippecanoe' or 'gdal-mbtiles'")
    if not source.exists():
        raise FileNotFoundError(f"Source vector file does not exist: {source}")
    group_id_field_tuple = tuple(dict.fromkeys(field.strip() for field in group_id_fields if field.strip()))
    if group_id_token_length is not None and group_id_token_length < 8:
        raise ValueError("group ID token length must be at least 8")
    required_property_tuple = tuple(
        dict.fromkeys(
            [
                *(property_name.strip() for property_name in required_properties if property_name.strip()),
                *([GENERATED_GROUP_ID_COLUMN] if group_id_field_tuple else []),
            ]
        )
    )
    validate_tippecanoe_args(
        tippecanoe_extra_args,
        allow_point_dropping=allow_point_dropping,
        required_properties=required_property_tuple,
    )

    layer = layer_name or slug_to_layer_name(asset_slug)
    work = work_dir or default_work_dir(asset_slug)
    output = output_dir or (work / "publish")
    build = work / "build"
    ensure_local_output_path(work, label="work directory", allow_repo_output=allow_repo_output)
    ensure_local_output_path(output, label="output directory", allow_repo_output=allow_repo_output)

    fgb_path = output / f"{asset_slug}.fgb"
    group_id_source_path = build / f"{asset_slug}-group-id-source.geojson" if group_id_field_tuple else None
    group_id_input_path = build / f"{asset_slug}-grouped.geojson" if group_id_field_tuple else None
    tippecanoe_input_path = build / f"{asset_slug}.geojson"
    mbtiles_path = build / f"{asset_slug}.mbtiles"
    pmtiles_path = output / f"{asset_slug}.pmtiles"
    pmtiles_profile_path = output / "pmtiles-profile.json"
    dataset_title = title or asset_slug.replace("-", " ").title()
    dataset_description = description or f"{dataset_title} vector tiles"
    effective_tippecanoe_args = (*DEFAULT_TIPPECANOE_ARGS, *tippecanoe_extra_args)

    fgb_source_path = group_id_input_path or source
    fgb_command = [
        ogr2ogr_bin,
        "-f",
        "FlatGeobuf",
        "-makevalid",
        "-nln",
        layer,
        "-nlt",
        "PROMOTE_TO_MULTI",
        "-lco",
        "SPATIAL_INDEX=YES",
        str(fgb_path),
        str(fgb_source_path),
    ]
    if source_layer and group_id_input_path is None:
        fgb_command.append(source_layer)

    commands: list[list[str]] = []
    if group_id_field_tuple and group_id_source_path and group_id_input_path:
        group_source_command = [
            ogr2ogr_bin,
            "-f",
            "GeoJSON",
            "-t_srs",
            "EPSG:4326",
            str(group_id_source_path),
            str(source),
        ]
        if source_layer:
            group_source_command.append(source_layer)
        group_id_command = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "shared_dataset_group_ids.py"),
            str(group_id_source_path),
            "--asset-slug",
            asset_slug,
            "--out",
            str(group_id_input_path),
        ]
        for field in group_id_field_tuple:
            group_id_command.extend(["--grouping-field", field])
        if group_id_token_length is not None:
            group_id_command.extend(["--token-length", str(group_id_token_length)])
        if group_id_fail_on_ambiguous_geometry:
            group_id_command.append("--fail-on-ambiguous-geometry")
        commands.extend([group_source_command, group_id_command])
    commands.append(fgb_command)

    plan = VectorBuildPlan(
        source=str(source),
        asset_slug=asset_slug,
        layer_name=layer,
        work_dir=str(work),
        output_dir=str(output),
        group_id_source_path=str(group_id_source_path) if group_id_source_path else None,
        group_id_input_path=str(group_id_input_path) if group_id_input_path else None,
        fgb_path=str(fgb_path),
        tippecanoe_input_path=str(tippecanoe_input_path),
        mbtiles_path=str(mbtiles_path),
        pmtiles_path=str(pmtiles_path),
        pmtiles_engine=pmtiles_engine,
        pmtiles_bin=pmtiles_bin,
        ogr2ogr_bin=ogr2ogr_bin,
        tippecanoe_bin=tippecanoe_bin,
        tool_paths=tool_paths(
            ogr2ogr_bin=ogr2ogr_bin,
            tippecanoe_bin=tippecanoe_bin,
            pmtiles_bin=pmtiles_bin,
        ),
        tool_versions=tool_versions(
            ogr2ogr_bin=ogr2ogr_bin,
            tippecanoe_bin=tippecanoe_bin,
            pmtiles_bin=pmtiles_bin,
        ),
        minzoom=minzoom,
        maxzoom=resolved_maxzoom,
        maxzoom_mode=maxzoom_mode,
        maxzoom_reason=maxzoom_reason,
        source_resolution_meters=source_resolution_meters,
        source_scale_denominator=source_scale_denominator,
        pmtiles_maxzoom=pmtiles_maxzoom,
        pmtiles_maxzoom_reason=pmtiles_maxzoom_reason,
        pmtiles_detail_hint=pmtiles_detail_hint,
        pmtiles_profile_path=str(pmtiles_profile_path),
        tile_simplify=tile_simplify,
        tippecanoe_extra_args=effective_tippecanoe_args,
        required_properties=required_property_tuple,
        group_id_fields=group_id_field_tuple,
        group_id_token_length=group_id_token_length,
        group_id_fail_on_ambiguous_geometry=group_id_fail_on_ambiguous_geometry,
        title=dataset_title,
        description=dataset_description,
        commands=commands,
    )
    if resolved_maxzoom is not None:
        plan = replace(plan, commands=[*commands, *pmtiles_commands(plan, resolved_maxzoom)])
    return plan


def pmtiles_commands(plan: VectorBuildPlan, maxzoom: int, *, profile: FgbProfile | None = None) -> list[list[str]]:
    synthetic_sql = pmtiles_synthetic_property_sql(plan, profile)
    tile_source_command = [
        plan.ogr2ogr_bin,
        "-f",
        "GeoJSON",
        "-t_srs",
        "EPSG:4326",
        "-nln",
        plan.layer_name,
        "-nlt",
        "PROMOTE_TO_MULTI",
    ]
    if plan.tile_simplify is not None:
        tile_source_command.extend(["-simplify", str(plan.tile_simplify)])
    if synthetic_sql is not None:
        tile_source_command.extend(["-dialect", "SQLite", "-sql", synthetic_sql])
    tile_source_command.extend([plan.tippecanoe_input_path, plan.fgb_path])

    tippecanoe_command = [
        plan.tippecanoe_bin,
        "-f",
        "-q",
        "--projection=EPSG:4326",
        "--minimum-zoom",
        str(plan.minzoom),
        "--maximum-zoom",
        str(maxzoom),
        "-o",
        plan.pmtiles_path,
        "-l",
        plan.layer_name,
        "-n",
        plan.title,
        "-N",
        plan.description,
    ]
    tippecanoe_command.extend(plan.tippecanoe_extra_args)
    tippecanoe_command.append(plan.tippecanoe_input_path)

    mbtiles_command = [
        plan.ogr2ogr_bin,
        "-f",
        "MBTiles",
        "-nln",
        plan.layer_name,
        "-nlt",
        "PROMOTE_TO_MULTI",
        "-dsco",
        f"NAME={plan.title}",
        "-dsco",
        f"DESCRIPTION={plan.description}",
        "-dsco",
        f"MINZOOM={plan.minzoom}",
        "-dsco",
        f"MAXZOOM={maxzoom}",
        "-lco",
        f"NAME={plan.layer_name}",
        "-lco",
        f"MINZOOM={plan.minzoom}",
        "-lco",
        f"MAXZOOM={maxzoom}",
    ]
    if plan.tile_simplify is not None:
        mbtiles_command.extend(["-simplify", str(plan.tile_simplify)])
    if synthetic_sql is not None:
        mbtiles_command.extend(["-dialect", "SQLite", "-sql", synthetic_sql])
    mbtiles_command.extend([plan.mbtiles_path, plan.fgb_path])

    convert_command = [plan.pmtiles_bin, "convert", plan.mbtiles_path, plan.pmtiles_path]
    if plan.pmtiles_engine == "gdal-mbtiles":
        return [mbtiles_command, convert_command]
    return [tile_source_command, tippecanoe_command]


def sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def pmtiles_synthetic_property_sql(plan: VectorBuildPlan, profile: FgbProfile | None = None) -> str | None:
    if profile is None or profile.property_keys:
        return None
    return (
        f"SELECT *, {sql_string(plan.layer_name)} AS {sql_identifier(SYNTHETIC_PMTILES_PROPERTY)} "
        f"FROM {sql_identifier(plan.layer_name)}"
    )


def run_command(command: Sequence[str]) -> None:
    subprocess.run(command, check=True)


def remove_existing_outputs(plan: VectorBuildPlan, *, keep_mbtiles: bool) -> None:
    paths = [
        Path(plan.fgb_path),
        Path(plan.pmtiles_path),
        Path(plan.tippecanoe_input_path),
        Path(plan.pmtiles_profile_path),
    ]
    if plan.group_id_source_path:
        paths.append(Path(plan.group_id_source_path))
    if plan.group_id_input_path:
        paths.append(Path(plan.group_id_input_path))
    if not keep_mbtiles:
        paths.append(Path(plan.mbtiles_path))
    for path in paths:
        if path.exists():
            path.unlink()


def run_build(plan: VectorBuildPlan, *, overwrite: bool = False, keep_mbtiles: bool = False) -> VectorValidationResult:
    for executable in required_executables(plan):
        require_executable(executable)

    Path(plan.output_dir).mkdir(parents=True, exist_ok=True)
    Path(plan.mbtiles_path).parent.mkdir(parents=True, exist_ok=True)

    outputs = [Path(plan.fgb_path), Path(plan.pmtiles_path), Path(plan.pmtiles_profile_path)]
    if any(path.exists() for path in outputs) and not overwrite:
        existing = ", ".join(str(path) for path in outputs if path.exists())
        raise FileExistsError(f"Refusing to overwrite existing generated output(s): {existing}")
    if overwrite:
        remove_existing_outputs(plan, keep_mbtiles=keep_mbtiles)

    pre_profile_commands = plan.commands
    if plan.maxzoom is not None:
        pre_profile_commands = plan.commands[: -len(pmtiles_commands(plan, plan.maxzoom))]
    for command in pre_profile_commands:
        run_command(command)
    profile = profile_fgb(Path(plan.fgb_path), ogr2ogr_bin=plan.ogr2ogr_bin)
    recommendation = resolve_maxzoom(plan, profile)
    if recommendation.status != "recommended" or recommendation.maxzoom is None:
        write_pmtiles_profile(plan, profile, recommendation)
        raise ValueError(f"could not resolve PMTiles maxzoom: {recommendation.reason}")

    actual_pmtiles_commands = pmtiles_commands(plan, recommendation.maxzoom, profile=profile)
    for command in actual_pmtiles_commands:
        run_command(command)

    if not keep_mbtiles:
        intermediates = [Path(plan.mbtiles_path), Path(plan.tippecanoe_input_path)]
        if plan.group_id_source_path:
            intermediates.append(Path(plan.group_id_source_path))
        if plan.group_id_input_path:
            intermediates.append(Path(plan.group_id_input_path))
        for intermediate in intermediates:
            if intermediate.exists():
                intermediate.unlink()
    validation = validate_outputs(
        Path(plan.fgb_path),
        Path(plan.pmtiles_path),
        pmtiles_bin=plan.pmtiles_bin,
        profile=profile,
        recommendation=recommendation,
        pmtiles_profile_path=Path(plan.pmtiles_profile_path),
        required_properties=plan.required_properties,
        decode_zoom=plan.minzoom,
    )
    write_pmtiles_profile(plan, profile, recommendation, validation=validation)
    return validation


def required_executables(plan: VectorBuildPlan) -> set[str]:
    executables = {plan.ogr2ogr_bin}
    if plan.pmtiles_engine == "gdal-mbtiles":
        executables.add(plan.pmtiles_bin)
    else:
        executables.add(plan.tippecanoe_bin)
    return executables


def resolve_maxzoom(plan: VectorBuildPlan, profile: FgbProfile) -> ZoomRecommendation:
    if plan.maxzoom is not None:
        return ZoomRecommendation(
            status="recommended",
            maxzoom=plan.maxzoom,
            confidence="high",
            reason=plan.maxzoom_reason or "manual maxzoom override",
            evidence={"source": "manual_maxzoom", "maxzoom": plan.maxzoom},
        )
    return recommend_maxzoom(
        profile,
        source_resolution_meters=plan.source_resolution_meters,
        source_scale_denominator=plan.source_scale_denominator,
        pmtiles_maxzoom=plan.pmtiles_maxzoom,
        pmtiles_maxzoom_reason=plan.pmtiles_maxzoom_reason,
        pmtiles_detail_hint=plan.pmtiles_detail_hint,
    )


def write_pmtiles_profile(
    plan: VectorBuildPlan,
    profile: FgbProfile,
    recommendation: ZoomRecommendation,
    *,
    validation: VectorValidationResult | None = None,
) -> None:
    payload = profile_payload(profile, recommendation)
    payload["asset_slug"] = plan.asset_slug
    payload["minzoom"] = plan.minzoom
    if plan.required_properties:
        payload["required_properties"] = list(plan.required_properties)
    synthetic_sql = pmtiles_synthetic_property_sql(plan, profile)
    if synthetic_sql is not None:
        payload["pmtiles_synthetic_properties"] = {SYNTHETIC_PMTILES_PROPERTY: plan.layer_name}
    if validation is not None:
        payload["validation"] = {
            "valid": validation.valid,
            "errors": list(validation.errors),
            "pmtiles_verify": validation.pmtiles_verify,
            "decoded_feature_count": validation.decoded_feature_count,
            "decoded_property_keys": list(validation.decoded_property_keys),
            "decoded_tile": validation.decoded_tile,
            "decoded_z0_feature_count": validation.decoded_z0_feature_count,
            "decoded_z0_property_keys": list(validation.decoded_z0_property_keys),
            "point_retention_valid": validation.point_retention_valid,
        }
    Path(plan.pmtiles_profile_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def validate_outputs(
    fgb_path: Path,
    pmtiles_path: Path,
    *,
    pmtiles_bin: str = "pmtiles",
    profile: FgbProfile | None = None,
    recommendation: ZoomRecommendation | None = None,
    pmtiles_profile_path: Path | None = None,
    required_properties: Sequence[str] = (),
    decode_zoom: int = 0,
) -> VectorValidationResult:
    errors: list[str] = []
    required_property_tuple = tuple(dict.fromkeys(property_name for property_name in required_properties if property_name))
    pmtiles_verify: str | None = None
    decoded_feature_count: int | None = None
    decoded_property_keys: tuple[str, ...] = ()
    decoded_tile: str | None = None
    decoded_z0_feature_count: int | None = None
    decoded_z0_property_keys: tuple[str, ...] = ()
    point_retention_valid: bool | None = None
    fgb_layer_name: str | None = None
    fgb_property_keys: tuple[str, ...] | None = None
    if decode_zoom < 0:
        errors.append("decode_zoom must be non-negative")
    for label, path in (("FGB", fgb_path), ("PMTiles", pmtiles_path)):
        if not path.exists():
            errors.append(f"{label} file does not exist: {path}")
        elif path.stat().st_size <= 0:
            errors.append(f"{label} file is empty: {path}")

    if fgb_path.exists() and shutil.which("ogrinfo"):
        completed = subprocess.run(
            ["ogrinfo", "-ro", "-al", "-so", "-json", str(fgb_path)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            fallback = subprocess.run(
                ["ogrinfo", "-ro", "-al", "-so", str(fgb_path)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if fallback.returncode != 0:
                errors.append(f"ogrinfo failed for FGB: {fallback.stderr.strip()}")
            elif "Feature Count: 0" in fallback.stdout:
                errors.append("FGB layer has no features.")
            elif "Feature Count:" not in fallback.stdout:
                errors.append("Could not confirm FGB feature count with ogrinfo.")
            else:
                fgb_layer_name = parse_ogrinfo_layer_name(fallback.stdout)
        else:
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                errors.append(f"ogrinfo did not return valid JSON: {exc}")
            else:
                layers = payload.get("layers") or []
                if not layers:
                    errors.append("FGB has no vector layers.")
                elif int(layers[0].get("featureCount") or 0) <= 0:
                    errors.append("FGB layer has no features.")
                else:
                    fgb_layer_name = layers[0].get("name")
                    fgb_property_keys = ogrinfo_property_keys(layers[0])

        if fgb_layer_name:
            geometry_result = validate_fgb_geometry_validity(fgb_path, fgb_layer_name)
            if geometry_result.returncode != 0:
                errors.append(
                    "Could not validate FGB geometry validity with ogrinfo SQLite/GEOS: "
                    f"{geometry_result.stderr.strip() or geometry_result.stdout.strip()}"
                )
            else:
                invalid_count = parse_invalid_geometry_count(geometry_result.stdout)
                if invalid_count is None:
                    errors.append("Could not parse FGB invalid geometry count from ogrinfo output.")
                elif invalid_count > 0:
                    errors.append(
                        f"FGB layer has {invalid_count} invalid geometries after build. "
                        "Repair source geometries with GDAL -makevalid before publishing."
                    )

    if required_property_tuple:
        if fgb_property_keys is None:
            errors.append("Could not verify required FGB properties with ogrinfo.")
        else:
            missing_fgb = sorted(set(required_property_tuple) - set(fgb_property_keys))
            if missing_fgb:
                errors.append(f"FGB is missing required propert{'y' if len(missing_fgb) == 1 else 'ies'}: {', '.join(missing_fgb)}")

    if pmtiles_path.exists() and shutil.which(pmtiles_bin):
        completed = subprocess.run(
            [pmtiles_bin, "verify", str(pmtiles_path)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            errors.append(f"pmtiles verify failed: {completed.stderr.strip() or completed.stdout.strip()}")
            pmtiles_verify = "failed"
        else:
            pmtiles_verify = "passed"

    decode_summary = (
        None
        if decode_zoom < 0
        else decoded_pmtiles_property_summary(
            pmtiles_path,
            zoom=decode_zoom,
            bounds=profile.bounds if profile else None,
        )
    )
    if decode_summary is not None:
        decoded_feature_count, decoded_property_keys, decoded_tile_values = decode_summary
        decoded_tile = "/".join(str(value) for value in decoded_tile_values)
        if decoded_tile_values == (0, 0, 0):
            decoded_z0_feature_count = decoded_feature_count
            decoded_z0_property_keys = decoded_property_keys
        if decoded_feature_count > 0:
            missing_pmtiles = sorted(set(required_property_tuple) - set(decoded_property_keys))
            if missing_pmtiles:
                errors.append(
                    f"PMTiles decoded features are missing required propert{'y' if len(missing_pmtiles) == 1 else 'ies'}: "
                    f"{', '.join(missing_pmtiles)}"
                )
            if not decoded_property_keys:
                errors.append(
                    f"PMTiles features decode with no feature properties at {decoded_tile}. "
                    "Do not publish geometry-only display tiles; preserve compact source properties "
                    "or add a synthetic property such as source_layer for the catalog inspector."
                )
        if (
            profile is not None
            and profile.feature_count > 0
            and profile.point_feature_count == profile.feature_count
            and decode_zoom == 0
        ):
            point_retention_valid = decoded_feature_count == profile.point_feature_count
            if not point_retention_valid:
                errors.append(
                    "Decoded PMTiles z0 feature count does not match point profile count: "
                    f"{decoded_feature_count} != {profile.point_feature_count}"
                )
    elif required_property_tuple:
        errors.append("Could not verify required PMTiles properties with tippecanoe-decode.")

    return VectorValidationResult(
        fgb_path=str(fgb_path),
        pmtiles_path=str(pmtiles_path),
        valid=not errors,
        errors=tuple(errors),
        pmtiles_verify=pmtiles_verify,
        decoded_feature_count=decoded_feature_count,
        decoded_property_keys=decoded_property_keys,
        decoded_tile=decoded_tile,
        decoded_z0_feature_count=decoded_z0_feature_count,
        decoded_z0_property_keys=decoded_z0_property_keys,
        point_retention_valid=point_retention_valid,
        pmtiles_profile_path=str(pmtiles_profile_path) if pmtiles_profile_path else None,
        profile=asdict(profile) if profile else None,
        recommendation=asdict(recommendation) if recommendation else None,
        required_properties=required_property_tuple,
    )


def ogrinfo_property_keys(layer: dict[str, Any]) -> tuple[str, ...]:
    fields = layer.get("fields") or []
    keys: list[str] = []
    for field in fields:
        if isinstance(field, dict) and field.get("name"):
            keys.append(str(field["name"]))
    return tuple(sorted(keys))


def parse_ogrinfo_layer_name(output: str) -> str | None:
    match = re.search(r"^Layer name:\s*(.+?)\s*$", output, flags=re.MULTILINE)
    return match.group(1) if match else None


def validate_fgb_geometry_validity(fgb_path: Path, layer_name: str) -> subprocess.CompletedProcess[str]:
    sql = (
        "SELECT COUNT(*) AS invalid_geometry_count "
        f"FROM {sql_identifier(layer_name)} "
        "WHERE geometry IS NOT NULL AND NOT ST_IsValid(geometry)"
    )
    return subprocess.run(
        ["ogrinfo", "-ro", "-q", "-dialect", "SQLite", "-sql", sql, str(fgb_path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def parse_invalid_geometry_count(output: str) -> int | None:
    match = re.search(r"invalid_geometry_count\s*\([^)]+\)\s*=\s*(\d+)", output)
    return int(match.group(1)) if match else None


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    lat = max(-85.05112878, min(85.05112878, lat))
    lon = max(-180.0, min(180.0, lon))
    tile_count = 2**zoom
    x = int((lon + 180.0) / 360.0 * tile_count)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * tile_count)
    return max(0, min(tile_count - 1, x)), max(0, min(tile_count - 1, y))


def candidate_decode_tiles(*, zoom: int, bounds: tuple[float, float, float, float] | None) -> tuple[tuple[int, int], ...]:
    if zoom == 0 or bounds is None:
        return ((0, 0),)
    min_lon, min_lat, max_lon, max_lat = bounds
    center_lon = (min_lon + max_lon) / 2
    center_lat = (min_lat + max_lat) / 2
    lonlats = (
        (center_lon, center_lat),
        (min_lon, min_lat),
        (min_lon, max_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
    )
    tiles: list[tuple[int, int]] = []
    for lon, lat in lonlats:
        tile = lonlat_to_tile(lon, lat, zoom)
        if tile not in tiles:
            tiles.append(tile)
    return tuple(tiles)


def decoded_pmtiles_property_summary(
    pmtiles_path: Path,
    *,
    decoder_bin: str = "tippecanoe-decode",
    zoom: int = 0,
    bounds: tuple[float, float, float, float] | None = None,
) -> tuple[int, tuple[str, ...], tuple[int, int, int]] | None:
    """Return decoded feature count and property keys from a likely occupied tile."""
    if not pmtiles_path.exists() or not shutil.which(decoder_bin):
        return None
    if zoom < 0:
        return None

    empty_summary: tuple[int, tuple[str, ...], tuple[int, int, int]] | None = None
    for x, y in candidate_decode_tiles(zoom=zoom, bounds=bounds):
        completed = subprocess.run(
            [decoder_bin, str(pmtiles_path), str(zoom), str(x), str(y)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            continue

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            continue

        feature_count = 0
        property_keys: set[str] = set()
        for layer_or_feature in payload.get("features") or []:
            nested_features = layer_or_feature.get("features")
            if isinstance(nested_features, list):
                for feature in nested_features:
                    feature_count += 1
                    property_keys.update((feature.get("properties") or {}).keys())
            else:
                feature_count += 1
                property_keys.update((layer_or_feature.get("properties") or {}).keys())

        summary = (feature_count, tuple(sorted(property_keys)), (zoom, x, y))
        if feature_count > 0:
            return summary
        empty_summary = summary

    return empty_summary


def _cmd_workdir(args: argparse.Namespace) -> int:
    validate_asset_slug(args.asset_slug)
    print(default_work_dir(args.asset_slug, root=Path(args.root) if args.root else None))
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    plan = build_plan(
        source=Path(args.source),
        asset_slug=args.asset_slug,
        layer_name=args.layer_name,
        source_layer=args.source_layer,
        work_dir=Path(args.work_dir) if args.work_dir else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        minzoom=args.minzoom,
        maxzoom=args.maxzoom,
        maxzoom_reason=args.maxzoom_reason,
        source_resolution_meters=args.source_resolution_meters,
        source_scale_denominator=args.source_scale_denominator,
        pmtiles_maxzoom=args.pmtiles_maxzoom,
        pmtiles_maxzoom_reason=args.pmtiles_maxzoom_reason,
        pmtiles_detail_hint=args.pmtiles_detail_hint,
        tile_simplify=args.tile_simplify,
        title=args.title,
        description=args.description,
        ogr2ogr_bin=args.ogr2ogr_bin,
        tippecanoe_bin=args.tippecanoe_bin,
        pmtiles_bin=args.pmtiles_bin,
        pmtiles_engine=args.pmtiles_engine,
        tippecanoe_extra_args=args.tippecanoe_arg,
        required_properties=args.required_property,
        group_id_fields=args.group_id_field,
        group_id_token_length=args.group_id_token_length,
        group_id_fail_on_ambiguous_geometry=args.group_id_fail_on_ambiguous_geometry,
        allow_repo_output=args.allow_repo_output,
        allow_low_maxzoom=args.allow_low_maxzoom,
        allow_high_maxzoom=args.allow_high_maxzoom,
        allow_point_dropping=args.allow_point_dropping,
    )
    if args.dry_run:
        print(json.dumps(asdict(plan), indent=2, sort_keys=True))
        return 0
    result = run_build(plan, overwrite=args.overwrite, keep_mbtiles=args.keep_mbtiles)
    print(json.dumps({"plan": asdict(plan), "validation": asdict(result)}, indent=2, sort_keys=True))
    return 0 if result.valid else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    result = validate_outputs(
        Path(args.fgb),
        Path(args.pmtiles),
        pmtiles_bin=args.pmtiles_bin,
        required_properties=args.required_property,
        decode_zoom=args.decode_zoom,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.valid else 1


def _cmd_recommend_maxzoom(args: argparse.Namespace) -> int:
    profile = profile_fgb(Path(args.fgb), ogr2ogr_bin=args.ogr2ogr_bin)
    recommendation = recommend_maxzoom(
        profile,
        source_resolution_meters=args.source_resolution_meters,
        source_scale_denominator=args.source_scale_denominator,
        pmtiles_maxzoom=args.pmtiles_maxzoom,
        pmtiles_maxzoom_reason=args.pmtiles_maxzoom_reason,
        pmtiles_detail_hint=args.pmtiles_detail_hint,
    )
    print(json.dumps(profile_payload(profile, recommendation), indent=2, sort_keys=True))
    return 0 if recommendation.status == "recommended" else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and validate shared-datasets vector artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    workdir_parser = subparsers.add_parser("workdir", help="Print the standard temp work directory for an asset.")
    workdir_parser.add_argument("asset_slug")
    workdir_parser.add_argument("--root", help=f"Override {WORK_ROOT_ENV} or the system temp root.")
    workdir_parser.set_defaults(func=_cmd_workdir)

    build_parser = subparsers.add_parser("build", help="Build FGB and PMTiles artifacts from a source vector file.")
    build_parser.add_argument("source", help="Source vector dataset, such as a .shp, .fgb, .geojson, or .gpkg file.")
    build_parser.add_argument("--asset-slug", required=True, help="Lowercase kebab-case shared dataset asset slug.")
    build_parser.add_argument("--layer-name", help="Output layer name. Defaults to the asset slug with underscores.")
    build_parser.add_argument("--source-layer", help="Optional source layer name for multi-layer input datasets.")
    build_parser.add_argument("--work-dir", help="Work directory for intermediates and default publish output.")
    build_parser.add_argument("--output-dir", help="Directory where final .fgb and .pmtiles files are written.")
    build_parser.add_argument("--minzoom", type=int, default=0)
    build_parser.add_argument(
        "--maxzoom",
        default=AUTO_MAXZOOM,
        help="PMTiles maximum zoom as an integer, or 'auto' to resolve after FGB profiling. Defaults to auto.",
    )
    build_parser.add_argument("--maxzoom-reason", help="Required when --maxzoom is a manual integer override.")
    build_parser.add_argument("--source-resolution-meters", type=float, help="Optional source resolution hint for auto maxzoom.")
    build_parser.add_argument("--source-scale-denominator", type=float, help="Optional source scale denominator hint for auto maxzoom.")
    build_parser.add_argument("--pmtiles-maxzoom", type=int, help="Optional explicit PMTiles maxzoom metadata hint for auto mode.")
    build_parser.add_argument("--pmtiles-maxzoom-reason", help="Reason required with --pmtiles-maxzoom.")
    build_parser.add_argument(
        "--pmtiles-detail-hint",
        choices=["coarse", "medium", "detailed"],
        help="Optional semantic display-detail hint used by auto maxzoom.",
    )
    build_parser.add_argument(
        "--tile-simplify",
        type=float,
        help=(
            "Optional source-unit simplification tolerance for PMTiles only. "
            "Use for dense display tiles; canonical FGB remains unsimplified."
        ),
    )
    build_parser.add_argument("--title", help="Tileset title metadata.")
    build_parser.add_argument("--description", help="Tileset description metadata.")
    build_parser.add_argument("--ogr2ogr-bin", default="ogr2ogr")
    build_parser.add_argument("--tippecanoe-bin", default="tippecanoe")
    build_parser.add_argument(
        "--tippecanoe-arg",
        action="append",
        default=[],
        help=(
            "Additional argument passed through to Tippecanoe. Repeat for multiple flags. "
            "The build already adds --no-feature-limit, --no-tile-size-limit, and --drop-rate=1 "
            "so low-zoom tiles retain published point features."
        ),
    )
    build_parser.add_argument("--pmtiles-bin", default="pmtiles")
    build_parser.add_argument(
        "--required-property",
        action="append",
        default=[],
        help=(
            "Property that must be present in the generated FGB schema and decoded PMTiles features. "
            f"Use --required-property {GENERATED_GROUP_ID_COLUMN} when generating shared group IDs."
        ),
    )
    build_parser.add_argument(
        "--group-id-field",
        action="append",
        default=[],
        help=(
            "Curator-selected field used to generate shared_datasets_group_id before FGB creation. "
            "Repeat for composite grouping fields."
        ),
    )
    build_parser.add_argument(
        "--group-id-token-length",
        type=int,
        help="Optional base62 token length for generated shared_datasets_group_id values; defaults to policy calculation.",
    )
    build_parser.add_argument(
        "--group-id-fail-on-ambiguous-geometry",
        action="store_true",
        help=(
            "Fail group ID generation when multiple grouping values share identical collective geometry "
            "and would receive the same generated ID."
        ),
    )
    build_parser.add_argument(
        "--pmtiles-engine",
        choices=["tippecanoe", "gdal-mbtiles"],
        default="tippecanoe",
        help="Prefer Tippecanoe direct PMTiles; use gdal-mbtiles only as an explicit fallback.",
    )
    build_parser.add_argument("--overwrite", action="store_true", help="Replace existing generated local outputs.")
    build_parser.add_argument("--keep-mbtiles", action="store_true", help="Keep the intermediate MBTiles file.")
    build_parser.add_argument("--allow-repo-output", action="store_true", help="Allow generated outputs under repo root.")
    build_parser.add_argument(
        "--allow-low-maxzoom",
        action="store_true",
        help=(
            f"Allow manual PMTiles maxzoom below {PMTILES_LOW_MAXZOOM_THRESHOLD}, "
            "or explicit metadata maxzoom below 6, for a documented exception."
        ),
    )
    build_parser.add_argument(
        "--allow-high-maxzoom",
        action="store_true",
        help=f"Allow PMTiles maxzoom above the default cap of {DEFAULT_MAXZOOM_CAP} for a documented exception.",
    )
    build_parser.add_argument(
        "--allow-point-dropping",
        action="store_true",
        help="Allow Tippecanoe point-dropping or point-altering flags for a documented exception.",
    )
    build_parser.add_argument("--dry-run", action="store_true", help="Print the plan without running GDAL or PMTiles.")
    build_parser.set_defaults(func=_cmd_build)

    validate_parser = subparsers.add_parser("validate", help="Validate existing local FGB and PMTiles artifacts.")
    validate_parser.add_argument("--fgb", required=True)
    validate_parser.add_argument("--pmtiles", required=True)
    validate_parser.add_argument("--pmtiles-bin", default="pmtiles")
    validate_parser.add_argument(
        "--decode-zoom",
        type=int,
        default=0,
        help="Zoom level to sample with tippecanoe-decode when validating PMTiles properties.",
    )
    validate_parser.add_argument(
        "--required-property",
        action="append",
        default=[],
        help="Property that must be present in both the FGB schema and decoded PMTiles properties.",
    )
    validate_parser.set_defaults(func=_cmd_validate)

    recommend_parser = subparsers.add_parser(
        "recommend-maxzoom",
        help="Profile an existing local FGB and print the auto PMTiles maxzoom recommendation.",
    )
    recommend_parser.add_argument("--fgb", required=True, help="Local FlatGeobuf file to profile.")
    recommend_parser.add_argument("--ogr2ogr-bin", default="ogr2ogr")
    recommend_parser.add_argument("--source-resolution-meters", type=float, help="Optional source resolution hint.")
    recommend_parser.add_argument("--source-scale-denominator", type=float, help="Optional source scale denominator hint.")
    recommend_parser.add_argument("--pmtiles-maxzoom", type=int, help="Optional explicit PMTiles maxzoom hint.")
    recommend_parser.add_argument("--pmtiles-maxzoom-reason", help="Reason required with --pmtiles-maxzoom.")
    recommend_parser.add_argument(
        "--pmtiles-detail-hint",
        choices=["coarse", "medium", "detailed"],
        help="Optional semantic display-detail hint.",
    )
    recommend_parser.set_defaults(func=_cmd_recommend_maxzoom)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return args.func(args)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
