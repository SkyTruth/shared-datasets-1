# Reviewed dataset mutation documents

Canonical mutation authority is a checked-in JSON document added by a same-repo
PR merged to `main`. The readable PR fences are generated review copies; editing
a PR body never changes executed mutations.

From concierge evidence, `uv run python scripts/publishing_concierge.py render-pr
--state-file "$STATE_FILE"` writes the document and prints the PR body. Include
the generated `.github/dataset-plans/{asset}/{proposal}/{sha256}.json` file in the
PR. For manually prepared payloads, delete-only or combined changes:

```bash
uv run python scripts/reviewed_dataset_plan.py prepare --publish publish-plan.json
uv run python scripts/reviewed_dataset_plan.py prepare --delete delete-plan.json
uv run python scripts/reviewed_dataset_plan.py prepare --publish publish-plan.json --delete delete-plan.json
```

These local commands write files and render fences; they grant no approval and
perform no Git or remote operations. After `render-pr`, concierge `validate`
checks the saved file bytes/hash against its current evidence. Unchanged rerenders
are supported. If the file or evidence changed, rendering refuses: start a fresh
concierge proposal, or prepare a replacement payload with
`reviewed_dataset_plan.py prepare` for a new reviewed PR. Do not edit the saved
state record to bypass the mismatch.

The document has `plan_version: 1`,
`finalization_version: finalize-promoted-release-v1`, and `publish` and/or `delete`
payloads. Both payloads must name the same asset/proposal and have disjoint
mutation targets. Existing metadata, schema waivers and explicit release-index
targets remain supported. Schema ownership is `scripts/reviewed_dataset_plan.py`;
unknown fields/versions, duplicate JSON keys, duplicate targets and ambiguous
fences are rejected. Serialization is UTF-8 JSON with sorted keys, two-space
indentation and a trailing newline; its SHA-256 is the filename.

Add exactly one document per PR. Existing merged records are immutable: a changed
plan produces a new digest file, not an edit to the old record. The gate verifies
identical regular-file bytes at the exact PR head and actual merge revision.
Non-self-authored PRs require the latest effective `jonaraphael` approval on that
head; later changes-requested or dismissed decisions fail. Comments/pending
reviews do not confer approval. Merged PRs authored by `jonaraphael` retain the
explicit self-acceptance exception. Restricted dispatch still requires a merged
PR and the same checks.

The gate captures one versioned authorization artifact. Apply verifies its hash,
source revisions and current acceptance, then runs the exact captured executor
SHA. Automatic localization verifies the upstream repository, workflow ID/path,
run/attempt, outcome and artifact before using the same payload. Ordinary merged
code PRs produce an explicit `no_mutation` result. Missing or expired artifacts
are errors, never a no-op or a reason to read current PR prose.

## Existing proposals and retries

The three pre-launch generated-ID assets also support a constrained
`publish.identity_reset` subtype containing only the complete reset `inventory`. It accepts exactly the evidence/adoption/state promotions
with create-only destination expectations and explicit JSON/no-cache metadata.
It cannot be combined with deletion, index rebuilds, or compatibility waivers.
The same immutable document and publish fence apply, but execution routes to the
dedicated protected reset job rather than generic promotion/localization.
The installer-owned `publications/reset.json` journal is deterministically bound
to that authority. See the [installation contract](../../docs/feature-id-reset-installation.md)
for prerequisites, staging, review, ordering, and recovery. The installer rechecks the affected job is paused and drained before each write,
using existing project identities; no organization IAM evidence is required.

For an open body-only PR, save its body locally and run:

```bash
uv run python scripts/reviewed_dataset_plan.py prepare --body pr-body.md
```

Add the resulting document, regenerate the fences and obtain approval on the new
head. Existing approval on an older head does not carry forward. A merged
body-only plan needs a new reviewed PR; there is no retrospective body-edit
migration. Historical prose remains readable for audit.

After partial execution, inspect exact current generations before preparing any
replacement proposal. `publishing_concierge.py refresh-retry-plan` only prepares
an **unapproved** payload; changed expectations or waivers require new reviewed
bytes. Never replace the authorization artifact or edit a merged body to resume.
Current per-object CAS can refuse a repeat after partial progress. Durable
byte-proven transaction recovery is a separate publisher contract.

The authorization `proposal_key` hashes repository identity, PR number, reviewed
head, merge and plan digest. `execution_contract_sha256` additionally binds the
executor SHA and finalization version. Run/attempt identity is recorded separately.
A publisher implementing receipts must look up the existing proposal before
planning: a different executor contract refuses that transaction, even when the
version string is unchanged. Use the original pinned executor for an exact retry;
never create a second transaction with refreshed destination expectations. The
shared fixture is `tests/fixtures/dataset-mutation-authorization-v1.json`.

## Rollout limits

This code does not change GitHub rulesets, environment reviewers or historical
workflow definitions. Enforcing the documented review settings and controlling
reruns of old workflows are rollout prerequisites. Current acceptance is checked
just before mutation, but GitHub review changes and GCS writes are not one atomic
transaction. Publication receipts/finalizer recovery and distributed localization
output races remain separate work; this document does not claim to fix them.

## Owned WDPA build promotion

The single-build WDPA rollout uses an immutable document at
`wdpa-build-{build-bundle-sha256}.json` in this directory. It contains
`schema_version: 1`, `kind: wdpa_owned_build_promotion`, the exact bundle reference
(URI, generation, size, SHA-256), cloud execution/image, image configuration
digest, original producer source fingerprint, run date and measured
realm summaries. The bundle hash transitively binds every staged file reference,
validated output fact and captured identity baseline. Include its full JSON in a
`shared-datasets-publish-plan` fence and record the evidence PR number in version
3 `catalog/wdpa-processing-acceptance.json`. Add one document per PR; never edit a
merged document or change authority through body edits.

This subtype routes through `wdpa-monthly-deploy.yml` and
`scripts/wdpa_build_authorization.py`, not the generic unmanaged object writer.
The gate verifies same-repo merged-to-main status, exact-head effective approval
(or the existing self-authored merged exception), the added regular-file plan,
identical reviewed-head/merge/current bytes, the matching readable fence and the
merge's ancestry. The worker checks exact staged generations and hashes, both
live predecessor manifests/counters and existing ownership before new writes.
Canonical destinations remain the fixed WDPA dated/latest artifacts, run records
and release indexes. Their generation expectations are captured by the owned
publisher after those predecessor checks, preserving the original receipt and
reservation recovery contract. The immutable dataset files are never rebuilt.
A dispatch cannot select an unreviewed bundle or a different run date.

The producer fingerprint identifies the code that built the retained files,
independently of subsequent reviewed publication fixes. The protected deployment
verifies the producer image's configuration and fingerprint before adding its
small publication-only software layer. An already-committed realm is checked
against the frozen predecessor and owned source/identity contract and left
unchanged; its regenerated candidate hashes do not authorize replacing it.
