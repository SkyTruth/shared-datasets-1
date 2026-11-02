# Feature inspection and identity review

Owner: the catalog feature-inspection chat, branch
`codex/catalog-feature-lookup`. This chat owns final integration for this series.
Each PR starts from current main after its prerequisites merge. No dataset
identity decision, publication, or production infrastructure change is part of
these UI and evidence improvements.

## PR 1: Find a published feature

Add a feature ID field beside the selected release and display the record's
source attributes in the existing inspector. Reuse release-bound metadata
lookup for public and authenticated catalogs. Copy a link containing the asset,
concrete release, feature ID, and canonical metadata generation. Reject missing
releases and changed generations instead of opening another snapshot. Keep
source keys such as WDPA `SITE_PID` distinct from published `feature_id` values.

Acceptance: find an existing and absent ID; open a historical feature link;
reject an unavailable release or replaced metadata; discard a late response
after changing datasets/releases; preserve restricted-data authorization;
support keyboard and narrow-screen use. This first PR inspects attributes;
it does not locate arbitrary IDs on the map or expose unpublished records.

Likely overlap: `web/catalog/app.js`, `index.html`, `styles.css`,
`release-reference.js`, browser acceptance tests, and the catalog contract.
No prerequisite PRs.

## PR 2: Capture identity review evidence

When ingestion pauses, retain a versioned, release-scoped review bundle with
each incoming record, all candidate previous records, their source attributes
and stored footprints, the exact hash evidence, and the previous feature ID
already owned by the incoming source key (including explicit absence).
Record upstream input identity and generation-pinned baseline artifacts so the
evidence cannot drift between review and retry. Keep evidence separate from
canonical published releases; do not relax the identity gate or decide by
geometry alone. Define storage, access, retention, and size limits before
adding a writer. Provide a read-only local report for immediate review.

Acceptance: two candidates sharing a footprint remain distinct; existing key
ownership limits legal actions; every changed field is shown; missing evidence
fails explicitly; the paused run leaves published data untouched. A report is
evidence for a maintainer, never an identity resolution itself.

Depends on PR 1 for the eventual link contract, but evidence construction can
be developed independently. Likely overlap: shared identity/sidecar code,
WDPA ingestion, identity tests, and the resolutions guide. Any required IAM or
deployment prerequisite must be a reviewed, protected change before activation.

## PR 3: Review cases from the alert

Add a read-only catalog view of one captured case: old/incoming source fields,
highlighted differences, overlaid footprints, and existing key ownership.
Show the evidence-supported legal actions and explain their effects. Link
each Slack case directly to this view and link published candidates through
PR 1's feature links. Missing, expired, or inaccessible evidence must be
explicit; ordinary published browsing remains available.

Depends on PRs 1 and 2 and any protected deployment prerequisites. Likely
overlap: catalog UI/viewer routes, identity alert formatting, browser tests,
and the resolutions guide. Keep decision authoring/review in the existing
resolution-file PR workflow; opening a case must never publish or approve it.

Acceptance: follow a synthetic alert through the actual viewer to both records
and map; check key ownership and field changes; reject cross-asset/release
mixups and unauthorized reads; preserve exact evidence links through retries.

## Integration and validation

Use focused tests while editing. Before each first push, commit the focused
scope, freeze the checkout, run complete preflight against current main, and
record the evidence path. Reconcile overlapping files and prerequisite PRs
before the final validation boundary. Open each PR only when its implementation
and validation are ready for review; do not open placeholder PRs for later work.
