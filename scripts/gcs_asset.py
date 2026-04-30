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
from pathlib import Path
from typing import Optional, Tuple

import typer
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage
from rich import print
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True)
console = Console()


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
    metadata_json: Optional[str] = typer.Option(None, help="JSON object of custom metadata."),
) -> None:
    """Upload a local file.

    Default behavior is no-clobber: succeeds only if no live destination object exists.
    Use --replace-generation for safe replacement of an existing object.
    Use --unsafe-overwrite only when explicitly approved.
    """
    blob = get_blob(uri)
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
) -> None:
    """Copy an object within or across buckets.

    Default destination behavior is no-clobber.
    """
    src_bucket_name, src_name = parse_gs_uri(src_uri)
    dst_bucket_name, dst_name = parse_gs_uri(dst_uri)
    if not src_name or not dst_name:
        raise typer.BadParameter("copy requires object URIs, not bucket roots")

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


if __name__ == "__main__":
    app()
