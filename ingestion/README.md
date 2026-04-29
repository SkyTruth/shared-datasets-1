# ingestion/

This directory contains production scheduled ingestion jobs and shared helpers.

## Layout

Each production cron job gets its own package:

```text
ingestion/<job_slug>/
  README.md
  Dockerfile
  run.py
tests/test_<job_slug>.py
terraform/envs/prod/<job_slug>.tf
```

Use `ingestion/common/` for reusable internals that are not tied to one source
or asset, such as:

- generation-preconditioned GCS publishing
- run-record writes
- logging setup
- subprocess helpers
- content type selection
- hashing
- temporary-file cleanup

Keep source-specific parsing, filtering, schema choices, asset slugs, canonical
paths, conversion rules, environment variables, and scheduler configuration in
the owning job package.

## Live Job Boundaries

Do not import from one job package into another. For example, a new protected
areas job should not import from `ingestion.wdpa_monthly`; reusable behavior
belongs in `ingestion/common/`.

Do not edit a functioning live job to support a new job unless the work is an
explicit behavior-preserving refactor. Preserve Cloud Run job names, scheduler
names, service account identities, asset slugs, GCS paths, output formats,
schemas, entrypoints, and run-record shape unless the user approves changing
those live surfaces.

When `ingestion/common/` changes, run the focused tests for every production job
that imports it.
