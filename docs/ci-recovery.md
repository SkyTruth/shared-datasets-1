# CI and deployment recovery

Inspect the exact revision, run/attempt and first failing boundary. Fix reproducible
defects and revalidate; never waive missing tools/tests or unexpected skips. After
transient validation outages, retry the affected job with original attempt evidence.
Never blanket-rerun production or automatically cancel mutations: reconcile source,
artifact, authority, deployment/state and receipts first. GitHub cancellation does
not prove Cloud Run or remote writes stopped. Preserve locks, saved plans,
generations, ownership and checkpoints; retries cover pre-mutation lock acquisition only.
For compatible unfinished publication, rerun the entire original eligible main
workflow with its executor/plan (GitHub limits: 30 days/50 attempts). Apply-only
reruns risk stale envelopes. A newer executor may verify an activated reset and
finish its journal, but cannot continue creating reset objects under a changed
contract. Missing authority/evidence needs a reviewed constrained recovery path.
Report safe retry, terminal outcome and changed paths/generations. See
[alert routing](alert-routing.md), [reset installation](feature-id-reset-installation.md) and the owning ingestion README.
