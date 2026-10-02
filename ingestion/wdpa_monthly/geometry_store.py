"""One normalized binary geometry spool; source JSON never becomes a big file."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ingestion.common import feature_metadata
from scripts import release_feature_model as model


def value_kind(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int32" if -(2**31) <= value < 2**31 else "int64"
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return "date"
        if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            return "datetime"
        return "string"
    if isinstance(value, list):
        return "list:" + ",".join(sorted({value_kind(item) for item in value}))
    return type(value).__name__


class NormalizedGeometryStore:
    """Geometry is normalized by the existing GDAL exporter before insertion.

    A small properties-only witness lets that same native GeoJSON reader infer
    field types, including widening integers and date strings. The spool's
    internal JSON column preserves the exact source property values and order.
    """

    def __init__(self, path: Path, *, layer_name: str):
        from osgeo import gdal, ogr, osr

        gdal.UseExceptions()
        ogr.UseExceptions()
        self.ogr = ogr
        self.gdal = gdal
        gdal.SetConfigOption("OGR_SQLITE_CACHE", "64")
        gdal.SetConfigOption("OGR_SQLITE_JOURNAL", "OFF")
        gdal.SetConfigOption("OGR_SQLITE_SYNCHRONOUS", "OFF")
        self.path, self.layer_name = path, layer_name
        self.ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(4326)
        self.layer = self.ds.CreateLayer(
            layer_name,
            reference,
            ogr.wkbUnknown,
            options=["SPATIAL_INDEX=NO", "FID=_sd_fid", "GEOMETRY_NAME=_sd_geometry"],
        )
        self.layer.CreateField(ogr.FieldDefn("_sd_ordinal", ogr.OFTInteger64))
        self.layer.CreateField(ogr.FieldDefn("_sd_properties", ogr.OFTString))
        self.witness_path = path.with_suffix(".schema.geojsonseq")
        self.witness = self.witness_path.open("w", encoding="utf-8")
        self.observed = {}
        self.field_names = ()
        self.layer.StartTransaction()
        self.inserted = 0

    def add(self, feature, ordinal):
        properties = feature.get("properties") or {}
        if any(name.startswith("_sd_") for name in properties):
            raise RuntimeError(
                "source properties conflict with reserved geometry-spool columns"
            )
        changed = False
        for name, value in properties.items():
            kind = value_kind(value)
            seen = self.observed.setdefault(name, set())
            if kind not in seen:
                seen.add(kind)
                changed = True
        if changed:
            self.witness.write(
                model.canonical_json(
                    {"type": "Feature", "geometry": None, "properties": properties}
                )
                + "\n"
            )
        row = self.ogr.Feature(self.layer.GetLayerDefn())
        row.SetFID(ordinal)
        row.SetField("_sd_ordinal", ordinal)
        row.SetField(
            "_sd_properties",
            json.dumps(properties, ensure_ascii=False, separators=(",", ":")),
        )
        if feature.get("geometry") is not None:
            row.SetGeometry(
                self.ogr.CreateGeometryFromJson(json.dumps(feature["geometry"]))
            )
        self.layer.CreateFeature(row)
        self.inserted += 1
        if self.inserted % 10000 == 0:
            self.layer.CommitTransaction()
            self.layer.StartTransaction()

    def prepare_fields(self):
        self.layer.CommitTransaction()
        self.witness.close()
        witness = self.gdal.OpenEx(
            "GeoJSONSeq:" + str(self.witness_path), self.gdal.OF_VECTOR
        )
        definition = witness.GetLayer(0).GetLayerDefn()
        names = []
        for index in range(definition.GetFieldCount()):
            field = definition.GetFieldDefn(index)
            clone = self.ogr.FieldDefn(field.GetName(), field.GetType())
            clone.SetSubType(field.GetSubType())
            self.layer.CreateField(clone)
            names.append(field.GetName())
        for name in model.REQUIRED_VECTOR_FGB_PROPERTIES:
            if name in names:
                raise RuntimeError(
                    f"source property conflicts with generated column {name}"
                )
            self.layer.CreateField(self.ogr.FieldDefn(name, self.ogr.OFTString))
            names.append(name)
        self.field_names = tuple(names)
        witness = None
        self.witness_path.unlink()

    def sidecar_records(self, *, plan, asset_slug, release, provenance):
        self.layer.ResetReading()
        self.layer.StartTransaction()
        count = 0
        try:
            for row in self.layer:
                ordinal = row.GetField("_sd_ordinal")
                allocation = plan.record(ordinal)
                properties = json.loads(row.GetField("_sd_properties"))
                geometry = row.GetGeometryRef()
                geometry_json = (
                    json.loads(geometry.ExportToJson())
                    if geometry is not None
                    else None
                )
                hashes = model.content_hashes(
                    geometry=geometry_json, properties=properties
                )
                if hashes != (
                    allocation["geometry_hash"],
                    allocation["properties_hash"],
                ):
                    raise RuntimeError(
                        f"normalized spool content changed at source ordinal {ordinal}"
                    )
                for name, value in properties.items():
                    if value is None:
                        row.SetFieldNull(name)
                    elif isinstance(value, (dict, list)):
                        field = row.GetFieldDefnRef(row.GetFieldIndex(name))
                        if field.GetType() == self.ogr.OFTString:
                            row.SetField(name, model.canonical_json(value))
                        elif field.GetType() == self.ogr.OFTIntegerList:
                            row.SetFieldIntegerList(row.GetFieldIndex(name), value)
                        elif field.GetType() == self.ogr.OFTInteger64List:
                            row.SetFieldInteger64List(row.GetFieldIndex(name), value)
                        elif field.GetType() == self.ogr.OFTRealList:
                            row.SetFieldDoubleList(row.GetFieldIndex(name), value)
                        else:
                            row.SetFieldStringList(row.GetFieldIndex(name), value)
                    else:
                        row.SetField(name, value)
                for name in model.REQUIRED_VECTOR_FGB_PROPERTIES:
                    row.SetField(name, allocation[name])
                self.layer.SetFeature(row)
                source_provenance = {
                    **provenance,
                    "source_row_number": ordinal,
                    "identity_key": list(allocation["identity_key"]),
                }
                if allocation["duplicate_source_row_numbers"]:
                    source_provenance["duplicate_source_row_numbers"] = allocation[
                        "duplicate_source_row_numbers"
                    ]
                yield feature_metadata.sidecar_record(
                    asset_slug=asset_slug,
                    release=release,
                    feature_id=allocation["feature_id"],
                    geometry_hash=allocation["geometry_hash"],
                    properties_hash=allocation["properties_hash"],
                    properties=properties,
                    provenance=source_provenance,
                    identity_key=allocation["identity_key"],
                )
                count += 1
                if count % 10000 == 0:
                    self.layer.CommitTransaction()
                    self.layer.StartTransaction()
        finally:
            self.layer.CommitTransaction()
            self.ds.FlushCache()

    def projection_sql(self):
        def quoted(name):
            return '"' + name.replace('"', '""') + '"'

        return (
            "SELECT "
            + ",".join(quoted(name) for name in (*self.field_names, "_sd_geometry"))
            + " FROM "
            + quoted(self.layer_name)
            + ' ORDER BY "_sd_ordinal"'
        )

    def close(self):
        if not self.witness.closed:
            self.witness.close()
        self.layer = None
        self.ds = None
