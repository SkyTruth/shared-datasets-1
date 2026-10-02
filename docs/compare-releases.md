# Compare releases

Click **Compare releases** beside Version to enter comparison mode. The single
Version dropdown becomes **Before** and **After**, with the selected release and
its immediate predecessor as defaults. Comparison starts automatically and
reruns when either selection changes. The same button becomes **Close comparison**;
closing restores ordinary browsing. There is no second run button or map-mode
selector. The union occupies the primary map at the top of the detail view,
retaining its viewport. Release, change, and schema summaries use compact tables.

The authenticated catalog viewer performs complete comparisons. A static catalog
without the endpoint explains availability, keeps browsing/downloads intact, and
supports visual release inspection and the local CLI route. Missing canonical
metadata, schema, or manifest artifacts prevent an exact comparison; they do not
trigger a latest fallback.

The result contains six mutually exclusive feature counts, separate schema
changes, a searchable table with 50 rows per page, before/after source properties,
publication provenance, and an export of every feature classification. Selecting
a row or clicking a displayed feature opens its properties. `Absent` differs
from explicit JSON `null`. The inspector retains publication fields for inspection;
source-property changes exclude the canonical hash exclusions and any declared
manifest exclusions. Source language is used regardless of the catalog's display
language. Export includes pinned inputs, checksums/sizes when declared, comparison
policy/version, identity assessment, schema changes, methods, counts, limits,
publication inputs and the complete ID/classification list. It does not embed
all source properties; those remain in the pinned sidecars.

## Deterministic semantics

`scripts/compare_releases.py` owns the model; both CLI and viewer call it.
Inputs are the canonical `{slug}.metadata.ndjson.gz`, release schema and manifest,
each identified by an exact GCS path and generation. Display PMTiles references
are pinned separately in the same snapshot. The engine enforces declared sizes
and SHA-256 checksums for all bytes it reads and cross-checks artifact identities
against the manifest. The manifest's own generation comes from the release
index/download evidence, since a manifest cannot embed its own generation.

The parser validates record and release identity, sidecar/schema/manifest versions,
unique feature IDs and record identity keys, required SHA-256 hashes, source-field
ID values, generated assignment keys, schema projection, the embedded schema,
and complete manifest feature counts. Supported identity evidence also enables
recomputation of canonical `properties_hash`. Duplicate JSON keys and non-finite
JSON numbers are rejected. The schema validator's established contract checks
field declarations and property membership; it does not coerce source values into
declared datatypes. No input is sampled or silently truncated.

Identity is comparable only when the manifests agree on strategy, source fields,
assignment semantics, hash algorithms/canonicalization and hash exclusions.
Generated identities additionally require the same explicit `contract_id` and
supported versioned sequence state. Source-field identities require compatible
source-field schema semantics, including datatype, nullability and projection;
they legitimately have no generated contract field. Legacy/missing identity
evidence, contract resets and incompatible semantics withhold feature counts,
matching and inspection. They still allow geometry colors and schema/publication comparison; geometry lookups never join feature IDs across releases.
There is no inferred identity continuity from similar geometry or numeric IDs.
Generated IDs must be below the declared next allocation. Each record must match
its declared assignment-key strategy. The publisher owns reviewed identity
overrides: a deliberate new ID remains an addition/removal; an approved reused
ID retains continuity. The reader does not invent a key-to-ID match or require
unchanged content keys across releases, since reviewed overrides may change them.

For compatible identities, match only by `feature_id`. A baseline-only ID is
removed; a target-only ID is added. For shared IDs, compare both hashes:

| Geometry hash | Properties hash | Primary classification |
| --- | --- | --- |
| Equal | Equal | Unchanged |
| Different | Equal | Geometry only |
| Equal | Different | Properties only |
| Different | Different | Both |

An edited record receiving a new ID under its declared assignment strategy is
an addition/removal. Schema additions, removals, datatype changes and field
semantics changes are separate, including fields not projected into sidecars.
Schema-only changes do not imply feature-value changes. Sidecar row order does
not affect results; result pages and reports sort IDs by binary string order.
Provenance, hash-excluded operational fields and localized sidecars do not create
source-property changes. No feature counts come from PMTiles.

## Union map

The primary map displays the union of Before and After geometry. New geometry
is green, removed geometry red, and identical geometry with altered source
metadata yellow. Unchanged geometry is gray with faint fill and outline opacity.
A moved feature has a red old footprint and green new footprint. Exact shared
geometry renders once from the After source to avoid doubling its opacity.

Geometry membership uses exact canonical `geometry_hash` sets, independently of
feature identity. For a shared geometry, differing sets of canonical
`properties_hash` values produce yellow. An exact shared geometry can be yellow
even if an identity edit is correctly an addition/removal in the table. This
geometric membership is not an invented feature match. Duplicate records on the
same geometry contribute a set of distinct source-property hashes. These display
annotations are computed from complete sidecars, never tile geometry.

Every loaded map feature receives a release-scoped geometry color, independently
of the table page, search, or feature-ID compatibility. The viewer looks up at
most 200 IDs per request in the complete comparison index and applies the results
as map feature states. IDs remain scoped to their own release: an ID reused after
a reset can correctly be red in Before and green in After. Paging or inspecting
records does not replace these colors. Geometry summary counts describe unique
geometry hashes; they are not feature-ID classifications.

Tiles simplify geometry and visibility varies with zoom. Missing or unauthorized
historical tile generations produce visible errors. Map signer and comparison
responses must match the selected paths/generations before use. Comparison report
schema version 2 adds geometry counts and the complete map-color scope.

## Viewer API and budgets

All comparison routes are same-origin and require the viewer's IAP authorization.
Private/internal assets also require an allowed identity when local public-only
API testing disables the global IAP check. Every start, poll, page, inspection,
map lookups, cancellation and export rechecks the current catalog and user. Jobs are bound to
the requesting email. Callers select catalog-owned slugs and concrete dates;
caller-provided object URIs cannot choose server download targets. Client expected
paths/generations are equality checks, not download authority.

```http
POST /api/comparisons
GET /api/comparisons/{job_id}?offset=0&limit=50&query=&classification=
GET /api/comparisons/{job_id}?feature_id=1
POST /api/comparisons/{job_id}/map
POST /api/comparisons/{job_id}/cancel
GET /api/comparisons/{job_id}/report
```

`GET /api/comparisons` reports capabilities and limits. Start takes `slug`,
`baseline`, `target`, and `expected`, with baseline/target role dictionaries of
`{path, generation}` for metadata/schema/manifest and available PMTiles. Start
returns `202` with a job ID and pinned inputs. Polls return state/progress and,
on completion, summary plus a bounded page when feature identity is comparable.
`POST /api/comparisons/{job_id}/map` takes `side` (`baseline` or `target`) and
1–200 unique `feature_ids`, with a 16-KiB request cap. It returns geometry colors
within that release even when cross-release feature identity is incompatible.
Unknown IDs fail visibly rather than being treated as unchanged. Invalid selectors/options return
400; missing authentication 401; denied domains 403; expired/unavailable jobs 404;
changed selected snapshots 409; oversized requests/responses/reports 413; job
capacity 429; unavailable catalog/backend authorization 503. Input-validation,
missing-byte and resource failures are terminal `failed` jobs with a visible
reason and no partial summary/counts. Cancellation is cooperative and checked
during downloads, row processing and SQLite work; an in-flight storage read has
a 15-second transport timeout with retries disabled.

| Interactive limit | Budget |
| --- | ---: |
| Records per release | 100,000 |
| Bytes per input artifact | 64 MiB |
| Schema/manifest contract each | 4 MiB |
| Expanded bytes per sidecar | 256 MiB |
| Workspace files per job | 128 MiB |
| Computation/download deadline | 120 seconds |
| Individual sidecar row | 900 KiB |
| Page size | 100 maximum; UI uses 50 |
| Response or complete report | 10 MiB |
| Start body | 16 KiB |
| Concurrent jobs per instance | 2 |
| Retained jobs per instance | 8 |
| Job retention | 15 minutes from start |

Budgets are independent: a dataset below the row limit can exceed workspace or
byte limits. Such jobs fail explicitly and point to the CLI. The browser never
indexes sidecars or performs expensive comparison work on its UI thread.

Jobs use task-scoped SQLite files under the standard local work root, not a
persistent database. There is no cross-job result cache; immutable job results
are tied to their complete inputs and policy/result version. Same-date generation
changes produce a different input key. The catalog discovers indexed snapshots,
not a history of overwritten generations. Old generations must actually be
retained and authorized to read; failures never substitute latest.

Viewer configuration enables Cloud Run session affinity and CPU outside requests,
with 2 GiB memory. Background workers need CPU between polls, and Cloud Run's
writable filesystem consumes instance memory. The bounded eight-job/128-MiB
workspace policy leaves headroom for processing. These are reviewed Terraform
source changes, routed through the existing protected catalog-viewer and preview
workflows; implementation testing does not deploy them. Session affinity is
[best effort](https://docs.cloud.google.com/run/docs/configuring/session-affinity):
an instance replacement, revision transition, high utilization or broken affinity
can expire a task. The viewer returns a visible rerun instruction, rather than
recreating a task from unrelated inputs. Browser cookies and `credentials: include`
preserve normal affinity. The CPU setting uses
[instance-based billing](https://docs.cloud.google.com/run/docs/configuring/billing-settings),
which can increase runtime cost; no minimum instance count is added.

## Local CLI

Follow the repo's `uv` environment instructions. Prepare two snapshot JSON files
from release-index evidence and generation-pinned downloads in a named workspace.
The example below is a shape template: replace paths, generations, sizes and hashes
with observed values; never invent missing evidence. Include only the canonical
sidecar, schema and manifest for computation. Available PMTiles descriptors may
also be included, but the CLI does not read tiles.

```json
{
  "asset_slug": "example-asset",
  "release": "2026-05-01",
  "files": {
    "metadata": {"path": "gs://skytruth-shared-datasets-1/category/subcategory/example-asset/releases/2026-05-01/example-asset.metadata.ndjson.gz", "generation": "REPLACE_WITH_OBSERVED_GENERATION"},
    "schema": {"path": "gs://skytruth-shared-datasets-1/category/subcategory/example-asset/releases/2026-05-01/example-asset.schema.json", "generation": "REPLACE_WITH_OBSERVED_GENERATION"},
    "manifest": {"path": "gs://skytruth-shared-datasets-1/category/subcategory/example-asset/releases/2026-05-01/example-asset.manifest.json", "generation": "REPLACE_WITH_OBSERVED_GENERATION"}
  },
  "local_paths": {
    "metadata": "/absolute/workspace/example-asset.metadata.ndjson.gz",
    "schema": "/absolute/workspace/example-asset.schema.json",
    "manifest": "/absolute/workspace/example-asset.manifest.json"
  }
}
```

Preserve `sha256` and `size` in each descriptor whenever declared. For currently
indexed objects, `scripts/gcs_asset.py download URI LOCAL_PATH --generation N`
enforces a generation precondition. If that object is no longer current or the
precondition fails, stop. Reading retained noncurrent generations requires an
authorized generation-addressed download, such as the viewer's signed exact URL
or Cloud Storage SDK `bucket.blob(object_name, generation=N)` with
`if_generation_match=N`. Pin the manifest download too. The comparison CLI itself
makes no remote calls and cannot attest how local generation evidence was acquired.

```sh
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
uv run python scripts/compare_releases.py \
  --baseline "$WORK_ROOT/comparisons/example/baseline.json" \
  --target "$WORK_ROOT/comparisons/example/target.json" \
  --output "$WORK_ROOT/comparisons/example/report.json" \
  --work-dir "$WORK_ROOT/comparisons/example/index"
```

Use a fresh index directory for each run. For a larger local comparison, explicitly
raise appropriate `--max-rows`, `--max-input-bytes`, `--max-expanded-bytes`,
`--max-disk-bytes`, and `--max-seconds` budgets after checking local capacity.
The 4-MiB schema/manifest and 900-KiB row bounds remain fixed. Reports are streamed
to disk without the viewer's 10-MiB response cap. Validation/resource failures exit
2 and do not export partial results. Progress and retained workspace paths are
printed; local bytes and indexes remain available for review and exact cleanup.

## Validation evidence

Unit tests cover all primary classifications, absent/null values, schema-only and
value changes, reordered rows, duplicate IDs/identity keys, invalid hashes,
source/generated compatibility, legacy evidence, localization exclusion,
checksums, correction generations, missing historical files, authorization,
resource limits, cancellation and export. Browser tests use the real engine and
viewer with synthetic pinned bytes, real MapLibre/PMTiles rendering in
the primary union map, automatic Before/After controls, actual polygon fill
colors across identity resets, property inspection, pagination, complete export,
keyboard operation, narrow layout and a delayed response. They do not establish
live IAP, CDN, generation retention, affinity or billing behavior.

Comparison and portable workspaces reuse the selected-release references and
artifact descriptor constructor in `release-reference.js`. Comparisons capture
source-language metadata, schema, and display tiles using the same exact path
and decimal generation semantics. The comparison input contract additionally
pins the release manifest, which workspace v1 does not include. A comparison
report is not a portable workspace lockfile.
