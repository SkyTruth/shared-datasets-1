# Compare releases

The Version row sits directly below the primary map. Click **Compare releases**
beside Version to enter comparison mode; the button is hidden for single-release assets. The single
Version dropdown becomes **Before** and **After**, with the selected release and
its immediate predecessor as defaults. Comparison starts automatically and
reruns when either selection changes. The same button becomes **Close comparison**;
closing restores ordinary browsing. There is no second run button or map-mode
selector. The union occupies the primary map at the top of the detail view,
retaining its viewport. Before is red and After green. The selections, Close comparison, and right-aligned
**Details** affordance share one row. Details start collapsed and contain compact
release, change, and schema tables; progress and failures remain visible. Selecting
a map feature shows its property details below the map, independently of the
collapsed comparison summary. **Use this dataset** sits directly above
the canonical paths.

The authenticated catalog viewer performs complete comparisons. A static catalog
without the endpoint explains availability, keeps browsing/downloads intact, and
supports visual release inspection and the local CLI route. Missing canonical
metadata, schema, or manifest artifacts prevent an exact comparison; they do not
trigger a latest fallback.

The result contains six mutually exclusive feature counts, separate schema
changes, a searchable table with 50 rows per page, before/after source properties,
and publication provenance. Complete JSON reports remain available through the
CLI and API; the viewer has no export button. Selecting
a table row opens paired properties for its comparable ID. Clicking the map
shows every overlapping polygon from both release layers in separate **Before**
and **After** tables, labeled with their release dates. `Absent` differs
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
matching and paired table inspection. Individual map hits remain inspectable
from their own pinned release sidecars. They still allow geometry colors and
schema/publication comparison; geometry lookups never join feature IDs across releases.
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
Changed fills, outlines, lines and points draw above all unchanged geometry from
both releases, so later gray layers cannot obscure the red, green or yellow marks.

Click **New geometry**, **Removed geometry**, **Metadata changed** or **Unchanged**
in the legend to show only that category and zoom to its display geometry. The
selected button is pressed; click it again to restore the full union. Map clicks
ignore hidden categories, including transparent overlapping gray points. The
feature table also filters by geometry membership when feature IDs are comparable;
a moved ID can belong to both the new and removed categories. Category selection
survives a basemap change and resets when either release changes or comparison closes.

Category zoom visits the union overview to include items outside the current
viewport, then fits matching loaded display geometry. It retains bounds seen at
finer zooms. This uses PMTiles geometry without reading canonical FGB or increasing
the comparison disk budget. Overview tiles may omit fine features; if no matching
display geometry is available, the viewer explains that finer tiles need inspection.
An empty category hides all features without moving the map.

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

Map inspection retains every overlapping release layer, including transparent
Before geometry when an exact shared polygon is drawn from After. Identical
properties do not merge hits across releases. Each hit loads source-language
metadata using its captured path and generation, without joining a reused ID to
another release. Map clicks remain available while the comparison runs and on
static viewers without comparison jobs. Clicking empty space, changing either
release, closing comparison or rebuilding the map clears the selection; delayed
metadata cannot restore a superseded inspector. Once both clicked hits load source
metadata, changed fields and their values are highlighted yellow. Pairing uses
identical canonical geometry hashes across the two releases, including historical
v1 geometry recovered by the engine; it never assumes reset IDs identify the same
feature. Missing fields differ from explicit nulls; object key order does not
create a change. Hash-excluded operational fields stay unhighlighted. Paired table
inspection also highlights its source-property changes.

Tiles simplify geometry and visibility varies with zoom. Missing or unauthorized
historical tile generations produce visible errors. Map signer and comparison
responses must match the selected paths/generations before use. Comparison report
schema version 3 also pins optional canonical FGB input for historical v1 readers.

## Published historical v1 releases

The June 6 coral release predates separate `geometry_hash`/`properties_hash`.
Its combined `feature_hash` cannot establish whether geometry changed. The reader
accepts only the declared v1 schema/manifest/hash contract and streams its exact,
generation-pinned canonical FGB. It validates every release-scoped ID, properties,
feature count, byte count and SHA-256 checksum against the sidecar and manifest.
It preserves stored coordinate precision, scans sequentially without loading the
whole archive, and never hashes simplified display tiles. Historical `ext_id`,
`feature_id`, and `feature_hash` are bookkeeping rather than source changes.
The v2 writer contract is unchanged. The historical reader stays necessary while
these citable releases remain available; removing it requires migrating their
comparison evidence or retiring this explicitly supported reader capability.

WDPA Marine's June 5 and June 7 releases also use v1; June 9 and later releases
use stored separate hashes. June 7's canonical FGB is 1,293,973,624 bytes, which
the former 512-MiB stream ceiling rejected before opening it. The reader now
allows a bounded 2-GiB stream per release and a ten-minute complete-job deadline,
with 8-MiB generation-pinned GCS reads. These allowances do not allocate that
amount of memory or copy the FGB to the job workspace.

FlatGeobuf omits null-valued property entries. The historical boundary therefore
compares the projected properties with null entries omitted on both sides, while
retaining exact feature hashes, IDs, counts and archive checksum validation.
Source-property hashes and inspection still distinguish missing fields from null.
The previous reader falsely rejected WDPA's null `GIS_AREA` and `GIS_M_AREA` values
as a metadata mismatch.

Read-only checks on October 2 against pinned published inputs established:

| Asset / Before → After | Rows Before / After | Local viewer job | Workspace size | Unique geometry changes |
| --- | ---: | ---: | ---: | --- |
| Coral / 2026-06-06 → 2026-06-10 | 18,429 / 18,429 | 47.44 s | 19.6 MiB | 14 new, 14 removed, 18,409 unchanged |
| WDPA marine / 2026-09-30 → 2026-10-01 | 17,648 / 17,938 | 4.24 s | 15.1 MiB | 296 new, 6 removed, 504 metadata, 16,633 unchanged |
| WDPA marine / 2026-06-07 → 2026-10-01 | 17,657 / 17,938 | 165.36 s | 14.1 MiB | 842 new, 556 removed, 572 metadata, 16,019 unchanged |
| EAMLIS / 2026-10-01 → 2026-10-02 | 64,386 / 64,400 | 17.02 s | 45.8 MiB | 4 new, 2 removed, 22,272 metadata, 1,226 unchanged |

These timings run the real viewer job code with already downloaded, pinned
canonical inputs. Workspace sizes include its downloaded sidecars and contracts. Coral's historical FGB is
329,582,712 bytes; local downloading took about 294 seconds, outside this cached
comparison measurement. The June 7 WDPA comparison also completed through the real
viewer GCS readers with default budgets in 273.77 seconds, using 14.1 MiB of
workspace files and 905.4 MiB peak process RSS. Both release-scoped map lookups
returned canonical hashes and categories. This read pinned June 7 FGB generation
`1780838301984256` and October 1 sidecar generation `1790866699087905`.
June 5 → June 7 also completed through the real GCS readers with default budgets
in 476.91 seconds: 17,657 features per side, 13.5 MiB workspace and 944.4 MiB peak
process RSS. It verified both complete historical streams at FGB generations
`1780651675251674` / `1780838301984256` and both map lookups, yielding no new or
removed geometry, 470 metadata changes and 16,677 unchanged unique geometries.
Cloud Run CPU timings remain unverified. September WDPA and EAMLIS use sidecars
only. Their classifications depend on IDs and hashes,
not expanded property payloads. The previous full-record SQLite representation
made EAMLIS exceed the 128-MiB budget: its two approximately 8.3-MiB compressed
sidecars each expand to about 100 MiB. The compact index removes that expanded
copy without raising any limits. WDPA and coral classifications are unchanged.
EAMLIS paired inspection took 0.035 seconds; a complete source-property search
for `KEDAS MINE` took 12.33 seconds and found five IDs. Search scans source
properties on demand rather than storing them in the comparison index.

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
GET /api/comparisons/{job_id}?offset=0&limit=50&query=&classification=&geometry_change=
GET /api/comparisons/{job_id}?feature_id=1
POST /api/comparisons/{job_id}/map
POST /api/comparisons/{job_id}/cancel
GET /api/comparisons/{job_id}/report
```

`GET /api/comparisons` reports capabilities and limits. Start takes `slug`,
`baseline`, `target`, and `expected`, with baseline/target role dictionaries of
`{path, generation}` for metadata/schema/manifest, available PMTiles, and canonical FGB when present. Start
returns `202` with a job ID and pinned inputs. Polls return state/progress and,
on completion, summary plus a bounded page when feature identity is comparable.
`POST /api/comparisons/{job_id}/map` takes `side` (`baseline` or `target`) and
1–200 unique `feature_ids`, with a 16-KiB request cap. It returns geometry colors
and canonical `geometry_hash` within that release even when cross-release feature
identity is incompatible. Summary `property_hash_exclusions` includes exclusions
from both manifests regardless of feature-ID compatibility. Optional page
`geometry_change` accepts `novel`, `removed`, `metadata_changed`, `unchanged` or
empty, and intersects with search and feature classification filters.
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
| Bytes per downloaded sidecar/schema/manifest | 64 MiB |
| Historical canonical FGB streamed per release | 2 GiB |
| Individual FGB feature | 64 MiB |
| Schema/manifest contract each | 4 MiB |
| Expanded bytes per sidecar | 256 MiB |
| Workspace files per job | 128 MiB |
| Computation/download deadline | 600 seconds |
| Individual sidecar row | 900 KiB |
| Page size | 100 maximum; UI uses 50 |
| Response or complete report | 10 MiB |
| Start body | 16 KiB |
| Concurrent jobs per instance | 2 |
| Retained jobs per instance | 8 |
| Job retention | 15 minutes from start |

The set comparison is inexpensive. The task-scoped SQLite index stores IDs,
identity keys, binary 32-byte geometry/property hashes, and offsets into the
uncompressed metadata stream. It does not store complete JSON records or source
properties. The verified compressed sidecars remain the source for inspection
and search. Inspection seeks to an indexed offset and parses just that row;
gzip may decompress earlier bytes but does not parse their JSON. Search streams
both complete sidecars and temporarily retains only matching IDs in memory,
preserving property search, exact totals, classification filters and pagination.
Detail reads recheck the pinned byte evidence before using retained inputs.
Each page/inspection request has its own computation deadline, so completed jobs
remain usable for their full retention period; cancellation still applies.

These budgets bound complete input validation, compressed inputs and the compact
index on a 2-GiB Cloud Run instance. Current releases use their stored hashes and
never read canonical geometry. Historical v1 releases need the separate geometry
pass described above; temporary hashes verify the FGB's full projected properties
and combined feature hash without retaining expanded metadata. No row sampling or
truncation is used.

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
sidecar, schema and manifest for current releases. Historical v1 requires an
`fgb` descriptor and matching `local_paths.fgb`. Available PMTiles descriptors may
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

Use a fresh index directory for each run. Keep the pinned local sidecars available
and unchanged while using the comparison's search/inspection methods.
For a larger local comparison, explicitly
raise appropriate `--max-rows`, `--max-input-bytes`, `--max-expanded-bytes`,
`--max-disk-bytes`, `--max-geometry-bytes`, and `--max-seconds` budgets after checking local capacity.
The 4-MiB schema/manifest and 900-KiB row bounds remain fixed. Reports are streamed
to disk without the viewer's 10-MiB response cap. Validation/resource failures exit
2 and do not export partial results. Progress and retained workspace paths are
printed; local bytes and indexes remain available for review and exact cleanup.

## Validation evidence

Unit tests cover all primary classifications, absent/null values, schema-only and
value changes, reordered rows, duplicate IDs/identity keys, invalid hashes,
source/generated compatibility, legacy evidence, localization exclusion,
checksums, correction generations, missing historical files, large property
payloads under a small workspace budget, changed local detail bytes, retained-job
detail deadlines, authorization,
resource limits, cancellation and export. Browser tests use the real engine and
viewer with synthetic pinned bytes, real MapLibre/PMTiles rendering in
the primary union map, automatic Before/After controls, actual polygon fill
colors across identity resets and historical v1, collapsed details, property inspection, pagination,
keyboard operation, narrow layout and a delayed response. They do not establish
live IAP, CDN, generation retention, affinity or billing behavior.

Comparison and portable workspaces reuse the selected-release references and
artifact descriptor constructor in `release-reference.js`. Comparisons capture
source-language metadata, schema, and display tiles using the same exact path
and decimal generation semantics. The comparison input contract additionally
pins the release manifest, which workspace v1 does not include. A comparison
report is not a portable workspace lockfile.
