#!/usr/bin/env python3
"""Reattach verified translations using source identity and exact source text.

SQLite is a disposable local index, not a new canonical dataset format. CSV and
sidecars are streamed; numeric feature IDs never establish cross-release identity.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import csv
from functools import lru_cache
import gzip
import io
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import feature_metadata_localization as localization, release_feature_model as model, translation_local_io as local_io

COLUMNS = (*localization.REQUIRED_TRANSLATION_COLUMNS, *localization.OPTIONAL_TRANSLATION_COLUMNS)


class TranslationReuseError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TranslationReuseError(message)


def source_key(properties: dict[str, Any], fields: Sequence[str]) -> str:
    values = [properties.get(field) for field in fields]
    require(all(isinstance(value, str) and bool(value.strip()) for value in values), "source identity fields must be nonempty strings")
    # Preserve exact source values: normalizing case or coercing numeric types
    # could silently conflate distinct provider identities.
    return model.canonical_json(values)


def validate_sidecar(path: Path, slug: str, release: str) -> int:
    result = localization.validate_sidecar(path, expected_asset_slug=slug, expected_release=release)
    require(result.valid, "invalid canonical sidecar: " + "; ".join(result.errors))
    return result.feature_count


def build_memory(*, database: Path, sources: Sequence[dict[str, Any]], fields: Sequence[str], locales: Sequence[str], source_key_fields: Sequence[str]) -> dict[str, Any]:
    fields = tuple(fields)
    locales = tuple(localization.parse_locale_arguments(locales))
    require(bool(fields) and len(set(fields)) == len(fields) and bool(locales) and bool(source_key_fields), "explicit unique fields, locales, and source identity fields are required")
    require(len({source["asset_slug"] for source in sources}) == len(sources) and bool(sources), "use one verified source bundle per asset")
    config = {"schema_version": 1, "fields": fields, "locales": locales, "source_key_fields": tuple(source_key_fields)}
    slots = {(field, locale): i for i, (field, locale) in enumerate((f, loc) for f in fields for loc in locales)}
    inputs = [Path(source[key]) for source in sources for key in ("canonical_sidecar", "translation_source")]
    snapshots = local_io.snapshots(inputs)
    report = {**config, "sources": [], "input_sha256": {str(s.path): s.sha256 for s in snapshots}}
    with local_io.candidate_output(database, protected=inputs, expected=snapshots) as candidate:
        db = sqlite3.connect(candidate)
        try:
            db.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA cache_size=-65536;
                PRAGMA temp_store=FILE;
                PRAGMA mmap_size=0;
                CREATE TABLE config (value TEXT NOT NULL);
                CREATE TABLE source (id INTEGER PRIMARY KEY, asset TEXT UNIQUE NOT NULL, release TEXT NOT NULL);
                CREATE TABLE feature (id INTEGER PRIMARY KEY, source INTEGER NOT NULL, fid TEXT NOT NULL, source_key TEXT NOT NULL, properties TEXT NOT NULL,
                                      UNIQUE(source,fid), UNIQUE(source,source_key));
                CREATE TABLE phrase (id INTEGER PRIMARY KEY, slot INTEGER NOT NULL, hash TEXT NOT NULL, value TEXT NOT NULL, review TEXT NOT NULL, notes TEXT NOT NULL,
                                     UNIQUE(slot,hash,value,review,notes));
                CREATE TABLE translation (feature INTEGER NOT NULL, slot INTEGER NOT NULL, hash TEXT NOT NULL, phrase INTEGER NOT NULL, PRIMARY KEY(feature,slot,hash)) WITHOUT ROWID;
                CREATE TABLE dictionary (slot INTEGER, hash TEXT, phrase INTEGER, PRIMARY KEY(slot,hash)) WITHOUT ROWID;
            """)
            db.execute("INSERT INTO config VALUES (?)", (model.canonical_json(config),))

            @lru_cache(maxsize=65536)
            def phrase_id(slot, source_hash, value, review, notes):
                params = (slot, source_hash, value, review, notes)
                db.execute("INSERT OR IGNORE INTO phrase(slot,hash,value,review,notes) VALUES (?,?,?,?,?)", params)
                return db.execute("SELECT id FROM phrase WHERE slot=? AND hash=? AND value=? AND review=? AND notes=?", params).fetchone()[0]

            for source in sources:
                slug, release = source["asset_slug"], source["release"]
                canonical, translations = Path(source["canonical_sidecar"]), Path(source["translation_source"])
                count = validate_sidecar(canonical, slug, release)
                source_id = db.execute("INSERT INTO source(asset,release) VALUES (?,?)", (slug, release)).lastrowid
                for record in model.read_metadata_sidecar(canonical):
                    properties = record["properties"]
                    db.execute("INSERT INTO feature(source,fid,source_key,properties) VALUES (?,?,?,?)",
                               (source_id, record["feature_id"], source_key(properties, source_key_fields), model.canonical_json({field: properties[field] for field in fields if field in properties})))
                stats = Counter(feature_count=count)

                @lru_cache(maxsize=2048)
                def feature(fid):
                    found = db.execute("SELECT id,properties FROM feature WHERE source=? AND fid=?", (source_id, fid)).fetchone()
                    if found is None:
                        return None
                    props = json.loads(found[1])
                    return found[0], props, {field: localization.source_value_hash(value) for field, value in props.items()}

                pending = []
                for row in localization.iter_translation_source(translations):
                    stats["input_rows"] += 1
                    field, locale = row.field, row.locale
                    if (field, locale) not in slots:
                        stats["out_of_scope_rows"] += 1
                        continue
                    found = feature(row.feature_id)
                    if found is None:
                        stats["orphan_source_rows"] += 1
                        continue
                    fid, properties, hashes = found
                    if field not in properties:
                        stats["removed_field_rows"] += 1
                        continue
                    source_hash = row.source_value_hash
                    if hashes.get(field) != source_hash:
                        stats["stale_source_rows"] += 1
                    state, value, notes = row.review_state, row.value, row.notes
                    failed = localization.translation_failed(row, properties[field])
                    if failed:
                        stats["unconfirmed_or_failed_rows"] += 1
                        state, value = localization.FAILED_REVIEW_STATE, ""
                    else:
                        stats["accepted_rows"] += 1
                    slot = slots[field, locale]
                    phrase = phrase_id(slot, source_hash, value, state, notes)
                    pending.append((fid, slot, source_hash, phrase))
                    if not failed and hashes.get(field) == source_hash:
                        db.execute("""INSERT INTO dictionary VALUES (?,?,?) ON CONFLICT(slot,hash) DO UPDATE SET phrase=
                            CASE WHEN (SELECT value FROM phrase WHERE id=dictionary.phrase) =
                                      (SELECT value FROM phrase WHERE id=excluded.phrase)
                                 THEN dictionary.phrase ELSE NULL END""", (slot, source_hash, phrase))
                    if len(pending) == 10000:
                        db.executemany("INSERT INTO translation VALUES (?,?,?,?)", pending)
                        pending.clear()
                    if stats["input_rows"] % 1000000 == 0:
                        print(f"{slug}: indexed {stats['input_rows']:,} translation rows", file=sys.stderr, flush=True)
                db.executemany("INSERT INTO translation VALUES (?,?,?,?)", pending)
                feature.cache_clear()
                report["sources"].append({"asset_slug": slug, "release": release, **stats, "provenance": source.get("provenance", {})})
                db.commit()
            db.executescript("""
                CREATE TABLE report (value TEXT NOT NULL);
            """)
            report["unique_phrase_variants"] = db.execute("SELECT count(*) FROM phrase").fetchone()[0]
            report["ambiguous_text_keys"] = db.execute("SELECT count(*) FROM dictionary WHERE phrase IS NULL").fetchone()[0]
            report["unique_text_locale_field_keys"] = db.execute("SELECT count(*) FROM dictionary").fetchone()[0]
            db.execute("INSERT INTO report VALUES (?)", (model.canonical_json(report),))
            db.commit()
        except sqlite3.IntegrityError as exc:
            raise TranslationReuseError("duplicate source identity or current translation key") from exc
        except localization.FeatureMetadataLocalizationError as exc:
            raise TranslationReuseError(str(exc)) from exc
        finally:
            db.close()
    return report


def read_supplement(path: Path, slots: Sequence[tuple[str, str]]) -> dict[tuple[int, str], dict[str, str]]:
    """Compact, local gap results; original source hashes remain authoritative."""
    by_slot = {pair: index for index, pair in enumerate(slots)}
    result = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            pair = (row["field"], row["locale"])
            require(pair in by_slot, "supplement contains an unapproved field or locale")
            require(isinstance(row["source_value"], str) and row["source_value"].strip(), "supplement source must be nonempty text")
            require(localization.source_value_hash(row["source_value"]) == row["source_value_hash"], "supplement source hash differs")
            require(isinstance(row["review_state"], str) and isinstance(row["value"], str) and row["value"].strip(), "supplement must contain completed translations")
            require(isinstance(row["notes"], str) and not localization.translation_failed(row, row["source_value"]), "supplement contains a failed translation")
            key = (by_slot[pair], row["source_value_hash"])
            require(key not in result, "duplicate supplement translation key")
            result[key] = row
    return result


class TranslationMemory:
    def __init__(self, database: Path, *, supplement: Path | None = None):
        self.database = database
        self.db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        self.db.execute("PRAGMA cache_size=-65536")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.execute("PRAGMA mmap_size=0")
        self.config = json.loads(self.db.execute("SELECT value FROM config").fetchone()[0])
        self.source_report = json.loads(self.db.execute("SELECT value FROM report").fetchone()[0])  # finalized builds only
        self.fields, self.locales = self.config["fields"], self.config["locales"]
        self.slots = [(field, locale) for field in self.fields for locale in self.locales]
        self.supplement_snapshot = local_io.FileSnapshot.capture(supplement) if supplement else None
        self.supplement = read_supplement(supplement, self.slots) if supplement else {}
        if self.supplement_snapshot:
            self.supplement_snapshot.verify()

    def close(self):
        self.shared.cache_clear()
        self.db.close()

    @lru_cache(maxsize=65536)
    def shared(self, slot: int, source_hash: str):
        return self.db.execute("SELECT d.phrase,p.value,p.review,p.notes FROM dictionary d LEFT JOIN phrase p ON p.id=d.phrase WHERE d.slot=? AND d.hash=?", (slot, source_hash)).fetchone()

    def direct(self, asset_slug: str, key: str):
        grouped = {}
        for row in self.db.execute("""
            SELECT t.slot,p.hash,p.value,p.review,p.notes FROM source s JOIN feature f ON f.source=s.id
            JOIN translation t ON t.feature=f.id JOIN phrase p ON p.id=t.phrase WHERE s.asset=? AND f.source_key=?
        """, (asset_slug, key)):
            grouped.setdefault(row[0], []).append(row[1:])
        return grouped

    def rebuild(self, *, canonical_sidecar: Path, schema: Path, asset_slug: str, release: str, output_dir: Path) -> dict[str, Any]:
        require(not output_dir.exists(), "rebuild output directory must be new; preserve earlier reports and candidates")
        localization.resolved_translatable_fields(schema=schema, fields=self.fields)
        feature_count = validate_sidecar(canonical_sidecar, asset_slug, release)
        protected = [canonical_sidecar, schema, self.database]
        if self.supplement_snapshot:
            self.supplement_snapshot.verify()
            protected.append(self.supplement_snapshot.path)
        snapshots = local_io.snapshots(protected)
        output_dir.mkdir(parents=True)
        pending_db = sqlite3.connect(output_dir / "pending.sqlite")
        pending_db.executescript("PRAGMA cache_size=-65536; PRAGMA temp_store=FILE; PRAGMA mmap_size=0; CREATE TABLE target_sources (source_key TEXT PRIMARY KEY) WITHOUT ROWID;")
        pending_db.execute("CREATE TABLE pending (slot INTEGER, hash TEXT, source TEXT, reason TEXT, affected INTEGER, PRIMARY KEY(slot,hash,reason)) WITHOUT ROWID")
        counts = {locale: Counter() for locale in self.locales}
        coverage = {locale: localization.LocalizationReport(locale, str(canonical_sidecar), "", "", sorted(self.fields)) for locale in self.locales}
        translations_path = output_dir / f"{asset_slug}.metadata-translations.csv"
        locale_paths = {locale: output_dir / f"{asset_slug}.metadata.{locale}.ndjson.gz" for locale in self.locales}
        try:
            with ExitStack() as stack:
                csv_handle = stack.enter_context(translations_path.open("x", encoding="utf-8", newline=""))
                writer = csv.DictWriter(csv_handle, fieldnames=COLUMNS, lineterminator="\n")
                writer.writeheader()
                handles, debt_writers = {}, {}
                for locale in self.locales:
                    debt_path = output_dir / f"{asset_slug}.translation-debt.{locale}.csv"
                    coverage[locale].debt_path = str(debt_path)
                    handle = stack.enter_context(debt_path.open("x", encoding="utf-8", newline=""))
                    debt_writers[locale] = csv.DictWriter(handle, fieldnames=localization.DEBT_COLUMNS, lineterminator="\n")
                    debt_writers[locale].writeheader()
                for locale, path in locale_paths.items():
                    raw = stack.enter_context(path.open("xb"))
                    zipped = stack.enter_context(gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0))
                    handles[locale] = stack.enter_context(io.TextIOWrapper(zipped, encoding="utf-8", newline="\n"))
                for number, record in enumerate(model.read_metadata_sidecar(canonical_sidecar), 1):
                    properties = record["properties"]
                    key = source_key(properties, self.config["source_key_fields"])
                    try:
                        pending_db.execute("INSERT INTO target_sources VALUES (?)", (key,))
                    except sqlite3.IntegrityError as exc:
                        raise TranslationReuseError("target source identity is ambiguous") from exc
                    direct = self.direct(asset_slug, key)
                    hashes = {field: localization.source_value_hash(properties[field]) for field in self.fields if field in properties}
                    locale_rows = {locale: [] for locale in self.locales}
                    for slot, (field, locale) in enumerate(self.slots):
                        original = properties.get(field)
                        if not isinstance(original, str) or not original.strip():
                            counts[locale]["empty_or_missing_fields"] += 1
                            continue
                        source_hash = hashes[field]
                        previous = direct.get(slot, ())
                        exact = next((row for row in previous if row[0] == source_hash), None)
                        row_hash = source_hash
                        shared = self.shared(slot, source_hash)
                        if exact and exact[2] != localization.FAILED_REVIEW_STATE:
                            _old_hash, value, state, notes = exact
                            counts[locale]["same_feature_rows"] += 1
                        elif shared is not None and shared[0] is not None:
                            _phrase, value, state, notes = shared
                            counts[locale]["shared_text_rows"] += 1
                        elif fresh := self.supplement.get((slot, source_hash)):
                            # Only fill gaps. Never replace an established
                            # site-specific or unambiguous reused translation.
                            value, state, notes = fresh["value"], fresh["review_state"], fresh["notes"]
                            counts[locale]["supplement_rows"] += 1
                        else:
                            reason = "conflicting_translations" if shared else "missing_translation"
                            counts[locale][reason] += 1
                            pending_db.execute("INSERT INTO pending VALUES (?,?,?,?,1) ON CONFLICT(slot,hash,reason) DO UPDATE SET affected=affected+1", (slot, source_hash, model.canonical_json(original), reason))
                            # An explicit failed task is valid CSV but cannot be
                            # mistaken for a completed translation on the next run.
                            stale = [row for row in previous if row[2] != localization.FAILED_REVIEW_STATE]
                            if exact:
                                row_hash, value, state, notes = exact
                            elif stale:
                                row_hash, value, state, notes = sorted(stale)[0]
                            else:
                                value, state, notes = "", localization.FAILED_REVIEW_STATE, reason
                        row = {"feature_id": record["feature_id"], "field": field, "locale": locale, "source_value_hash": row_hash,
                               "value": value, "review_state": state, "notes": notes}
                        writer.writerow(row)
                        locale_rows[locale].append(localization.TranslationRow(row_number=0, **row))
                    for locale, handle in handles.items():
                        localized = localization.localize_record(record, rows=locale_rows[locale], report=coverage[locale], debt=debt_writers[locale].writerow)
                        handle.write(model.canonical_json(localized) + "\n")
                    if number % 50000 == 0:
                        print(f"{asset_slug}: rebuilt {number:,} features", file=sys.stderr, flush=True)
            pending_db.commit()
            pending_path = output_dir / "pending-translations.ndjson"
            with pending_path.open("x", encoding="utf-8") as handle:
                for slot, source_hash, original, reason, affected in pending_db.execute("SELECT * FROM pending ORDER BY slot,hash,reason"):
                    field, locale = self.slots[slot]
                    handle.write(model.canonical_json({"field": field, "locale": locale, "source_value_hash": source_hash, "source_value": json.loads(original), "reason": reason, "affected_rows": affected}) + "\n")
            pending_keys = pending_db.execute("SELECT count(*) FROM pending").fetchone()[0]
            validate_rebuilt_sidecars(canonical_sidecar, locale_paths, self.fields, asset_slug, release, feature_count, translations_path)
            for snapshot in snapshots:
                snapshot.verify()
            output_hashes = {path.name: local_io.file_sha256(path) for path in [translations_path, *locale_paths.values()]}
            report = {"schema_version": 1, "asset_slug": asset_slug, "release": release, "feature_count": feature_count,
                      "source_key_fields": self.config["source_key_fields"], "fields": self.fields, "locales": self.locales,
                      "input_sha256": {str(s.path): s.sha256 for s in snapshots}, "source_bundles": self.source_report["sources"],
                      "translations": localization.translations_payload(list(coverage.values())),
                      "debt_files": {loc: report.debt_path for loc, report in coverage.items()},
                      "by_locale": {loc: dict(counts[loc]) for loc in self.locales}, "unique_pending_tasks": pending_keys,
                      "requested_rows_complete": pending_keys == 0, "valid": True, "output_sha256": output_hashes,
                      "pending_tasks": str(pending_path), "publication_status": "local_candidate_only"}
            local_io.write_json(output_dir / "reuse-report.json", report)
            return report
        finally:
            pending_db.close()


def validate_rebuilt_sidecars(canonical: Path, localized: dict[str, Path], fields: Sequence[str], slug: str, release: str, count: int, translation_source: Path) -> None:
    """Stream the complete CSV/sidecar join in the rebuild's declared row order."""
    with ExitStack() as stack:
        reader = csv.DictReader(stack.enter_context(translation_source.open(encoding="utf-8", newline="")))
        require(reader.fieldnames == list(COLUMNS), "rebuilt translation CSV header differs")
        streams = {locale: model.read_metadata_sidecar(path) for locale, path in localized.items()}
        canonical_rows = model.read_metadata_sidecar(canonical)
        for stream in [canonical_rows, *streams.values()]:
            stack.callback(stream.close)
        seen = 0
        for expected in canonical_rows:
            require(expected["asset_slug"] == slug and expected["release"] == release, "unexpected canonical release")
            rows = {locale: [] for locale in localized}
            for field in fields:
                original = expected["properties"].get(field)
                if not isinstance(original, str) or not original.strip():
                    continue
                for locale in localized:
                    row = next(reader, None)
                    require(row is not None and set(row) == set(COLUMNS) and all(value is not None for value in row.values()), "rebuilt translation CSV is truncated or malformed")
                    require((row["feature_id"], row["field"], row["locale"]) == (expected["feature_id"], field, locale), "rebuilt translation CSV key/hash/order differs")
                    if row["review_state"] == localization.FAILED_REVIEW_STATE:
                        require(row["value"] == "", "failed translation must have an empty value")
                    else:
                        require(bool(row["value"].strip()) and not localization.translation_failed(row, original), "rebuilt CSV contains a failed translation")
                    localization.normalize_source_value_hash(row["source_value_hash"], context="rebuilt translation")
                    rows[locale].append(localization.TranslationRow(row_number=0, **row))
            for locale, stream in streams.items():
                report = localization.LocalizationReport(locale, str(canonical), str(translation_source), str(localized[locale]), sorted(fields))
                result = localization.localize_record(expected, rows=rows[locale], report=report)
                require(next(stream, None) == result, f"{locale}: localized row differs from canonical/translation CSV join")
            seen += 1
        require(seen == count, "incomplete canonical file")
        require(next(reader, None) is None, "extra translation CSV rows")
        require(all(next(stream, None) is None for stream in streams.values()), "extra localized rows")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("index")
    build.add_argument("--config", type=Path, required=True)
    build.add_argument("--database", type=Path, required=True)
    rebuild = sub.add_parser("rebuild")
    rebuild.add_argument("--database", type=Path, required=True)
    rebuild.add_argument("--supplement", type=Path)
    rebuild.add_argument("--canonical-sidecar", type=Path, required=True)
    rebuild.add_argument("--schema", type=Path, required=True)
    rebuild.add_argument("--asset-slug", required=True)
    rebuild.add_argument("--release", required=True)
    rebuild.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "index":
        report = build_memory(database=args.database, **json.loads(args.config.read_text()))
    else:
        memory = TranslationMemory(args.database, supplement=args.supplement)
        try:
            report = memory.rebuild(canonical_sidecar=args.canonical_sidecar, schema=args.schema, asset_slug=args.asset_slug, release=args.release, output_dir=args.output_dir)
        finally:
            memory.close()
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
