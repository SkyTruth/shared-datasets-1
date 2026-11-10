---
name: deploy-scheduled-ingestion
description: "Use before deploying or updating shared-datasets Cloud Run and Cloud Scheduler ingestion jobs, including Artifact Registry images, Terraform applies, manual job executions, alert coverage, and post-deploy verification."
---

# Deploy Scheduled Ingestion

Follow [Change or add an ingestion deployment](../../../docs/ci-deployment.md)
in order. It owns preparation, registry/wrapper changes, prerequisites, tests,
review, rollout and completion. Read only the selected job's operational
exceptions in [job operations](references/job-operations.md).

Identity activation follows the [two-phase rollout](../../../docs/github-identity-rollout.md):
phase 1 adds workflow identities beside legacy access; phase 2 removes legacy
grants only after successful main runs prove every new identity authenticates.

## Required boundaries

- Load [protected Terraform guidance](../protected-terraform-apply/SKILL.md)
  before discussing production Terraform mutations. Production uses reviewed
  main and protected workflows; no local apply is permitted outside explicit
  break-glass authority. Dataset files never belong in Terraform state.
- Load [GCS guidance](../gcp-shared-datasets/SKILL.md) before inspecting or
  changing remote dataset objects. No deployment grants local canonical writes.
- Follow [publication readiness](../../../docs/publication-readiness.md) for
  every publication batch before expensive builds, CI or publication recovery.
  Preserve active execution ownership, source pins, claims and checkpoints.
- Use the [deployment contract](../../../docs/deployment-contracts.md) for
  tested bytes, target registry views, queue ownership, saved-plan validation,
  runtime windows, signed receipts and recovery eligibility. Changes to the
  shared queue require draining active/queued runs using the old configuration.
- Follow [job boundaries](../../../ingestion/README.md) for source isolation,
  stable live surfaces, unchanged-source skips and release indexes. New
  vector/table pipelines need the AGENTS.md identity decision table and approved
  assignment rule before publication.
- Use immutable Linux AMD64 images retained by successful main CI. Size CPU,
  memory, timeout and scratch against the full source. Production-source
  fractional tests must follow the real conversion chain and never publish
  sampled bytes; full-release decisions still require complete discovery.
- Verify [alert coverage](../../../docs/alert-routing.md#execution-and-delivery-verification)
  when changing a job or policy. Monitoring notification channels and the
  Terraform summary webhook are separate delivery paths. Never deliberately
  fail production or send synthetic production alerts to prove delivery.

WDPA and sea ice verify installed publication state before preparing images;
missing state cannot authorize an automatic reset. For the one-time transition,
use [feature-ID installation](../../../docs/feature-id-reset-installation.md).
WDPA's disk/memory constraints and multi-hour execution monitoring belong to
[its operations runbook](../../../docs/wdpa-monthly-runbook.md).
