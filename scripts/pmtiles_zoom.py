"""PMTiles maxzoom recommendation helpers.

The vector build flow uses this module after the canonical FGB has been
generated. The profiler samples geometries through GDAL/OGR GeoJSONSeq output
so large FlatGeobuf assets do not need to be loaded fully into Python memory.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


WEB_MERCATOR_EQUATOR_M_PER_PIXEL = 156543.0339
WEB_MERCATOR_SCALE_DENOMINATOR_Z0 = 559082264.0
DEFAULT_MAXZOOM_CAP = 12
PMTILES_MIN_AUTO_MAXZOOM = 6
PMTILES_LOW_MAXZOOM_THRESHOLD = 8
DETAIL_HINTS = {"coarse", "medium", "detailed"}
PROFILE_SAMPLE_FEATURES = 5_000
PROFILE_SAMPLE_SEGMENTS = 20_000


@dataclass(frozen=True)
class FgbProfile:
    path: str
    feature_count: int
    geometry_types: tuple[str, ...]
    bounds: tuple[float, float, float, float] | None
    point_feature_count: int
    sampled_feature_count: int
    sampled_segment_count: int
    segment_length_m_p25: float | None
    segment_length_m_p50: float | None
    feature_min_dimension_m_p10: float | None
    feature_min_dimension_m_p25: float | None
    envelope_like: bool
    property_keys: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ZoomRecommendation:
    status: str
    maxzoom: int | None
    confidence: str
    reason: str
    evidence: dict[str, Any]


def clamp_zoom(value: int, *, lower: int = PMTILES_MIN_AUTO_MAXZOOM, upper: int = DEFAULT_MAXZOOM_CAP) -> int:
    return max(lower, min(upper, value))


def zoom_for_resolution_meters(resolution_meters: float, *, cap: int = DEFAULT_MAXZOOM_CAP) -> int:
    if resolution_meters <= 0:
        raise ValueError("source resolution must be positive")
    zoom = math.ceil(math.log2(WEB_MERCATOR_EQUATOR_M_PER_PIXEL / (resolution_meters / 4)))
    return clamp_zoom(zoom, upper=cap)


def zoom_for_scale_denominator(scale_denominator: float, *, cap: int = DEFAULT_MAXZOOM_CAP) -> int:
    if scale_denominator <= 0:
        raise ValueError("source scale denominator must be positive")
    zoom = math.ceil(math.log2(WEB_MERCATOR_SCALE_DENOMINATOR_Z0 / scale_denominator)) + 2
    return clamp_zoom(zoom, upper=cap)


def zoom_for_detail_meters(detail_meters: float, *, cap: int = DEFAULT_MAXZOOM_CAP) -> int:
    if detail_meters <= 0:
        raise ValueError("geometry detail measurement must be positive")
    zoom = math.ceil(math.log2(WEB_MERCATOR_EQUATOR_M_PER_PIXEL / (detail_meters / 4)))
    return clamp_zoom(zoom, upper=cap)


def validate_detail_hint(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    normalized = value.strip().lower()
    if normalized not in DETAIL_HINTS:
        raise ValueError("pmtiles detail hint must be one of coarse, medium, or detailed")
    return normalized


def profile_fgb(
    path: Path,
    *,
    ogr2ogr_bin: str = "ogr2ogr",
    max_features: int = PROFILE_SAMPLE_FEATURES,
    max_segments: int = PROFILE_SAMPLE_SEGMENTS,
) -> FgbProfile:
    """Return sampled display-detail metrics for a local FlatGeobuf file."""
    if not path.exists():
        raise FileNotFoundError(f"FGB file does not exist: {path}")

    command = [
        ogr2ogr_bin,
        "-f",
        "GeoJSONSeq",
        "-t_srs",
        "EPSG:4326",
        "/vsistdout/",
        str(path),
    ]
    completed = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert completed.stdout is not None

    errors: list[str] = []

    def iter_features() -> Iterator[dict[str, Any]]:
        for line in completed.stdout or []:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                errors.append(f"ogr2ogr GeoJSONSeq line was not valid JSON: {exc}")
                continue
            if payload.get("type") == "Feature":
                yield payload

    profile = profile_geojson_features(
        iter_features(),
        path=str(path),
        max_features=max_features,
        max_segments=max_segments,
        extra_errors=errors,
    )
    _, stderr = completed.communicate()
    if completed.returncode != 0:
        raise RuntimeError(f"ogr2ogr failed while profiling {path}: {stderr.strip()}")
    return profile


def profile_geojson_features(
    features: Iterable[dict[str, Any]],
    *,
    path: str = "",
    max_features: int = PROFILE_SAMPLE_FEATURES,
    max_segments: int = PROFILE_SAMPLE_SEGMENTS,
    extra_errors: list[str] | None = None,
) -> FgbProfile:
    geometry_types: set[str] = set()
    property_keys: set[str] = set()
    point_feature_count = 0
    feature_count = 0
    sampled_feature_count = 0
    segment_lengths: list[float] = []
    feature_min_dimensions: list[float] = []
    bounds: list[float] | None = None
    errors = extra_errors or []

    for feature in features:
        feature_count += 1
        properties = feature.get("properties")
        if isinstance(properties, dict):
            property_keys.update(str(key) for key in properties.keys())
        geometry = feature.get("geometry") or {}
        geometry_type = str(geometry.get("type") or "")
        if geometry_type:
            geometry_types.add(geometry_type)
        if geometry_type in {"Point", "MultiPoint"}:
            point_feature_count += 1

        positions = list(iter_positions(geometry))
        if positions:
            bounds = merge_bounds(bounds, positions_bounds(positions))

        if sampled_feature_count < max_features:
            sampled_feature_count += 1
            if positions:
                minx, miny, maxx, maxy = positions_bounds(positions)
                mid_lat = (miny + maxy) / 2
                width_m = lon_distance_m(maxx - minx, mid_lat)
                height_m = lat_distance_m(maxy - miny)
                nonzero_dimensions = [value for value in (width_m, height_m) if value > 0]
                if nonzero_dimensions:
                    feature_min_dimensions.append(min(nonzero_dimensions))
            for line in iter_lines(geometry):
                for start, end in zip(line, line[1:]):
                    if len(segment_lengths) >= max_segments:
                        break
                    segment = position_distance_m(start, end)
                    if segment > 0:
                        segment_lengths.append(segment)
                if len(segment_lengths) >= max_segments:
                    break

    segment_p25 = quantile(segment_lengths, 0.25)
    segment_p50 = quantile(segment_lengths, 0.50)
    dimension_p10 = quantile(feature_min_dimensions, 0.10)
    dimension_p25 = quantile(feature_min_dimensions, 0.25)
    envelope_like = (
        feature_count > 0
        and point_feature_count == 0
        and feature_count <= 2
        and segment_p25 is not None
        and segment_p25 >= 100_000
    )

    return FgbProfile(
        path=path,
        feature_count=feature_count,
        geometry_types=tuple(sorted(geometry_types)),
        property_keys=tuple(sorted(property_keys)),
        bounds=tuple(bounds) if bounds else None,
        point_feature_count=point_feature_count,
        sampled_feature_count=sampled_feature_count,
        sampled_segment_count=len(segment_lengths),
        segment_length_m_p25=segment_p25,
        segment_length_m_p50=segment_p50,
        feature_min_dimension_m_p10=dimension_p10,
        feature_min_dimension_m_p25=dimension_p25,
        envelope_like=envelope_like,
        errors=tuple(errors),
    )


def recommend_maxzoom(
    profile: FgbProfile | None,
    *,
    source_resolution_meters: float | None = None,
    source_scale_denominator: float | None = None,
    pmtiles_maxzoom: int | None = None,
    pmtiles_maxzoom_reason: str | None = None,
    pmtiles_detail_hint: str | None = None,
    cap: int = DEFAULT_MAXZOOM_CAP,
) -> ZoomRecommendation:
    hint = validate_detail_hint(pmtiles_detail_hint)
    if pmtiles_maxzoom is not None:
        if not pmtiles_maxzoom_reason:
            raise ValueError("pmtiles_maxzoom requires pmtiles_maxzoom_reason")
        return ZoomRecommendation(
            status="recommended",
            maxzoom=pmtiles_maxzoom,
            confidence="high",
            reason=pmtiles_maxzoom_reason,
            evidence={"source": "explicit_pmtiles_maxzoom", "pmtiles_maxzoom": pmtiles_maxzoom},
        )

    if source_resolution_meters is not None:
        zoom = zoom_for_resolution_meters(source_resolution_meters, cap=cap)
        return ZoomRecommendation(
            status="recommended",
            maxzoom=zoom,
            confidence="high",
            reason=f"source resolution {source_resolution_meters:g} meters maps to zoom {zoom}",
            evidence={"source": "source_resolution_meters", "source_resolution_meters": source_resolution_meters},
        )

    if source_scale_denominator is not None:
        zoom = zoom_for_scale_denominator(source_scale_denominator, cap=cap)
        return ZoomRecommendation(
            status="recommended",
            maxzoom=zoom,
            confidence="high",
            reason=f"source scale 1:{source_scale_denominator:g} maps to zoom {zoom}",
            evidence={"source": "source_scale_denominator", "source_scale_denominator": source_scale_denominator},
        )

    if hint:
        zoom = {"coarse": 6, "medium": 10, "detailed": cap}[hint]
        return ZoomRecommendation(
            status="recommended",
            maxzoom=zoom,
            confidence="high",
            reason=f"pmtiles_detail_hint={hint} maps to zoom {zoom}",
            evidence={"source": "pmtiles_detail_hint", "pmtiles_detail_hint": hint},
        )

    if profile is None:
        return ZoomRecommendation(
            status="needs_input",
            maxzoom=None,
            confidence="none",
            reason="no source hints or FGB profile are available",
            evidence={},
        )

    if profile.feature_count <= 0:
        return ZoomRecommendation(
            status="needs_input",
            maxzoom=None,
            confidence="none",
            reason="FGB profile has no features",
            evidence={"feature_count": profile.feature_count},
        )

    if profile.point_feature_count > 0:
        point_scope = "point-only" if profile.point_feature_count == profile.feature_count else "mixed point/vector"
        return ZoomRecommendation(
            status="recommended",
            maxzoom=cap,
            confidence="high",
            reason=f"{point_scope} profile uses detailed cap zoom {cap} with all-point retention",
            evidence={
                "source": "fgb_profile_points",
                "feature_count": profile.feature_count,
                "point_feature_count": profile.point_feature_count,
                "geometry_types": profile.geometry_types,
            },
        )

    if profile.envelope_like:
        return ZoomRecommendation(
            status="recommended",
            maxzoom=6,
            confidence="medium",
            reason="FGB profile is envelope-like and coarse",
            evidence={
                "source": "fgb_profile_envelope",
                "feature_count": profile.feature_count,
                "segment_length_m_p25": profile.segment_length_m_p25,
                "geometry_types": profile.geometry_types,
            },
        )

    detail_candidates = [
        value
        for value in (
            profile.segment_length_m_p25,
            profile.feature_min_dimension_m_p10,
        )
        if value is not None and value > 0
    ]
    if detail_candidates:
        zooms = [zoom_for_detail_meters(value, cap=cap) for value in detail_candidates]
        zoom = max(zooms)
        return ZoomRecommendation(
            status="recommended",
            maxzoom=zoom,
            confidence="medium",
            reason=f"FGB sampled geometry detail maps to zoom {zoom}",
            evidence={
                "source": "fgb_profile_geometry_detail",
                "feature_count": profile.feature_count,
                "geometry_types": profile.geometry_types,
                "segment_length_m_p25": profile.segment_length_m_p25,
                "feature_min_dimension_m_p10": profile.feature_min_dimension_m_p10,
                "candidate_zooms": zooms,
            },
        )

    return ZoomRecommendation(
        status="needs_input",
        maxzoom=None,
        confidence="none",
        reason="FGB profile did not contain usable point, segment, or feature-size evidence",
        evidence={
            "feature_count": profile.feature_count,
            "geometry_types": profile.geometry_types,
            "sampled_feature_count": profile.sampled_feature_count,
            "sampled_segment_count": profile.sampled_segment_count,
        },
    )


def profile_payload(profile: FgbProfile, recommendation: ZoomRecommendation) -> dict[str, Any]:
    return {
        "profile": asdict(profile),
        "recommendation": asdict(recommendation),
    }


def quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def iter_positions(geometry: dict[str, Any]) -> Iterator[tuple[float, float]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        yield position2(coordinates)
    elif geometry_type in {"MultiPoint", "LineString"}:
        for position in coordinates or []:
            yield position2(position)
    elif geometry_type in {"MultiLineString", "Polygon"}:
        for line in coordinates or []:
            for position in line or []:
                yield position2(position)
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates or []:
            for ring in polygon or []:
                for position in ring or []:
                    yield position2(position)
    elif geometry_type == "GeometryCollection":
        for child in geometry.get("geometries") or []:
            yield from iter_positions(child)


def iter_lines(geometry: dict[str, Any]) -> Iterator[list[tuple[float, float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "LineString":
        yield [position2(position) for position in coordinates or []]
    elif geometry_type == "MultiLineString":
        for line in coordinates or []:
            yield [position2(position) for position in line or []]
    elif geometry_type == "Polygon":
        for ring in coordinates or []:
            yield [position2(position) for position in ring or []]
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates or []:
            for ring in polygon or []:
                yield [position2(position) for position in ring or []]
    elif geometry_type == "GeometryCollection":
        for child in geometry.get("geometries") or []:
            yield from iter_lines(child)


def position2(value: Any) -> tuple[float, float]:
    return float(value[0]), float(value[1])


def positions_bounds(positions: Sequence[tuple[float, float]]) -> list[float]:
    xs = [position[0] for position in positions]
    ys = [position[1] for position in positions]
    return [min(xs), min(ys), max(xs), max(ys)]


def merge_bounds(current: list[float] | None, new: Sequence[float]) -> list[float]:
    if current is None:
        return [float(new[0]), float(new[1]), float(new[2]), float(new[3])]
    return [
        min(current[0], float(new[0])),
        min(current[1], float(new[1])),
        max(current[2], float(new[2])),
        max(current[3], float(new[3])),
    ]


def position_distance_m(start: tuple[float, float], end: tuple[float, float]) -> float:
    mid_lat = (start[1] + end[1]) / 2
    dx = lon_distance_m(end[0] - start[0], mid_lat)
    dy = lat_distance_m(end[1] - start[1])
    return math.hypot(dx, dy)


def lon_distance_m(delta_lon: float, latitude: float) -> float:
    return abs(delta_lon) * 111_320 * max(0.0, math.cos(math.radians(latitude)))


def lat_distance_m(delta_lat: float) -> float:
    return abs(delta_lat) * 110_574
