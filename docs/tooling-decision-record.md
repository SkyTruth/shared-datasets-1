# Tooling decision record: GCP asset operations

## Decision

For the shared datasets project:

- **Terraform** manages infrastructure.
- **A repo-owned Python CLI/library using `google-cloud-storage`** manages dataset objects.
- **`uv`** manages local Python dependencies and runs the repo CLI.
- **`gcloud storage`** is allowed for human diagnostics, emergency downloads,
  and documented break-glass operations. Manual canonical writes should still
  stage under `_scratch/pending-publishes/` and promote only through an
  explicit PR with a fenced publish or delete plan and the approved publisher
  workflow after merge.
- **Cloud Storage FUSE** is allowed only for read-heavy exploration, not canonical writes.
- **Terraform/Pulumi should not manage frequently changing dataset objects.**

## Context

The repo controls both cloud automation and the organization of a shared data bucket. Contributors and AI agents need a safe, repeatable way to inspect, upload, edit, and publish files without turning bucket contents into an IaC state problem.

## Options considered

| Option | Strengths | Weaknesses | Decision |
|---|---|---|---|
| Terraform | Excellent for infrastructure, plans, review, IAM, schedulers | Bad fit for frequently changing data objects; creates noisy state churn | Use for infra only |
| Pulumi | Programmable IaC | Adds another runtime/framework; same data-object state issue | Do not adopt unless team standard changes |
| `gcloud storage` | Official CLI, good for humans, supports preconditions | Easy to use inconsistently; shell scripts can become fragile | Allow for diagnostics, emergency downloads, and documented break-glass use |
| Cloud Storage FUSE | Convenient filesystem interface | Not POSIX; metadata/concurrency limitations; unsafe mental model for canonical writes | Read-heavy exploration only |
| Python `google-cloud-storage` CLI | Testable, scriptable, supports preconditions, works locally/CI/Cloud Run, can enforce SkyTruth conventions | Requires small amount of maintained code | Preferred for data objects |
| `uv` for local Python tooling | Fast, reproducible project dependency management; avoids ad hoc pip/venv/mamba setup | Requires `uv` to be installed locally | Preferred runner for repo Python commands |

## Consequences

- Agents have one standard path for remote object edits.
- Cron jobs can share upload/list/stat behavior.
- We can make no-clobber and generation preconditions the default.
- Terraform remains clean and focused on infrastructure.
- Manual object edits become easier to audit because they stage under
  `_scratch/pending-publishes/` and promote only through an explicit PR and the
  approved publisher workflow after merge; exceptional break-glass edits must be
  documented.

## Implementation

The initial implementation is `scripts/gcs_asset.py`.

Run it through `uv`:

```bash
uv run python scripts/gcs_asset.py --help
```

Shipped capabilities:

| Capability | Implementation |
|---|---|
| Path validation against taxonomy/layout | [`gcs_asset.py validate-path`](../scripts/gcs_asset.py) |
| Release planning and generation-preconditioned publication | [`gcs_asset.py publish-release`](../scripts/gcs_asset.py), backed by [`publish_release.py`](../scripts/publish_release.py) |
| YAML frontmatter parsing, managed asset-doc blocks, catalog/index generation and README export | [`catalog_docs.py`](../scripts/catalog_docs.py) |
| Local FGB/PMTiles build and validation, including archive verification and representative tile decoding | [`vector_asset.py`](../scripts/vector_asset.py) |
| Format/layout, README/catalog, release-index and sidecar/schema/manifest compliance checks | [compliance audit](../.claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py) |
| Read-only JSON release plans | `gcs_asset.py publish-release --dry-run` |

Validation is format-specific: vector archive/content checks, raster validation
in [`raster_asset.py`](../scripts/raster_asset.py), and CSV/OGR schema inspection
in [`dataset_alerts.py`](../scripts/dataset_alerts.py) complement the compliance
audit. The audit does not validate every dataset's complete bytes. See the
[GCS workflow](../.claude/skills/gcp-shared-datasets/SKILL.md) for object operations
and the [reviewed plan contract](../.github/dataset-plans/README.md) for manual
canonical mutation authority; a dry-run plan grants no approval.
