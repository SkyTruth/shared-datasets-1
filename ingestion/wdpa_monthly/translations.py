"""Generation-pinned WDPA translation evidence and monthly reuse.

The initial evidence predates the ID reset and is joined only by SITE_PID and
exact source text. Both historical bundles remain fixed until both reset
publications finish. Later runs consume their owned publication's committed CSV.
"""

from contextlib import contextmanager
import gzip
import hashlib
from pathlib import Path

from ingestion.common import publication as p
from scripts.feature_metadata_translation_reuse import TranslationMemory, build_memory

LOCALES = ("es", "fr", "id", "pt", "pt_br", "sw")
FIELDS = ("NAME_ENG", "DESIG_ENG", "DESIG_TYPE", "GOV_TYPE", "OWN_TYPE", "NO_TAKE", "STATUS", "IUCN_CAT", "VERIF", "OECM_ASMT", "DESIG", "MANG_PLAN", "CONS_OBJ", "SUPP_INFO", "INLND_WTRS", "GOVSUBTYPE", "OWNSUBTYPE")
SUFFIXES = (".metadata.ndjson.gz", ".metadata-translations.csv")
LEGACY = {
    "wdpa-marine": (
        (1781025267875035, "7bae601c9d5643fcbcbc456e453c805c92b123e0d8dad51bf4a8bba8f0d5bbb9", 3094466),
        (1781542880691080, "fb35204e7b28a9f441aa086aac180fccbe23d926a5fb9ec7c4ee8c4569f2d7f9", 290751864),
    ),
    "wdpa-terrestrial": (
        (1781059584800280, "2d5a83c6a671e7ebb719ffbfe195b6ffc61698880629b796c91f59d58cd8ebac", 46855752),
        (1781110590312857, "0c71507f9e9244d2fcdb3be633fc2e78b391195b9cf37a26bb98bff5128bab86", 5562775096),
    ),
}


def download_source(bucket, version: p.ObjectVersion, destination: Path, *, compress: bool) -> None:
    bucket_name, name = p.split_uri(version.path)
    p.require(bucket_name == bucket.name, "translation source is outside the dataset bucket")
    digest, size = hashlib.sha256(), 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with bucket.blob(name, generation=version.generation).open("rb", chunk_size=8 * 1024 * 1024, if_generation_match=version.generation) as source, destination.open("xb") as raw:
        target = gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=1, mtime=0) if compress else raw
        try:
            while data := source.read(8 * 1024 * 1024):
                digest.update(data)
                size += len(data)
                target.write(data)
        finally:
            if compress:
                target.close()
    p.require(size == version.size and digest.hexdigest() == version.sha256, "translation source generation/hash differs from reviewed evidence")


@contextmanager
def prepare_memory(publisher, assets, workdir: Path):
    committed = {asset.slug: publisher.committed_artifacts(asset, suffixes=SUFFIXES) for asset in assets}
    supplement = None
    for asset in assets:
        if committed[asset.slug] is None:
            # This branch is reachable only through the explicit pending-reset
            # adoption state, never through a missing manifest or failed read.
            p.require(publisher.bucket.name == "skytruth-shared-datasets-1", "legacy reset evidence is pinned to the production dataset bucket")
            approved = publisher.reset_translation_supplement(asset)
            p.require(supplement is None or supplement == approved, "pending WDPA resets must approve the same shared translation supplement")
            supplement = approved

    sources = []
    for asset in assets:
        versions = committed[asset.slug]
        if supplement is not None:
            # An area can move between realms. A partially completed reset must
            # retain both historical sources, including the published realm's.
            release = "2026-06-09"
            versions = {suffix: p.ObjectVersion(f"gs://{publisher.bucket.name}/{asset.root}/releases/{release}/{asset.slug}{suffix}", *pin)
                        for suffix, pin in zip(SUFFIXES, LEGACY[asset.slug], strict=True)}
        else:
            release = next(iter(versions.values())).path.split("/releases/", 1)[1].split("/", 1)[0]
        local_paths = {}
        for suffix, version in versions.items():
            path = workdir / "translation-inputs" / asset.slug / (asset.slug + suffix + (".gz" if suffix.endswith(".csv") else ""))
            download_source(publisher.bucket, version, path, compress=suffix.endswith(".csv"))
            local_paths[suffix] = str(path)
        sources.append({"asset_slug": asset.slug, "release": release, "canonical_sidecar": local_paths[SUFFIXES[0]], "translation_source": local_paths[SUFFIXES[1]],
                        "provenance": {suffix: version.identity() for suffix, version in versions.items()}})
    database = workdir / "translation-memory.sqlite"
    build_memory(database=database, sources=sources, fields=FIELDS, locales=LOCALES, source_key_fields=("SITE_PID",))
    # Rebuilds consume only the verified reusable SQLite index from here.
    for source in sources:
        for key in ("canonical_sidecar", "translation_source"):
            Path(source[key]).unlink()
    supplement_path = None
    if supplement is not None:
        supplement_path = workdir / "translation-inputs" / "approved-supplement.ndjson"
        download_source(publisher.bucket, supplement, supplement_path, compress=False)
    memory = TranslationMemory(database, supplement=supplement_path)
    try:
        yield memory
    finally:
        memory.close()
