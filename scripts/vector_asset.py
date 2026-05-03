#!/usr/bin/env python3
"""Build and validate local vector publish artifacts.

This helper prepares upload-ready files only. Use scripts/gcs_asset.py for the
actual Cloud Storage mutation so generation preconditions stay centralized.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT_ENV = "SHARED_DATASETS_WORKDIR"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DEFAULT_TIPPECANOE_ARGS = ("--no-feature-limit", "--no-tile-size-limit", "--drop-rate=1")
PROPERTY_STRIPPING_TIPPECANOE_ARGS = {"--exclude-all"}


@dataclass(frozen=True)
class VectorBuildPlan:
    source: str
    asset_slug: str
    layer_name: str
    work_dir: str
    output_dir: str
    fgb_path: str
    tippecanoe_input_path: str
    mbtiles_path: str
    pmtiles_path: str
    pmtiles_engine: str
    pmtiles_bin: str
    tool_paths: dict[str, str]
    tool_versions: dict[str, str]
    minzoom: int
    maxzoom: int
    tile_simplify: float | None
    tippecanoe_extra_args: tuple[str, ...]
    title: str
    description: str
    commands: list[list[str]]


@dataclass(frozen=True)
class VectorValidationResult:
    fgb_path: str
    pmtiles_path: str
    valid: bool
    errors: tuple[str, ...]


def default_work_dir(asset_slug: str, *, root: Path | None = None) -> Path:
    """Return the repo-standard local work directory for generated vector files."""
    base = root or Path(os.environ.get(WORK_ROOT_ENV) or tempfile.gettempdir())
    return base / "shared-datasets-1" / "vector-assets" / asset_slug


def slug_to_layer_name(asset_slug: str) -> str:
    return asset_slug.replace("-", "_")


def validate_asset_slug(asset_slug: str) -> None:
    if not SLUG_PATTERN.fullmatch(asset_slug):
        raise ValueError(f"asset slug must be lowercase kebab-case: {asset_slug!r}")


def validate_tippecanoe_args(args: Sequence[str]) -> None:
    for arg in args:
        option = arg.split("=", 1)[0]
        if option in PROPERTY_STRIPPING_TIPPECANOE_ARGS:
            raise ValueError(
                f"{option} strips all feature properties from PMTiles and breaks the catalog "
                "feature inspector. Use narrower --exclude/--include filters or add compact "
                "synthetic properties instead."
            )


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
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
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
    maxzoom: int = 8,
    tile_simplify: float | None = None,
    title: str | None = None,
    description: str | None = None,
    ogr2ogr_bin: str = "ogr2ogr",
    tippecanoe_bin: str = "tippecanoe",
    pmtiles_bin: str = "pmtiles",
    pmtiles_engine: str = "tippecanoe",
    tippecanoe_extra_args: Sequence[str] = (),
    allow_repo_output: bool = False,
) -> VectorBuildPlan:
    validate_asset_slug(asset_slug)
    if minzoom < 0 or maxzoom < 0 or maxzoom < minzoom:
        raise ValueError("zoom range must satisfy 0 <= minzoom <= maxzoom")
    if tile_simplify is not None and tile_simplify <= 0:
        raise ValueError("tile simplification tolerance must be positive")
    if pmtiles_engine not in {"tippecanoe", "gdal-mbtiles"}:
        raise ValueError("pmtiles engine must be 'tippecanoe' or 'gdal-mbtiles'")
    if not source.exists():
        raise FileNotFoundError(f"Source vector file does not exist: {source}")
    validate_tippecanoe_args(tippecanoe_extra_args)

    layer = layer_name or slug_to_layer_name(asset_slug)
    work = work_dir or default_work_dir(asset_slug)
    output = output_dir or (work / "publish")
    build = work / "build"
    ensure_local_output_path(work, label="work directory", allow_repo_output=allow_repo_output)
    ensure_local_output_path(output, label="output directory", allow_repo_output=allow_repo_output)

    fgb_path = output / f"{asset_slug}.fgb"
    tippecanoe_input_path = build / f"{asset_slug}.geojson"
    mbtiles_path = build / f"{asset_slug}.mbtiles"
    pmtiles_path = output / f"{asset_slug}.pmtiles"
    dataset_title = title or asset_slug.replace("-", " ").title()
    dataset_description = description or f"{dataset_title} vector tiles"
    effective_tippecanoe_args = tuple(dict.fromkeys([*DEFAULT_TIPPECANOE_ARGS, *tippecanoe_extra_args]))

    fgb_command = [
        ogr2ogr_bin,
        "-f",
        "FlatGeobuf",
        "-nln",
        layer,
        "-nlt",
        "PROMOTE_TO_MULTI",
        "-lco",
        "SPATIAL_INDEX=YES",
        str(fgb_path),
        str(source),
    ]
    tile_source_command = [
        ogr2ogr_bin,
        "-f",
        "GeoJSON",
        "-t_srs",
        "EPSG:4326",
        "-nln",
        layer,
        "-nlt",
        "PROMOTE_TO_MULTI",
    ]
    if tile_simplify is not None:
        tile_source_command.extend(["-simplify", str(tile_simplify)])
    tile_source_command.extend([str(tippecanoe_input_path), str(source)])

    tippecanoe_command = [
        tippecanoe_bin,
        "-f",
        "-q",
        "--projection=EPSG:4326",
        "--minimum-zoom",
        str(minzoom),
        "--maximum-zoom",
        str(maxzoom),
        "-o",
        str(pmtiles_path),
        "-l",
        layer,
        "-n",
        dataset_title,
        "-N",
        dataset_description,
    ]
    tippecanoe_command.extend(effective_tippecanoe_args)
    tippecanoe_command.append(str(tippecanoe_input_path))

    mbtiles_command = [
        ogr2ogr_bin,
        "-f",
        "MBTiles",
        "-nln",
        layer,
        "-nlt",
        "PROMOTE_TO_MULTI",
        "-dsco",
        f"NAME={dataset_title}",
        "-dsco",
        f"DESCRIPTION={dataset_description}",
        "-dsco",
        f"MINZOOM={minzoom}",
        "-dsco",
        f"MAXZOOM={maxzoom}",
        "-lco",
        f"NAME={layer}",
        "-lco",
        f"MINZOOM={minzoom}",
        "-lco",
        f"MAXZOOM={maxzoom}",
    ]
    if tile_simplify is not None:
        mbtiles_command.extend(["-simplify", str(tile_simplify)])
    mbtiles_command.extend([str(mbtiles_path), str(source)])
    if source_layer:
        fgb_command.append(source_layer)
        tile_source_command.append(source_layer)
        mbtiles_command.append(source_layer)
    convert_command = [pmtiles_bin, "convert", str(mbtiles_path), str(pmtiles_path)]
    pmtiles_commands = [tile_source_command, tippecanoe_command]
    if pmtiles_engine == "gdal-mbtiles":
        pmtiles_commands = [mbtiles_command, convert_command]

    return VectorBuildPlan(
        source=str(source),
        asset_slug=asset_slug,
        layer_name=layer,
        work_dir=str(work),
        output_dir=str(output),
        fgb_path=str(fgb_path),
        tippecanoe_input_path=str(tippecanoe_input_path),
        mbtiles_path=str(mbtiles_path),
        pmtiles_path=str(pmtiles_path),
        pmtiles_engine=pmtiles_engine,
        pmtiles_bin=pmtiles_bin,
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
        maxzoom=maxzoom,
        tile_simplify=tile_simplify,
        tippecanoe_extra_args=effective_tippecanoe_args,
        title=dataset_title,
        description=dataset_description,
        commands=[fgb_command, *pmtiles_commands],
    )


def run_command(command: Sequence[str]) -> None:
    subprocess.run(command, check=True)


def remove_existing_outputs(plan: VectorBuildPlan, *, keep_mbtiles: bool) -> None:
    paths = [Path(plan.fgb_path), Path(plan.pmtiles_path), Path(plan.tippecanoe_input_path)]
    if not keep_mbtiles:
        paths.append(Path(plan.mbtiles_path))
    for path in paths:
        if path.exists():
            path.unlink()


def run_build(plan: VectorBuildPlan, *, overwrite: bool = False, keep_mbtiles: bool = False) -> VectorValidationResult:
    for executable in {command[0] for command in plan.commands}:
        require_executable(executable)

    Path(plan.output_dir).mkdir(parents=True, exist_ok=True)
    Path(plan.mbtiles_path).parent.mkdir(parents=True, exist_ok=True)

    outputs = [Path(plan.fgb_path), Path(plan.pmtiles_path)]
    if any(path.exists() for path in outputs) and not overwrite:
        existing = ", ".join(str(path) for path in outputs if path.exists())
        raise FileExistsError(f"Refusing to overwrite existing generated output(s): {existing}")
    if overwrite:
        remove_existing_outputs(plan, keep_mbtiles=keep_mbtiles)

    for command in plan.commands:
        run_command(command)

    if not keep_mbtiles:
        for intermediate in (Path(plan.mbtiles_path), Path(plan.tippecanoe_input_path)):
            if intermediate.exists():
                intermediate.unlink()
    return validate_outputs(Path(plan.fgb_path), Path(plan.pmtiles_path), pmtiles_bin=plan.pmtiles_bin)


def validate_outputs(fgb_path: Path, pmtiles_path: Path, *, pmtiles_bin: str = "pmtiles") -> VectorValidationResult:
    errors: list[str] = []
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

    decode_summary = decoded_pmtiles_property_summary(pmtiles_path)
    if decode_summary is not None:
        feature_count, property_keys = decode_summary
        if feature_count > 0 and not property_keys:
            errors.append(
                "PMTiles features decode with no feature properties at z0/0/0. "
                "Do not publish geometry-only display tiles; preserve compact source properties "
                "or add a synthetic property such as source_layer for the catalog inspector."
            )

    return VectorValidationResult(
        fgb_path=str(fgb_path),
        pmtiles_path=str(pmtiles_path),
        valid=not errors,
        errors=tuple(errors),
    )


def decoded_pmtiles_property_summary(
    pmtiles_path: Path,
    *,
    decoder_bin: str = "tippecanoe-decode",
) -> tuple[int, tuple[str, ...]] | None:
    """Return decoded z0 feature count and property keys when tippecanoe-decode is available."""
    if not pmtiles_path.exists() or not shutil.which(decoder_bin):
        return None

    completed = subprocess.run(
        [decoder_bin, str(pmtiles_path), "0", "0", "0"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        return None

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None

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

    return feature_count, tuple(sorted(property_keys))


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
        tile_simplify=args.tile_simplify,
        title=args.title,
        description=args.description,
        ogr2ogr_bin=args.ogr2ogr_bin,
        tippecanoe_bin=args.tippecanoe_bin,
        pmtiles_bin=args.pmtiles_bin,
        pmtiles_engine=args.pmtiles_engine,
        tippecanoe_extra_args=args.tippecanoe_arg,
        allow_repo_output=args.allow_repo_output,
    )
    if args.dry_run:
        print(json.dumps(asdict(plan), indent=2, sort_keys=True))
        return 0
    result = run_build(plan, overwrite=args.overwrite, keep_mbtiles=args.keep_mbtiles)
    print(json.dumps({"plan": asdict(plan), "validation": asdict(result)}, indent=2, sort_keys=True))
    return 0 if result.valid else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    result = validate_outputs(Path(args.fgb), Path(args.pmtiles), pmtiles_bin=args.pmtiles_bin)
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.valid else 1


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
    build_parser.add_argument("--maxzoom", type=int, default=8)
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
        "--pmtiles-engine",
        choices=["tippecanoe", "gdal-mbtiles"],
        default="tippecanoe",
        help="Prefer Tippecanoe direct PMTiles; use gdal-mbtiles only as an explicit fallback.",
    )
    build_parser.add_argument("--overwrite", action="store_true", help="Replace existing generated local outputs.")
    build_parser.add_argument("--keep-mbtiles", action="store_true", help="Keep the intermediate MBTiles file.")
    build_parser.add_argument("--allow-repo-output", action="store_true", help="Allow generated outputs under repo root.")
    build_parser.add_argument("--dry-run", action="store_true", help="Print the plan without running GDAL or PMTiles.")
    build_parser.set_defaults(func=_cmd_build)

    validate_parser = subparsers.add_parser("validate", help="Validate existing local FGB and PMTiles artifacts.")
    validate_parser.add_argument("--fgb", required=True)
    validate_parser.add_argument("--pmtiles", required=True)
    validate_parser.add_argument("--pmtiles-bin", default="pmtiles")
    validate_parser.set_defaults(func=_cmd_validate)

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
