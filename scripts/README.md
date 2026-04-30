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

Dataset notification helpers live in:

```text
scripts/dataset_alerts.py
scripts/slack_notify.py
```

After a successful manual dataset upload, post a lightweight summary:

```bash
uv run python scripts/dataset_alerts.py upload-summary \
  --asset-slug example-asset \
  --changed-path gs://skytruth-shared-datasets-1/path/to/object.fgb
```

For canonical vector/table assets, compare and update the schema snapshot:

```bash
uv run python scripts/dataset_alerts.py check-schema \
  --asset-slug example-asset \
  --dataset-path ./example-asset.fgb
```

Schema deltas are emitted as structured Cloud Logging warnings and delivered
through the Cloud Monitoring Slack alert channel.

Production Terraform applies should use:

```bash
uv run python scripts/terraform_prod_apply.py
```

Install dependencies:

```bash
uv sync
```

Show help:

```bash
uv run python scripts/gcs_asset.py --help
```
