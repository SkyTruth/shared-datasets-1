# Tested production deployment contracts

Main-push CI validates the release revision before automatic production jobs are
called. Ingestion deployments receive `executor_sha`, `source_run_id` and
`source_run_attempt`. They first check out the immutable main workflow revision
and use its trusted verifier to prove the candidate's repository ID, CI workflow
ID, main-push event and successful `ci-ready` job through GitHub's API. Only then
do they check out the tested executor with complete history and authenticate to
GCP. Candidate code cannot replace the verifier before authorization. Automatic
callers must use their current run attempt; inputs alone are not authority.

`uv run python scripts/release_contracts.py --target all` is the offline contract
check shared by local preflight and CI. It validates immutable reset plans,
retained WDPA acceptance evidence and its checked-in file hashes, quota approval,
permission declarations, bootstrap ordering, scheduler bundle defaults and
protected deployment workflow boundaries. It does not claim that a checked-in
reset has been installed or that an IAM grant has propagated. Trusted protected
jobs check live publication state and deployment identity permissions before
mutation. Safe permission reads have bounded propagation checks; API/network
errors and missing authority remain failures.

`uv run python scripts/production_image_contracts.py` builds the actual Linux
production Dockerfiles and exercises installed entrypoint imports and native
CLIs. It checks NumPy/GDAL using `gdal_calc.py`'s actual shebang interpreter,
executes the WDPA input probe's installed CLI, and runs the small real production
fixture. It neither publishes images nor accesses datasets. EAMLIS and sea-ice
images use the locked Python dependencies and the pinned native versions.
The production-image CI suite retains the exact EAMLIS, sea-ice and viewer
images after these checks. Its result binds the complete image manifest to the
tested SHA, config IDs, archive hashes and ordered rootfs hashes. Deployment
loads only the selected image from the successful source CI attempt; it never
rebuilds it. Docker-save exporter formats are normalized without changing config
or rootfs bytes. Before Terraform mutation, the pushed registry manifest must
reference the tested config ID; Terraform uses that immutable registry digest.
Missing, expired, incomplete or mismatched evidence fails closed.

WDPA distinguishes the tested executor revision, the accepted producer image and
fingerprint, and the retained bundle's generation/hash. Publication fixes inherit
the accepted producer and update its consumer layer; they never regenerate the
accepted dataset bytes. Changes to staged producer evidence may launch the
isolated validation deployment. That path cannot automatically launch the
production publisher or bypass complete acceptance.

Production Terraform jobs retain the `prod-terraform-state` non-cancelling queue
from plan through resource allowlist, saved-plan apply and verification. The
caller never holds that same queue. The main pipeline invokes shared ingestion
IAM once, before dependents; the secret authority bootstrap precedes runtime
binding plans, with live permission verification between them.

Artifact Registry IAM, preview IAM, scratch cleanup IAM and cron alert policies
also use callable workflows after `ci-ready`, with explicit tested source inputs
for restricted manual recovery. The generic target apply requires those inputs
and a registered caller contract for every invocation. Target records bind each
sync name to its actual trusted workflow. Artifact Registry and monitoring role
bootstraps precede dependent policies; preview role authority precedes service
account creation and bindings. Read-only probes check the actual registry or
bucket policy resource and the declared project permissions before planning.
These checks grant no additional authority and retain the existing allowlists.

Immediately before each saved-plan apply, trusted code runs
`python scripts/deployment_permissions.py --target <target> --plan-json <plan.json>`
after the resource allowlist. It derives permission probes from every changed
resource's actual action and before/after identities. An update requires update
authority; unchanged resources do not require creation or deletion authority.
Bucket, secret, service account, Cloud Run, registry, URL map, backend and IAP
policy probes use the affected resource so IAM conditions remain binding. A
resource created in the same plan uses its known production parent, with an
explicit creation dependency required when its identity is still unknown.
Missing parent authority, unknown identities, deferred changes and unsupported
mutation classes stop the apply. The checked-in roles are also validated against
their owned dependency contracts, without adding grants. For CDN sync, cache
invalidation authority is checked even when the URL map itself is unchanged.
Compute permission probes also require their method-specific list permission
before they can report operation authority. The two-phase
[probe-call contract](compute-probe-call-contract.md) checks that prerequisite
first and preserves the actual-resource operation check.
These checks prove mutation authority; runtime acceptance and installed state
remain separate prerequisites.

## Deployment records and outcomes

Each target records its exact tested revision, source CI attempt, execution run,
artifact digest and changed saved-plan target scope using GitHub deployment
records. These records prevent stale replay; approvals and mutation allowlists
still establish authority. Under the target's serialization, an older or
divergent revision cannot supersede a newer attempted deployment. An identical
successful revision is detected before loading an image or planning another
mutation and becomes a no-op. If a candidate reaches the final guard, different
artifact bytes from the same
code require reconciliation, except catalog refreshes: an actual reviewed data
mutation or index rebuild may produce a distinct current-state catalog bundle
under the same executor. A failed or interrupted attempt cannot be blanket
retried.

The Actions bot identity and a caller-supplied run URL do not prove who emitted a
record. Each initial claim and outcome has a canonical receipt binding the exact
deployment ID, status ID, target, executor, source CI attempt, artifact, target
scope and phase. The protected leaf signs it using GitHub artifact attestations.
Verification uses checksum-pinned GitHub CLI 2.96.0 and the cryptographically
verified Fulcio certificate to require the repository ID, main ref, exact leaf
and caller workflow revisions, GitHub-hosted runner, run ID and attempt. Observer
and read-only recovery updates require their own exact protected signatures.
A PR signature cannot copy a main run's metadata or replay another record's
receipt. Unavailable attestations and invalid signatures fail closed.

Receipts are reconstructed from durable Deployment API records and verified by
their digest through the repository attestation API; ordinary Actions artifact
retention does not discard their authorization evidence. The non-mutating
protected receipt rehearsal lands before deployment rewiring, and each leaf
must complete an exact-current-run rehearsal before starting a record. Its
signed claim must verify before infrastructure, SDK or catalog mutation. Image
staging first verifies the rehearsal and replay guard, then loads and pushes the
same immutable bytes; ingestion claims retain the resolved registry digest.
If claim signing fails, the same protected run may sign its failed/unknown
outcome to permit later read-only reconciliation. That failure receipt proves
record ownership and cannot satisfy success or bypass the claim barrier. If no
receipt can be signed, the unsigned attempt remains a blocking unknown state.

Validation jobs have read-only record/signing permissions. Writer tokens are
restricted in the reviewed workflows to protected production jobs and explicit
main-only reusable callers. The SDK retains its existing npm OIDC publisher
boundary, requiring trusted main bootstrap code and exact successful CI before
candidate checkout. Offline guardrails reject broader declarations.
Signatures protect against forged records and outcomes, including copied
Actions-bot metadata. They do not prevent destructive control-plane deletion:
an administrator or token independently granted Deployment API write authority
can inactivate and delete records. Such deletion is outside ordinary replay
integrity and requires explicit administrative recovery; it must never be
described as a successful deployment or a permitted stale retry.

A valid older revision that reaches the queue after a newer attempted deployment
finishes as an explicit `superseded` no-op. The verifier proves its exact tested
source and every blocking record's repository, workflow and source attempt first.
It writes no claim, changes no deployment record and launches no mutation. A
newer failed attempt still supersedes older code; it does not become successful.
Divergent history and a failed or incomplete attempt of the same revision remain
errors requiring reconciliation. A forged source or record cannot justify this
no-op.

Outcomes distinguish applied configuration, pending runtime verification,
verified terminal success, failure and unknown state. Short canaries wait for
terminal status. Before building or applying, an active execution defers the
whole ingestion target; production work is never cancelled automatically. A
paused WDPA schedule also defers the target until an explicit canary date is
supplied. Ingestion no-ops require verified runtime evidence; applied
configuration alone is insufficient. A canary skipped because of a later race
leaves unknown state and fails visibly. Detached WDPA executions retain the exact execution ID and
image in their pending record. The independent execution observer and Cloud Run
failure alerts remain active. `Deployment completion check` reads pending
records hourly, allocating a protected verifier only when one exists; it checks
the exact image and terminal execution, reports its outcome and fails on a
terminal failure or unknown result. A launched execution is never reported as
verified success.

## Selective recovery

For an interrupted ingestion deployment whose apply and canary actually
completed, dispatch `deployment-recovery.yml` on main with its `deployment_id`.
The protected workflow reads the original authority, checks out the original
tested revision, refreshes the captured target scope without applying, requires
a no-change plan and the original live image, and requires the latest execution
of that image to have terminal success. Only then can it reconcile the record
as verified. It performs no production apply or dataset writes. Later attempts,
drift, partial apply, missing scope, failed execution or incompatible state are
refused and require a reviewed repair. Registered IAM
and monitoring target records also support this read-only reconciliation: the
workflow refreshes the original explicit target scope and accepts only a
complete no-change plan. It performs no apply, does not grant new permissions,
and cannot reconcile partial or incompatible state. Dataset publication retains
its existing reviewed recovery and durable transaction rules. Failed or
incomplete CDN, viewer and catalog deployment records remain blocked until
a reviewed target-specific reconciliation is implemented; this workflow cannot
clear them.

An SDK publication that completed but failed its registry confirmation supports
read-only reconciliation through the same workflow. The main reconciler retains
its trusted checkout and reads the original executor in a separate directory.
It verifies the original successful CI plan, SDK producing job, artifact archive
and tested package digest, confirms live npm version/integrity metadata, then
downloads that exact version's public tarball and requires byte-for-byte equality before signing a verified status on the
original record. Missing or expired evidence, different bytes, a different
executor or any later attempt fails closed. It never republishes a package.
Normal publication metadata polling forces online cache checks so a pre-publish
version list cannot remain fresh throughout the confirmation window.

Manual deployment recovery uses explicit `executor_sha`, `source_run_id` and
`source_run_attempt` identifying successful main-push CI. It cannot authorize a
PR/fork revision. CDN sync, viewer deployment and SDK publication retain their
manual main entrypoints with all three inputs required. A first authorized
attempt may proceed, and an already verified identical revision becomes a
no-op. Dispatch does not clear a failed or incomplete record, change its
authorization or permit a blanket retry; target-specific reconciliation or a
reviewed repair is still required. SDK manual admission checks the real current
main workflow and exact source CI through GitHub's API; a fabricated upstream
completion event cannot substitute for that authority.

After runner/network failures, first inspect whether any
mutation began and inspect the persisted record. Retry safe validation or reads
selectively. Preserve partial publication ownership and original executor
contracts; do not delete claims, reset counters or choose a new date to bypass
an interrupted transaction.
