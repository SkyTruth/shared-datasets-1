"""One normalized binary geometry spool; source JSON never becomes a big file."""

from __future__ import annotations

import json
import re
import sqlite3
from itertools import zip_longest
from pathlib import Path

from ingestion.common import feature_metadata
from ingestion.common.runtime import sha256_file
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
        self.input_open = True
        self.attributes = None

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

    def finish_input(self):
        """Persist normalization before retaining it for a later decision pass."""
        self.layer.CommitTransaction()
        self.ds.FlushCache()
        self.witness.close()

        self.layer = None
        self.ds = None
        self.input_open = False
        self.path.with_suffix(".sha256").write_text(sha256_file(self.path))

    @classmethod
    def open(cls, path: Path, *, layer_name: str):
        from osgeo import gdal, ogr

        store = cls.__new__(cls)
        store.ogr, store.gdal = ogr, gdal
        store.path, store.layer_name = path, layer_name
        store.ds = ogr.Open(str(path), update=False)
        if store.ds is None:
            raise RuntimeError("retained normalized geometry cannot be opened")
        store.layer = store.ds.GetLayerByName(layer_name)
        if store.layer is None:
            raise RuntimeError("normalized spool is missing the expected layer")
        store.witness_path = path.with_suffix(".schema.geojsonseq")
        store.witness = store.witness_path.open("r", encoding="utf-8")
        store.field_names = ()
        store.input_open = False
        store.attributes = None
        return store

    @staticmethod
    def _source_feature(row):
        geometry = row.GetGeometryRef()
        return {"properties": json.loads(row.GetField("_sd_properties")),
                "geometry": json.loads(geometry.ExportToJson()) if geometry is not None else None}

    def source_feature(self, ordinal):
        row = self.layer.GetFeature(ordinal)
        if row is None:
            raise RuntimeError(f"normalized source ordinal {ordinal} is missing")
        return self._source_feature(row)

    def prepare_fields(self, attributes_path=None):
        if self.input_open:
            self.finish_input()
        self.witness.close()
        self.layer = None
        self.ds = None
        if sha256_file(self.path) != self.path.with_suffix(".sha256").read_text():
            raise RuntimeError("normalized spool content changed before metadata generation")
        self.attributes_path = attributes_path or self.path.with_suffix(".attributes.gpkg")
        self.attributes = self.ogr.GetDriverByName("GPKG").CreateDataSource(str(self.attributes_path))
        self.attribute_layer = self.attributes.CreateLayer("_sd_attributes", geom_type=self.ogr.wkbNone)
        self.attribute_layer.CreateField(self.ogr.FieldDefn("_sd_ordinal", self.ogr.OFTInteger64))
        witness = self.gdal.OpenEx(
            "GeoJSONSeq:" + str(self.witness_path), self.gdal.OF_VECTOR
        )
        definition = witness.GetLayer(0).GetLayerDefn()
        names = []
        for index in range(definition.GetFieldCount()):
            field = definition.GetFieldDefn(index)
            clone = self.ogr.FieldDefn(field.GetName(), field.GetType())
            clone.SetSubType(field.GetSubType())
            self.attribute_layer.CreateField(clone)
            names.append(field.GetName())
        for name in model.REQUIRED_VECTOR_FGB_PROPERTIES:
            if name in names:
                raise RuntimeError(
                    f"source property conflicts with generated column {name}"
                )
            self.attribute_layer.CreateField(self.ogr.FieldDefn(name, self.ogr.OFTString))
            names.append(name)
        self.field_names = tuple(names)
        witness = None

    def sidecar_records(self, *, plan, asset_slug, release, provenance):
        self.attribute_layer.StartTransaction()
        count = 0
        source_db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        source_db.execute("PRAGMA cache_size=-65536")
        source_db.execute("PRAGMA mmap_size=0")
        table = self.layer_name.replace('"', '""')
        rows = source_db.execute(f'SELECT "_sd_ordinal","_sd_properties" FROM "{table}" ORDER BY "_sd_fid"')
        try:
            for source, planned in zip_longest(rows, plan.records()):
                if source is None or planned is None or source[0] != planned[0]:
                    raise RuntimeError("normalized spool and completed allocation ordinals differ")
                ordinal, properties_json = source
                allocation = planned[1]
                properties = json.loads(properties_json)
                row = self.ogr.Feature(self.attribute_layer.GetLayerDefn())
                row.SetField("_sd_ordinal", ordinal)
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
                self.attribute_layer.CreateFeature(row)
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
                    self.attribute_layer.CommitTransaction()
                    self.attribute_layer.StartTransaction()
        finally:
            source_db.close()
            self.attribute_layer.CommitTransaction()
            self.attribute_layer = None
            self.attributes = None

    def projection_sql(self):
        def quoted(name):
            return '"' + name.replace('"', '""') + '"'

        self.layer = None
        self.ds = None
        with sqlite3.connect(self.path) as db:
            db.execute("ATTACH DATABASE ? AS attributes", (str(self.attributes_path),))
            sql = db.execute("SELECT sql FROM attributes.sqlite_master WHERE type='table' AND name='_sd_attributes'").fetchone()[0]
            db.execute(sql)
            db.execute('INSERT INTO "_sd_attributes" SELECT * FROM attributes."_sd_attributes"')
            db.execute('CREATE UNIQUE INDEX "_sd_attribute_ordinals" ON "_sd_attributes" ("_sd_ordinal")')
        return (
            "SELECT "
            + ",".join("a." + quoted(name) for name in self.field_names)
            + ',g."_sd_geometry" FROM ' + quoted(self.layer_name)
            + ' g JOIN "_sd_attributes" a ON g."_sd_fid"=a."_sd_ordinal" ORDER BY g."_sd_fid"'
        )

    def tile_sql(self):
        table = self.layer_name.replace('"', '""')
        return (f'SELECT a."feature_id",g."_sd_geometry" FROM "{table}" g '
            'JOIN "_sd_attributes" a ON g."_sd_fid"=a."_sd_ordinal" ORDER BY g."_sd_fid"')

    def close(self):
        if not self.witness.closed:
            self.witness.close()
        self.layer = None
        self.ds = None
        self.attribute_layer = None
        self.attributes = None
