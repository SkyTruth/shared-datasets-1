# Scheduled source fingerprints

The four dataset-publishing jobs use `ingestion/common/source_fingerprint.py`.
A fingerprint is a SHA-256 of canonical UTF-8 JSON containing `version: 1`, a
source-specific `definition`, and all comparison `inputs`. Runs compare only
`source_fingerprint_hash`; normalized reads also retain the input payload as
`source_fingerprint` for evidence. An identical-byte shortcut can carry forward
the published normalized hash. Changing a definition requires a new definition
name. A raw download hash, edit
clock, release label, failed attempt or skipped check-in cannot establish source
equality on its own.

Only a completed publication is a comparison baseline. e-AMLIS resolves the
release index's `latest_release` run pointer, never `latest_run`. Sea ice and WDPA
use durable owned publication state and its successful receipt. MPAtlas verifies
the generation-pinned published manifest and canonical metadata. Existing
same-date completed requests remain idempotent; a rerun is not permission to
replace a release. A missing legacy fingerprint cannot establish equality for a
new release. A verified e-AMLIS baseline without a fingerprint publishes changed
content once; a broken baseline requires reviewed repair. Generated assets still
require their installed identity contract and approved reset/recovery path.

Canonical JSON ignores object-key order. Feature rows and translation CSV rows
are multisets of SHA-256 values sorted before hashing: row order is irrelevant,
but duplicate multiplicity matters. CSV headers are sorted independently of
column order. WDPA sorts fixed-size row hashes in a disposable SQLite database
with an 8 MiB cache rather than holding the full translation table in memory.
Retrieval timestamps, local paths, GCS generations and ZIP/gzip packaging do not
become content inputs. Generations and approved hashes still pin reads and writes;
they are authorization/integrity evidence rather than equality signals.

## e-AMLIS: `eamlis-content-v1`

Inputs are the ArcGIS layer URL, filter, service item ID, the sorted normalized
source field definitions (name/type/alias/length/nullability), and **every selected
feature's geometry and source properties**. Dates use the same day normalization
as publication. `OBJECTID` remains a source property and the published feature ID.
Geometry number spelling is normalized (`1` and `1.0` agree); coordinates retain
full numeric precision and vertex order. The CSV's fields and rows, maintained
translation fields and locales are also included.

The job downloads and validates the selected source and captures the translation
CSV generation before comparing. `describe` prepares canonical metadata and
reconciles translation reuse against it before hashing. `build` generates native
FGB/PMTiles artifacts and locale sidecars only for changed content. The successful
record and manifest name the fingerprint of that reconciled CSV, published in
the same bundle. The next attempt describes the same source and published CSV
with the same hash. Reconciled translation rows cannot trigger a redundant
release on the next check. The manifest retains the original captured CSV
generation and hash as provenance. `source_version` records the acquired GeoJSON SHA-256.
An unchanged record uses the attempt for `run_date` and the baseline release for
`release_date`, without writing dataset artifacts.

ArcGIS edit dates, maximum IDs/revision dates and row statistics remain diagnostics,
not skip authority. Identical statistics cannot hide geometry or attribute edits.
The old FGB-only skip was removed because unchanged FGB bytes cannot establish
unchanged localization inputs. A missing/invalid baseline metadata contract requires
reviewed repair before source acquisition. Existing partial dated artifacts fail
before a new comparison; an unchanged-source skip cannot hide them.

Limitations: paginated ArcGIS requests are not an upstream transactional snapshot.
Row counts and null geometry are checked, but same-count edits during pagination
can still yield a mixed snapshot. Polygon ring rotation/orientation and coordinate
sequence changes conservatively count as changes; no topological simplification
or tolerance is applied. Source properties retain their JSON value types.

## e-AMLIS after reviewed identity installation: `eamlis-keyed-content-v2`

The installed [permanent-key contract](eamlis-feature-identity.md) uses the same
complete content inputs, excluding only source `OBJECTID` values from feature
properties. That field remains preserved in published data. Source field
definitions, exact geometry and every other property still participate, so
renumbering alone skips publication while a key, attribute or geometry change
does not. The definition name changes at cutover, forcing the first new-contract
release. The captured translation inputs are remapped through pinned old-release
keys and hashes before comparison; the reconciled keyed CSV becomes the final
successful fingerprint input. Legacy releases keep `eamlis-content-v1`.

The first keyed release used `eamlis-keyed-content-v1`. Version 2 additionally
pins `geometry_hash_algorithm: sha256:canonical-geometry:v2`, forcing a refresh
on the next unused run date even if upstream content is unchanged. It retains
the same exact source inputs and the same permanent feature-ID ledger. Geometry
number spelling is normalized without rounding; this rule is shared with the
v2 published geometry hashes. Historical fingerprints remain unchanged.

## IMS sea ice: `ims-raster-v1`

Inputs are the resolved source URL, **complete IMS filename**, raster dimensions,
geotransform, coordinate-system definition, band types/nodata/color interpretation,
fixed-order decoded raster samples and the selected ice class. GDAL reads the
expanded TIFF and exports samples with fixed band-sequential ENVI layout; TIFF
compression, tile layout and incidental tags are excluded. Gzip retrieval/header
timestamps are excluded. No polygonization, vector artifacts, feature allocation
or publication runs on equality.

The filename is intentionally meaningful. A new day's file produces a new data
plane item even when its samples and resulting geometry match yesterday's.
The release date remains the documented valid date (filename date plus one day),
and `ice_date` remains the filename date. Feature identity continues to exclude
`ice_date` from its properties hash; fingerprinting does not change ID assignment.

A changed fingerprint under an already committed filename/date fails with
`changed source cannot replace or predate a published release`. The lifecycle
owns that refusal and the unchanged-source check-in. A historical success without
a fingerprint never proves equality; publication under its date requires reviewed
repair. Current deployed successes carry fingerprints and remain valid baselines
without `tools`. New successes record each native tool's resolved path and version.
The baseline's metadata contract is checked before source acquisition, including
when changed input would otherwise publish.

`run_date` is the filename lookup anchor. `release_date` is the documented valid
date on success, the baseline release on an unchanged check-in, and anchor date
plus one day when no source is available in the lookback window. The unavailable
date identifies the newest requested filename's documented valid date. Both skip
outcomes retain transient source-request warnings and update the release index.
Sea ice has no lost-object percentage limit because its polygons turn over daily.

Limitations: all raster classes count, including changes outside class 3. Such a
change can conservatively produce a release with equivalent ice geometry. Source
URL and CRS representation changes also conservatively count. ENVI sample bytes
use the pinned native GDAL toolchain (current supported architectures are little
endian). There is no tolerance for numeric/georeferencing changes.

## WDPA: `wdpa-retained-content-v1`

The scheduled `automatic` entrypoint builds and validates the current month,
then passes its immutable staged descriptor to publication. Fingerprints do not
replace compatibility/full-build validation or relax predecessor checks. Both
realms must validate before any new canonical write.

For an unpublished validated candidate, the worker verifies descriptor, generation,
size, SHA-256, both realm predecessors and allocations before publication. It then
reads the validated canonical/locale sidecars, schema and translation CSV. Each
realm fingerprint includes canonical and localized geometry hashes, source
properties and source identity keys; sorted source/schema field definitions;
translation CSV rows joined from generated `feature_id` to source identity key
and stored geometry/properties hashes;
maintained translation fields/locales; and identity contract.
Orphan translation rows retain their original key. Generated IDs, release dates,
provenance/retrieval fields and sidecar compression/order do not affect equality.
Canonical geometry hashes are the producer's validated geometry evidence.

If these inputs match the realm's latest committed success, the worker records an
owned skip/check-in and avoids FGB/PMTiles downloads and all publication artifacts.
Both realms' needed inputs are verified before the first new publication. A realm
already committed for the requested retained build keeps the existing exact
receipt/predecessor/byte verification and idempotent completion path, including
after reviewed translation edits. Legacy completed bundles still use that proof;
their absence of a new fingerprint does not permit a new-release content skip.

The source-processing `run.py` image is a diagnostic/retained-build producer,
not the production scheduled entrypoint. Retained artifacts already exist before
review, so this consumer comparison saves publication and large downloads, not
the separately approved build. A future build path must capture/compare its own
complete source and translation inputs before generating artifacts; this change
does not authorize or introduce one.

Limitations: the producer's geometry hash precision and normalization
remain authoritative. Changes to ZIP ordering, upstream month labels or build
telemetry or per-run identity-decision summary counts do not affect the
comparison. Published field value types, localized values and editorial CSV
fields do. Owned publication still validates the reviewed identity decisions.

## MPAtlas: `mpatlas-content-v1`

Inputs are the public v4 zone API URL and the multiset of published source
`feature_id` (`zone_id`), stored geometry hash and stored properties hash. The
source validation, null-geometry exclusion, GDAL MakeValid/multi-geometry
normalization and canonical JSON text for `affiliated_reports`,
`assessment_projects` and `classes` remain the monthly job's existing rules.
Original attributes are reattached after geometry repair so GDAL cannot coerce
timestamps or reinterpret JSON text. No retrieval or release dates enter the hash.

The baseline is the existing generation-pinned published manifest and its
hash-verified dated sidecar. Future manifests record `source_fingerprint_hash`;
legacy manual releases derive the same hash from their verified sidecars. An
identical source-byte SHA-256 can skip source normalization too, provided that
published source evidence binds it to the current normalized fingerprint. A
changed fingerprint definition disables that shortcut. Reordered or reformatted
responses still normalize and compare equal without generating publishable
artifacts. Both unchanged paths write the run/check-in only.

Limitations: source geometry/properties hash normalization remains authoritative;
coordinate and nested-array ordering are retained. Null-geometry rows are
excluded from the published footprint. The current asset has no localization
sidecars. Missing/corrupt baselines and partial dated publications retain their
existing reviewed-repair boundary. Source timestamps that are actual published
attributes remain meaningful; retrieval provenance does not.

## Check-ins, failures and recovery

Every meaningful unchanged check updates `_catalog/releases/{asset-slug}.json`;
`latest_release` and all dataset artifact generations stay unchanged. e-AMLIS and
MPAtlas write a no-clobber dated `runs/` record. WDPA and sea ice capture the skip
payload in an immutable owned transaction receipt and update `latest_run` through
that transaction; they do not write over a successful dated run record. Index
`updated_at` is the check-in time and `latest_run.date` is the attempt date
(`run_date`), while the skip payload retains the attempt date and relevant
release date.

Owned transactions resume **before** new equality decisions. A different owner
cannot turn an incomplete publication into a skip, abandon its claim, or reuse its
reserved IDs. Captured intents and checkpoints continue to replay their original
bytes. A failure after e-AMLIS/MPAtlas's immutable run write retries the index
update from that same record. Other partial unowned publications require reviewed
repair; changing source inputs is not an overwrite escape hatch.

Focused regressions cover content/ordering equality, geometry and attribute
changes, localization and schema changes, sea-ice filenames, required native
raster repackaging, and interruption at each durable owned publication/skip write.
