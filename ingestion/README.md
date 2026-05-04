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

## Default Publishing Semantics

Cron jobs should publish only meaningful dataset changes. When the source or
generated output is unchanged, write a skipped run record for observability and
leave release and `latest/` dataset artifacts unchanged. Do not repeat this
behavior in asset `update_cadence` metadata; use cadence values such as `daily`,
`weekly`, or `monthly`.

Jobs that poll an upstream source over an availability window should distinguish
the stable target release date from the scheduler attempt date. For example, an
early-month monthly source should publish under one month-start release path,
while later attempts for the same source period should only write a skipped
run/check-in record for the actual attempt date.

Every success and meaningful skip must refresh the asset release index under
`_catalog/releases/{asset-slug}.json`. After changing a job, verify both
`latest_release` and `latest_run`, and inspect the custom metadata on `latest/`
objects so it matches the current release bytes.

Normal cron runs must not require Git commits or tracked catalog date edits.
Keep repo asset docs and the CSV catalog focused on static registry metadata;
the bucket release index is the source of truth for latest release, last
check-in, source version, row count, and file hashes.

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
