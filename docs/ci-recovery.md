# CI and deployment recovery

1. Record the exact failed run/attempt, executor SHA, target and first failing step;
   identify one recovery owner and the original Slack incident/parent.
2. Classify the failure and locate the last confirmed mutation using signed records,
   live state and exact artifact/plan/generation evidence. Unknown impact stays unknown.
3. Repair reproducible defects and missing prerequisites; exercise that boundary and
   negative controls, then complete required validation. Never waive missing tests/tools.
4. Choose the existing protected recovery path with its original approval, executor,
   locks and immutable plan. For transient read-only validation outages, refresh live
   identity and bound retries to two additional attempts after 15 then 60 minutes.
5. Verify the operation's terminal outcome and observer proof separately. Confirm
   the original Slack parent changed to resolved; report blockers and retained evidence.

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
