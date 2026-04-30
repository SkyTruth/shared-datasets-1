# scripts/

This directory contains small operational scripts for maintainers and AI agents.

The most important script is:

```text
scripts/gcs_asset.py
```

It is the default interface for safe Cloud Storage object operations.

Raster validation helpers live in:

```text
scripts/raster_asset.py
```

Use `validate-cog` before publishing a local Cloud Optimized GeoTIFF:

```bash
uv run python scripts/raster_asset.py validate-cog ./asset.tif
```

Install dependencies:

```bash
uv sync
```

Show help:

```bash
uv run python scripts/gcs_asset.py --help
```
