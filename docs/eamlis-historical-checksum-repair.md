# EAMLIS historical checksum repair

The June 5, 2026 run record has an obsolete `sha256.manifest` value. Its
path-specific manifest reference already contains the correct hash, but the
manifest object's custom `sha256` label is also obsolete. The index currently
mirrors that obsolete object label. A strict full index
rebuild correctly refuses these conflicting declarations. Ordinary cron
publication updates the current index incrementally and does not scan this
historical record.

The verified manifest is:

```text
gs://skytruth-shared-datasets-1/300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/releases/2026-06-05/eamlis-abandoned-mine-land-inventory.manifest.json
generation: 1781551212087043
SHA-256: 7fb42b213cd25d1049baa8e08ec2202de99daf7706f60cfb1f49b7f7d6fca8c9
```

The audit compared all successful historical run records, their path/format
declarations and conflicting object labels against pinned object bytes. Only
June 5 supplied this conflict. The repair leaves every feature artifact, stored
geometry, feature ID, manifest byte and manifest generation unchanged.

## Reviewed operation

`publish.historical_checksum_repair` is part of an immutable reviewed publish
document, not a force option on the reader. The ordinary protected publisher
owns the operation; no local canonical writes are allowed.

The subtype contains `schema_version: 1`, `release`, `baseline_token`, the full
`original_record` and its raw `original_record_sha256`, and a `manifest` snapshot
with `path`, `generation`, `metageneration`, `size`, verified `sha256`, stored
custom `metadata`, `content_type` and `cache_control`. Capture these fields from
the object resource, not HTTP download response headers. The storage SDK may
update its local Blob cache-control value from a download response.

The plan must have exactly one promotion to the historical run record, with its
observed destination generation, a generation-pinned scratch source and explicit
JSON/no-cache metadata. The only explicit release-index target is EAMLIS. The
original run must name a successful release before the installed identity's
first release. Its exact path-specific manifest reference must already agree
with the independently hashed manifest. The approved staged JSON can change
only `sha256.manifest`; all other JSON values and types must be identical.

During promotion, the existing source-quality claim excludes cron writers and
other publications. A hold, changed current baseline, changed historical bytes,
changed manifest metadata or mismatched generation stops the operation. The
manifest label is patched using both generation and metageneration preconditions.
The run record is replaced through the existing generation-guarded copy.

The index step runs the unchanged strict full builder, independently verifies
any existing indexed checksum omitted by the rebuilt result, and requires the
result to equal the current index except for the reviewed historical manifest
checksum and update timestamp. It preserves existing verified checksums, every
release, latest pointers, counts and identity information. Only that checksum is
changed in the published index, with its current generation precondition.

Completion rechecks the label, exact corrected run, indexed manifest identity
and unchanged current release before releasing the same claim. An interruption
retains ownership. Retry the same approved PR transaction; exact completed
copies/patches are reused. Never refresh its destination expectations, create a
replacement owner, clear the claim locally or relax checksum validation.

## Validation and retirement

The regression fixture uses the real legacy producer, identity installation,
serialized publish plan, publisher entrypoint and strict index builder. External
GCS/auth transport is substituted; serialization and comparison remain real.
It reproduces the original checksum conflict, validates completion and exact
retry, and rejects changed source fields/types, expanded mutation targets,
changed generations/metadata, held or inactive identity state, unrelated index
changes and unverifiable retained checksums.

This is explicit persisted-data migration handling. It adds no fallback to the
normal reader or cron producer. After the reviewed repair, the installed
transaction document remains audit evidence. The subtype can be retired in a
later reviewed change once no interrupted repair owns a claim; existing
immutable documents and their authorization history must remain readable.
