# EAMLIS permanent feature identity

ArcGIS `OBJECTID` is unique inside a download but is reused and renumbered
between downloads. The deployed legacy releases therefore cannot establish
problem continuity from `feature_id`. The code supports a separately reviewed
cutover to `eamlis-problem-key-v1`. Merging or deploying this code alone does not
perform that cutover. Read the concrete release manifest to determine its
identity contract; an old and a new numeric ID with the same spelling can refer
to different records.

## Evidence and decision

The [audit report](eamlis-identity-audit-2026-10-27.json) records checksums,
generations and row counts for 19 releases, April 30 through October 27, 2026.
Eighteen supply `PROBLEM_KEY`; April 30 does not and is explicitly unsupported.
All supplied nonempty problem keys are unique within each audited release.
The same 25 distinct area records have no problem keys or populated problem-side
columns throughout the keyed history. No problem row shares their area keys.
The resulting permanent ledger contains 64,526 problem assignments and 25 area
assignments. It retains 57 keys absent from the latest release; none reappeared
in the observed history. Its next allocation is 64,552.

The latest audited release has 64,494 rows and 57 source columns. Percentages
below use the complete row count; domination is the most frequent nonempty
value's fraction. Exact profiling output is retained in the audit report.

| Candidate | Type | Distinct | Distinction | Empty | Domination | Skew | Top examples (counts) | Concern |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `OBJECTID` | integer | 64,494 | 100% | 0% | <0.01% | 1.00 | 1, 2, 3 (1 each) | Reused and renumbered across releases |
| `PROBLEM_KEY` | string | 64,469 | 99.96% | 25 / 0.04% | <0.01% | 1.00 | AL0003422, AL0003423 (1 each) | Missing on area-only records; no published immutable-key guarantee |
| `AMLIS_KEY` | string | 24,703 | 38.30% | 0% | 0.48% | 117.59 | PA942003 (307), PA942069 (243) | Groups multiple problems; cannot identify every row |
| `NUMBER` | integer | 307 | 0.48% | 25 / 0.04% | 37.94% | 116.54 | 1 (24,472), 2 (12,982) | Repeats and changes; cannot reconstruct `PROBLEM_KEY` |

OSMRE's [data dictionary](https://www.osmre.gov/programs/e-amlis-data-dictionary)
defines the area key as a state/tribal prefix and assigned six-digit problem-area
number. Its [May 2025 instructions, page A-26](https://www.osmre.gov/sites/default/files/2025-05/AML-1_Appendix-A-May-2025.pdf)
describe one-time use of a problem-area number. This supports treating an area
as an entity, but does not guarantee that hosted `PROBLEM_KEY` values are
immutable or never reused. The choice of a problem key is supported by observed
history, with continuing validation. We observed 87 `NUMBER` changes for a
retained problem key; 2,564 latest keys differ from concatenating `AMLIS_KEY`
and `NUMBER`. Names, geometry, costs and guessed concatenations are not keys.

The approved assignment choice for the proposed contract is the typed pair
`(problem, exact PROBLEM_KEY)` or `(area, exact AMLIS_KEY)`. Area classification
requires every defined problem-side column to be explicitly empty. A zero cost
or quantity is populated. A missing column, malformed key, duplicate typed key,
area-only/problem coexistence for one area, or a problem key rebound to another
area stops the job. This cannot detect upstream reuse for a different problem
within the same area; source guidance or a future authoritative identifier
would be needed to eliminate that uncertainty.

## Allocation and publication

The producer assigns monotonic decimal string IDs to the typed pair. Canonical
FGB/metadata preserve every source property and add `eamlis_record_kind` and
`eamlis_identity_key`. Co-located problems retain separate IDs. `OBJECTID` is
preserved for diagnosis but excluded from the new semantic property hash and
content fingerprint. Geometry remains exact: tiny coordinate differences still
count as geometry changes. No rounding or deduplication changes the source.

Every allocation remains in an append-only ledger, even if its record disappears.
Under the existing source-quality publication claim, the job writes a create-only,
content-addressed ledger at
`{asset-root}/publications/identity-ledgers/{sha256}.json`, then selects its pinned
reference with a generation-preconditioned control write **before** a builder can
use the IDs. An unselected orphan object is not allocation authority. Failed
publication never makes selected IDs available for reassignment. Manifests record
the allocation reference and sequence high-water; release/run indexes carry
`identity_contract`. Cross-contract feature-ID comparisons are rejected by the
existing comparison engine; immutable legacy releases remain available.

Control schema v1 means legacy identity. Installed schema v2 includes the selected
ledger, initial ledger, installation ID, explicit first release and activation
evidence. The immutable `{asset-root}/publications/identity-installation.json`
marker is written before the v2 control. A marker with absent/v1 control blocks
legacy publishing without changing its reviewed generation. Missing/corrupt
selected state cannot silently bootstrap IDs. Exact installation retries recognize
an existing append-only installation and never replace or rewind it.

## Reviewed operational cutover

This code PR contains no installation plan and changes no remote data. Actual
installation needs a later maintainer-directed immutable plan PR.

1. Deploy the tested code through the existing protected EAMLIS deployment path.
   Then pause `eamlis-monthly` with the
   [schedule-control workflow](../.github/workflows/ingestion-schedule-control.yml) and wait for
   all pending/running executions to finish. The installer verifies this itself
   with the existing deployer identity, including every execution-list page.
2. Replay the pinned local inventory with `scripts/eamlis_identity_audit.py`.
   The inventory contains `date`, `format` (`fgb`/`metadata`), `local`, `rows`,
   `path`, `generation`, `size`, and `sha256` per source. Use a named standard
   temporary workspace. Refresh the evidence if the published baseline changes.
3. Choose an unused `RUN_DATE` after the complete baseline. Stage the installation
   sources and generate its immutable document/fences:

   ```bash
   uv run python scripts/eamlis_identity_transition.py stage-plan \
     --inventory "$WORK_ROOT/verified-inventory.json" \
     --directory "$WORK_ROOT/installation" \
     --first-release YYYY-MM-DD --proposal-id eamlis-key-cutover
   ```

   This writes only three immutable scratch sources plus a local plan document.
   It requires an existing unowned v1 control, complete baseline, pinned current
   metadata/CSV, and coverage of every indexed historical release. Review the
   audit, proposed mapping, first date and exact generation expectations. Add
   the generated `.github/dataset-plans/` document and matching publish fence
   to a focused PR. Obtain the normal exact-head approval/merge authority.
4. The protected installer routes `publish.identity_transition` away from generic
   promotion. It rechecks PR authority and paused/drained jobs before each write,
   recomputes the full ledger from pinned historical bytes, verifies current
   metadata/CSV against their concrete legacy manifest, and installs ledger,
   marker, then control with create/CAS preconditions. Only the ledger generation
   placeholders in the reviewed control template are finalized. It publishes no
   FGB, tiles, catalog or release data. No new IAM or local Terraform apply is
   required.
5. Keep the schedule paused. On the maintainer's explicit instruction, execute
   the already deployed, verified EAMLIS Cloud Run job once with a `RUN_DATE`
   execution override matching the reviewed first release. Verify the deployed
   image/revision through the normal scheduled-ingestion procedure before running.
   A replay of an already completed deployment deliberately skips its canary;
   it does not request this new execution. Prepared state
   accepts only the chosen date. The existing source-quality hold remains fully
   enforced: if held, inspect the exact staged candidate in the private catalog
   and approve its separate normal held-candidate plan. A healthy download cannot
   release a hold. Verify new IDs, all area-only records, exact geometry, translation
   migration counts, manifest/ledger pins and the completed release index.
6. Once the first release is active, update the asset document's structured
   `feature_identity` to the generated strategy, assignment columns and contract,
   describe the two new columns, then regenerate catalog docs and publish the
   README/catalog through their normal approved route. Keep the legacy declaration
   while legacy data is live. Resume through the schedule-control workflow.
   Its EAMLIS resume gate requires
   active installed identity and complete pinned publication evidence. A later
   source-quality hold can remain, allowing scheduled checks to continue while
   publication is held. Prepared or incomplete installation cannot resume.

If installation fails, retry the exact reviewed workflow after investigating.
Identical immutable bytes and the original unchanged control generation permit
completion. Changed control, baseline, sources, missing selected state, or a
different installation require investigation and fresh reviewed recovery; never
delete the marker or synthesize a new ledger. A failed data publication retains
the claim/hold and all allocations. Resolve its actual partial-publication boundary
through the normal reviewed recovery path. An index commit with a lost activation
response can reconcile only after full pinned manifest/artifact/metadata checks.

## Translation continuity

The installation pins the matching **dated** legacy CSV and canonical metadata,
verified against the old concrete release manifest. The first keyed run maps
old feature ID → that metadata's typed source key → new feature ID, requiring
the exact field and source-value hash. It preserves matched values, review state
and provenance and writes `identity-translation-migration.json` in the run's local
work directory/logs its counts. Historical orphan or stale rows cannot safely
be assigned using old numeric IDs: they are counted as archived/unmapped and
remain preserved in the immutable old CSV/release. They are not imported under
potentially unrelated new IDs. Source text remains fallback for translation debt.
Subsequent releases use normal permanent-ID reconciliation. There are no new
translation-provider calls or changes to the declared Spanish `PA_NAME` scope.

After activation, plain reviewed manual edits are restricted to the complete
current CSV/Spanish sidecar/manifest bundle while preserving identity and core
artifact references. New dataset bytes require the exact held-candidate path.
Generic tools cannot replace or delete identity control/ledger protocol objects.

## Engineering boundary

Invariant enforced: one permanent decimal ID per exact typed source key; no reuse,
renumbering, guessed identity or implicit cutover. Boundary changed: source
classification, serialized ledgers/control, manifest validation and protected
installation. Code removed: private metadata-constructor duplication was avoided
by exposing the shared constructor. Internal handling removed: no row-level
fallback can guess an ID. Fallbacks added: none for ambiguous identity; legacy v1
remains solely for deployed persisted compatibility until explicit migration.
Fallbacks rejected: location matching, `NUMBER` reconstruction, new genesis after
state loss and silent CSV numeric-ID carryover. Deletion candidates left in place:
the live v1 producer, required until installation. Remaining uncertainty: upstream
key reuse within an area, incomplete upstream pagination snapshots and exact
submillimeter geometry changes.
