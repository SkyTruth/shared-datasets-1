#!/usr/bin/env python3
"""Safe-ish GCS asset operations for shared-datasets-1.

This CLI intentionally wraps a small subset of google-cloud-storage so agents and
maintainers have one predictable interface for inspecting, downloading, uploading,
and replacing bucket objects.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

# Keep direct invocation (`python scripts/gcs_asset.py ...`) equivalent to
# module/imported use by making repo-root packages importable.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import typer
import yaml
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage
from rich import print
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True)
release_index_app = typer.Typer(no_args_is_help=True)
app.add_typer(release_index_app, name="release-index")
console = Console()
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RUN_RECORD_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
RESERVED_TOP_LEVEL = {"_catalog", "_templates", "_scratch", "_deprecated", "000-system"}
ROOT_ALLOWED_DOCS = {"README.md"}
APPROVED_DATA_EXTENSIONS = {".fgb", ".pmtiles", ".geojson", ".ndgeojson", ".csv", ".tif", ".tiff"}
PREVIEW_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SOURCE_ARCHIVE_EXTENSIONS = APPROVED_DATA_EXTENSIONS | {".nc", ".grib", ".grib2", ".hdf", ".h5", ".hdf5"}
ALLOW_CANONICAL_MUTATION_ENV = "SHARED_DATASETS_ALLOW_CANONICAL_MUTATION"


def parse_gs_uri(uri: str) -> Tuple[str, str]:
    if not uri.startswith("gs://"):
        raise typer.BadParameter(f"Expected gs:// URI, got: {uri}")
    rest = uri[5:]
    if "/" not in rest:
        return rest, ""
    bucket, name = rest.split("/", 1)
    if not bucket:
        raise typer.BadParameter(f"Missing bucket in URI: {uri}")
    return bucket, name


def get_client() -> storage.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    return storage.Client(project=project) if project else storage.Client()


def get_blob(uri: str) -> storage.Blob:
    bucket_name, name = parse_gs_uri(uri)
    if not name:
        raise typer.BadParameter(f"Expected object URI, got bucket root: {uri}")
    return get_client().bucket(bucket_name).blob(name)


def require_mutation_allowed(uri: str, *, operation: str, unsafe_overwrite: bool = False) -> None:
    """Refuse non-scratch mutations unless an approved runtime explicitly opts in."""
    _bucket_name, name = parse_gs_uri(uri)
    if not name:
        raise typer.BadParameter(f"{operation} requires an object URI, not a bucket root")
    is_scratch = name.startswith("_scratch/")
    if unsafe_overwrite and not is_scratch:
        raise typer.BadParameter("--unsafe-overwrite is only allowed for _scratch/ objects")
    if is_scratch:
        return
    if os.environ.get(ALLOW_CANONICAL_MUTATION_ENV) == "1":
        return
    raise typer.BadParameter(
        f"{operation} to non-scratch objects requires {ALLOW_CANONICAL_MUTATION_ENV}=1 "
        "from the approved publisher workflow, scheduled job, or documented break-glass path"
    )


def content_type_for(path: Path, explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        return "image/tiff; application=geotiff; profile=cloud-optimized"
    if suffix == ".fgb":
        return "application/octet-stream"
    if suffix == ".pmtiles":
        return "application/vnd.pmtiles"
    if suffix in {".json", ".geojson"}:
        return "application/json"
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".html":
        return "text/html"
    if suffix == ".css":
        return "text/css"
    if suffix == ".js":
        return "application/javascript"
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".ndgeojson":
        return "application/x-ndjson"
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed


def load_categories(path: Path) -> dict[str, set[str]]:
    payload = yaml.safe_load(path.read_text()) or {}
    categories = payload.get("categories", {})
    return {name: set((data.get("subcategories") or {}).keys()) for name, data in categories.items()}


def validate_asset_object_name(name: str, categories: dict[str, set[str]]) -> list[str]:
    parts = [part for part in name.split("/") if part]
    if not parts:
        return ["object path is empty"]
    if len(parts) == 1:
        if parts[0] in ROOT_ALLOWED_DOCS:
            return []
        return ["root-level bucket objects are noncanonical; use a reserved system prefix or asset root"]

    top = parts[0]
    if top in RESERVED_TOP_LEVEL:
        return []
    if top not in categories:
        return [f"unknown top-level prefix {top!r}"]
    if len(parts) < 3:
        return ["object under category must be inside {category}/{subcategory}/{asset-slug}/"]

    subcategory = parts[1]
    slug = parts[2]
    errors: list[str] = []
    if subcategory not in categories[top]:
        errors.append(f"unknown subcategory {top}/{subcategory}")
    if not SLUG_PATTERN.fullmatch(slug):
        errors.append(f"asset slug must be lowercase kebab-case: {slug!r}")

    rel = parts[3:]
    if not rel:
        errors.append("asset root object is missing README.md/latest/releases/runs path")
        return errors
    if rel == ["README.md"]:
        return errors
    if rel[0] == "latest":
        if rel == ["latest", "manifest.json"]:
            return errors
        if len(rel) != 2:
            errors.append("latest/ should contain direct files only, except latest/manifest.json for Zarr")
        ext = Path(rel[-1]).suffix.lower()
        if ext not in APPROVED_DATA_EXTENSIONS:
            errors.append(f"latest/ file extension {ext or '<none>'} is not approved")
        return errors
    if rel[0] == "releases":
        if len(rel) < 3:
            errors.append("releases/ objects must be under releases/YYYY-MM-DD/")
            return errors
        if not DATE_PATTERN.fullmatch(rel[1]):
            errors.append("releases/ child must be YYYY-MM-DD")
        if any(part.endswith(".zarr") for part in rel):
            return errors
        ext = Path(rel[-1]).suffix.lower()
        if ext not in APPROVED_DATA_EXTENSIONS:
            errors.append(f"release file extension {ext or '<none>'} is not approved")
        return errors
    if rel[0] == "previews":
        ext = Path(rel[-1]).suffix.lower()
        if ext not in PREVIEW_EXTENSIONS:
            errors.append(f"previews/ file extension {ext or '<none>'} is not an approved preview image format")
        return errors
    if rel[0] in {"source", "sources", "archive"}:
        ext = Path(rel[-1]).suffix.lower()
        if ext not in SOURCE_ARCHIVE_EXTENSIONS:
            errors.append(f"{rel[0]}/ file extension {ext or '<none>'} is not a documented source/archive format")
        return errors
    if rel[0] == "runs":
        if len(rel) != 2 or not RUN_RECORD_PATTERN.fullmatch(rel[1]):
            errors.append("runs/ records must be named YYYY-MM-DD.json")
        return errors

    errors.append("object is outside README.md/latest/releases/previews/source/archive/runs layout")
    return errors


@app.command("list")
def list_prefix(
    uri: str = typer.Argument(..., help="Bucket or prefix URI, e.g. gs://bucket/path/"),
    max_results: int = typer.Option(200, help="Maximum objects to list."),
) -> None:
    """List objects under a GCS bucket/prefix."""
    bucket_name, prefix = parse_gs_uri(uri)
    client = get_client()
    blobs = client.list_blobs(bucket_name, prefix=prefix, max_results=max_results)

    table = Table(title=f"gs://{bucket_name}/{prefix}")
    table.add_column("name")
    table.add_column("size", justify="right")
    table.add_column("updated")
    table.add_column("generation", justify="right")

    count = 0
    for blob in blobs:
        count += 1
        table.add_row(blob.name, str(blob.size or 0), str(blob.updated), str(blob.generation))

    console.print(table)
    print(f"[dim]Listed {count} object(s).[/dim]")


@app.command("stat")
def stat(uri: str = typer.Argument(..., help="Object URI.")) -> None:
    """Print object metadata as JSON."""
    blob = get_blob(uri)
    try:
        blob.reload()
    except NotFound:
        print(f"[red]Object not found:[/red] {uri}")
        raise typer.Exit(1)

    payload = {
        "uri": uri,
        "bucket": blob.bucket.name,
        "name": blob.name,
        "size": blob.size,
        "content_type": blob.content_type,
        "generation": blob.generation,
        "metageneration": blob.metageneration,
        "etag": blob.etag,
        "crc32c": blob.crc32c,
        "md5_hash": blob.md5_hash,
        "updated": blob.updated.isoformat() if blob.updated else None,
        "metadata": blob.metadata or {},
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


@app.command("download")
def download(
    uri: str = typer.Argument(..., help="Object URI."),
    dest: Path = typer.Argument(..., help="Local destination path."),
    generation: Optional[int] = typer.Option(None, help="Download only this generation."),
) -> None:
    """Download one object."""
    blob = get_blob(uri)
    kwargs = {}
    if generation is not None:
        kwargs["if_generation_match"] = generation
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(dest, **kwargs)
    print(f"Downloaded {uri} -> {dest}")


@app.command("upload")
def upload(
    src: Path = typer.Argument(..., exists=True, dir_okay=False, help="Local file to upload."),
    uri: str = typer.Argument(..., help="Destination object URI."),
    replace_generation: Optional[int] = typer.Option(
        None,
        help="Only replace destination if its current generation matches this value.",
    ),
    unsafe_overwrite: bool = typer.Option(
        False,
        help="Blindly overwrite existing destination. Use only with explicit approval.",
    ),
    content_type: Optional[str] = typer.Option(None, help="Explicit content type."),
    cache_control: Optional[str] = typer.Option(None, help="Explicit Cache-Control metadata."),
    metadata_json: Optional[str] = typer.Option(None, help="JSON object of custom metadata."),
) -> None:
    """Upload a local file.

    Default behavior is no-clobber: succeeds only if no live destination object exists.
    Use --replace-generation for safe replacement of an existing object.
    Use --unsafe-overwrite only when explicitly approved.
    """
    require_mutation_allowed(uri, operation="upload", unsafe_overwrite=unsafe_overwrite)
    blob = get_blob(uri)
    if cache_control:
        blob.cache_control = cache_control
    if metadata_json:
        metadata = json.loads(metadata_json)
        if not isinstance(metadata, dict):
            raise typer.BadParameter("metadata-json must decode to an object")
        blob.metadata = {str(k): str(v) for k, v in metadata.items()}

    upload_kwargs = {}
    if replace_generation is not None and unsafe_overwrite:
        raise typer.BadParameter("Use either --replace-generation or --unsafe-overwrite, not both.")
    if replace_generation is not None:
        upload_kwargs["if_generation_match"] = replace_generation
    elif not unsafe_overwrite:
        upload_kwargs["if_generation_match"] = 0

    try:
        blob.upload_from_filename(
            src,
            content_type=content_type_for(src, content_type),
            **upload_kwargs,
        )
    except PreconditionFailed as exc:
        print("[red]Precondition failed.[/red]")
        print("Destination changed or already exists. Run `stat` and retry with the current generation if replacement is intended.")
        raise typer.Exit(2) from exc

    blob.reload()
    print(
        json.dumps(
            {
                "uploaded": str(src),
                "uri": uri,
                "generation": blob.generation,
                "size": blob.size,
                "content_type": blob.content_type,
                "cache_control": blob.cache_control,
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("delete")
def delete(
    uri: str = typer.Argument(..., help="Object URI."),
    generation: int = typer.Option(..., help="Delete only this exact object generation."),
    confirm: str = typer.Option("", help="Must be DELETE to confirm a destructive operation."),
) -> None:
    """Delete one object using a generation precondition."""
    if confirm != "DELETE":
        raise typer.BadParameter("Pass --confirm DELETE to confirm the destructive delete.")
    require_mutation_allowed(uri, operation="delete")
    blob = get_blob(uri)
    try:
        blob.delete(if_generation_match=generation)
    except NotFound as exc:
        print(f"[red]Object not found:[/red] {uri}")
        raise typer.Exit(1) from exc
    except PreconditionFailed as exc:
        print("[red]Precondition failed.[/red]")
        print("Object generation changed or does not match the requested delete generation.")
        raise typer.Exit(2) from exc
    print(
        json.dumps(
            {
                "deleted": uri,
                "generation": generation,
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("copy")
def copy_object(
    src_uri: str = typer.Argument(..., help="Source object URI."),
    dst_uri: str = typer.Argument(..., help="Destination object URI."),
    unsafe_overwrite: bool = typer.Option(False, help="Allow replacing destination without a generation precondition."),
    source_generation: Optional[int] = typer.Option(None, help="Require this source generation."),
    replace_generation: Optional[int] = typer.Option(None, help="Require this destination generation."),
    content_type: Optional[str] = typer.Option(None, help="Optional destination content type override."),
    cache_control: Optional[str] = typer.Option(None, help="Optional destination Cache-Control metadata override."),
) -> None:
    """Copy an object within or across buckets.

    Default destination behavior is no-clobber.
    """
    src_bucket_name, src_name = parse_gs_uri(src_uri)
    dst_bucket_name, dst_name = parse_gs_uri(dst_uri)
    if not src_name or not dst_name:
        raise typer.BadParameter("copy requires object URIs, not bucket roots")
    require_mutation_allowed(dst_uri, operation="copy", unsafe_overwrite=unsafe_overwrite)

    client = get_client()
    src_bucket = client.bucket(src_bucket_name)
    dst_bucket = client.bucket(dst_bucket_name)
    src_blob = src_bucket.blob(src_name)

    kwargs = {}
    if source_generation is not None:
        kwargs["if_source_generation_match"] = source_generation
    if replace_generation is not None and unsafe_overwrite:
        raise typer.BadParameter("Use either --replace-generation or --unsafe-overwrite, not both.")
    if replace_generation is not None:
        kwargs["if_generation_match"] = replace_generation
    elif not unsafe_overwrite:
        kwargs["if_generation_match"] = 0

    try:
        new_blob = src_bucket.copy_blob(src_blob, dst_bucket, new_name=dst_name, **kwargs)
        if content_type is not None or cache_control is not None:
            if content_type is not None:
                new_blob.content_type = content_type
            if cache_control is not None:
                new_blob.cache_control = cache_control
            new_blob.patch(if_generation_match=int(new_blob.generation))
    except PreconditionFailed as exc:
        print("[red]Precondition failed.[/red]")
        raise typer.Exit(2) from exc

    print(
        json.dumps(
            {
                "copied_from": src_uri,
                "copied_to": dst_uri,
                "generation": new_blob.generation,
                "size": new_blob.size,
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("exists")
def exists(uri: str = typer.Argument(..., help="Object URI.")) -> None:
    """Exit 0 if object exists, 1 if it does not."""
    blob = get_blob(uri)
    if blob.exists():
        print(f"exists: {uri}")
        raise typer.Exit(0)
    print(f"missing: {uri}")
    raise typer.Exit(1)


@app.command("publish-release")
def publish_release(
    asset_slug: str = typer.Option(..., help="Existing catalog asset slug."),
    release_date: str = typer.Option(..., help="Release date in YYYY-MM-DD form."),
    publish_dir: Optional[Path] = typer.Option(
        None,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Directory containing {asset-slug}.{fgb,pmtiles,geojson,ndgeojson,csv,tif}.",
    ),
    artifact: list[str] = typer.Option(
        [],
        "--artifact",
        help="Explicit artifact override in format=/path/file form. May be repeated.",
    ),
    allow_stale_format: list[str] = typer.Option(
        [],
        "--allow-stale-format",
        help="Allow a catalog-listed companion format to remain unchanged.",
    ),
    source_version: str = typer.Option("", help="Source version string for the run record."),
    row_count: Optional[int] = typer.Option(None, help="Published row count, if known."),
    notes: str = typer.Option("", help="Notes for the run record."),
    readme_path: Optional[Path] = typer.Option(
        None,
        exists=True,
        dir_okay=False,
        help="Optional local README.md to replace at the remote asset root.",
    ),
    remote_catalog_path: Optional[Path] = typer.Option(
        None,
        exists=True,
        dir_okay=False,
        help="Optional local catalog CSV to replace at _catalog/shared-datasets-catalog.csv.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate and print the publish plan without writing to GCS.",
    ),
    no_notify: bool = typer.Option(
        False,
        "--no-notify",
        help="Do not send the upload summary notification.",
    ),
    skip_schema_snapshot: bool = typer.Option(
        False,
        "--skip-schema-snapshot",
        help="Do not update the schema snapshot after publish.",
    ),
    compatibility_waiver: Optional[Path] = typer.Option(
        None,
        "--compatibility-waiver",
        exists=True,
        dir_okay=False,
        help="Reviewed waiver JSON for otherwise blocked schema compatibility changes.",
    ),
) -> None:
    """Publish prepared local artifacts as an immutable release and latest update."""
    from scripts import publish_release as publish_release_core

    client = get_client()
    try:
        plan = publish_release_core.build_publish_plan(
            asset_slug=asset_slug,
            release_date=release_date,
            publish_dir=publish_dir,
            artifact_overrides=publish_release_core.parse_artifact_overrides(artifact),
            allow_stale_formats=allow_stale_format,
            client=client,
            readme_path=readme_path,
            remote_catalog_path=remote_catalog_path,
            compatibility_waiver_path=compatibility_waiver,
        )
        if dry_run:
            sys.stdout.write(json.dumps(publish_release_core.plan_to_dict(plan), indent=2, sort_keys=True) + "\n")
            return
        require_mutation_allowed(plan.run_record_uri, operation="publish-release")
        result = publish_release_core.execute_publish_plan(
            plan,
            client=client,
            source_version=source_version,
            row_count=row_count,
            notes=notes,
            notify=not no_notify,
            update_schema_snapshot=not skip_schema_snapshot,
        )
    except publish_release_core.PublishReleaseError as exc:
        print(f"[red]publish-release failed:[/red] {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc

    sys.stdout.write(json.dumps(publish_release_core.result_to_dict(result), indent=2, sort_keys=True) + "\n")


@release_index_app.command("rebuild")
def rebuild_release_index(
    asset_slug: str = typer.Option(..., help="Existing catalog asset slug."),
    catalog_path: Path = typer.Option(
        Path("catalog/shared-datasets-catalog.csv"),
        "--catalog",
        exists=True,
        dir_okay=False,
        help="Local catalog CSV used to locate the asset root.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the rebuilt release index without writing it.",
    ),
) -> None:
    """Rebuild _catalog/releases/{asset_slug}.json from remote releases and runs."""
    from ingestion.common import release_index as release_index_core
    from scripts import publish_release as publish_release_core

    try:
        catalog = publish_release_core.load_catalog(catalog_path)
        row = catalog.get(asset_slug)
        if row is None:
            raise release_index_core.ReleaseIndexError(f"asset slug is not in the catalog: {asset_slug}")
        bucket_name, _asset_root = release_index_core.asset_root_from_catalog_row(row)
        bucket = get_client().bucket(bucket_name)
        payload = release_index_core.rebuild_index_from_bucket(bucket, row)
        index_uri = release_index_core.release_index_uri(bucket.name, asset_slug)
        if dry_run:
            sys.stdout.write(
                json.dumps(
                    {
                        "dry_run": True,
                        "release_index": index_uri,
                        "payload": payload,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            return

        loaded = release_index_core.load_release_index(bucket, asset_slug)
        require_mutation_allowed(index_uri, operation="release-index rebuild")
        write_info = release_index_core.write_release_index(
            bucket,
            asset_slug,
            payload,
            generation=loaded.generation,
        )
    except (release_index_core.ReleaseIndexError, PreconditionFailed) as exc:
        print(f"[red]release-index rebuild failed:[/red] {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc

    sys.stdout.write(
        json.dumps(
            {
                "dry_run": False,
                "release_index": write_info,
                "payload": payload,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


@app.command("validate-path")
def validate_path(
    uri: str = typer.Argument(..., help="Object URI or bucket-relative object name."),
    categories: Path = typer.Option(Path("catalog/categories.yaml"), help="Categories YAML path."),
) -> None:
    """Validate an intended GCS object path against shared-datasets layout rules."""
    if uri.startswith("gs://"):
        bucket_name, name = parse_gs_uri(uri)
    else:
        bucket_name, name = "", uri
    if not name:
        raise typer.BadParameter("Expected object URI or object name, not a bucket root.")
    errors = validate_asset_object_name(name, load_categories(categories))
    payload = {
        "valid": not errors,
        "bucket": bucket_name or None,
        "name": name,
        "errors": errors,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if errors:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
