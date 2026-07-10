# Terraform State Isolation And Recovery

Production and preview Terraform state belong in the private flat-namespace
bucket `gs://skytruth-shared-datasets-1-terraform-state/`. The bucket has
Object Versioning, 30-day soft delete, public-access prevention, and
`prevent_destroy`. Dataset consumers, catalog services, and Cloud CDN receive no
access to it.

The shared dataset bucket uses hierarchical namespace, which does not support
Object Versioning. It instead uses 30-day soft delete for deleted or overwritten
objects. Do not remove hierarchical namespace or add an unsupported versioning
block.

## Reviewed Migration Order

Run these only after their changes are reviewed by `jonaraphael` and merged to
`main`:

1. Dispatch **Storage hardening sync** with phase `add_scoped`, confirmation
   `HARDEN_STORAGE_ADD_SCOPED`, and `use_legacy_backend=true`. Review its saved
   plan, then verify the new state bucket, 30-day policies, managed-folder IAM,
   public catalog/canonical reads, and anonymous private-object denial.
2. In a separate dispatch, run **Storage hardening sync** with phase
   `remove_broad`, confirmation `HARDEN_STORAGE_REMOVE_BROAD`, and
   `use_legacy_backend=true`. The workflow rechecks the scoped grants before it
   plans only deletion of the legacy bucket-wide CDN and consumer grants.
3. Dispatch **Terraform State Migration** with
   `MIGRATE_TERRAFORM_STATE`. It refuses an existing destination, migrates prod
   and preview serially, records each legacy source generation, and verifies
   lineage, serial, outputs, complete indexed resource addresses, and
   destination generations.
4. Rerun two read-only Terraform plans and one protected no-op apply. Every
   normal apply workflow runs `terraform_state_backend_guard.py` before init and
   therefore fails closed until migration has produced both state objects.
5. Dispatch **Preview Terraform IAM sync** to import the stable preview bucket
   and database into prod state and apply the narrowed roles.
6. Dispatch **Feature Preview State Ownership Migration** with
   `MIGRATE_PREVIEW_OWNERSHIP`. Its saved plan must contain only `forget`
   actions for the stable preview bucket, database, and bucket IAM addresses.
7. Use the exact legacy prod and preview generations printed by the migration
   summaries, then dispatch
   **Terraform State Legacy Cleanup** with those generations and
   `DELETE_LEGACY_TERRAFORM_STATE`. The workflow refuses changed generations;
   deleted copies remain recoverable under the shared bucket's 30-day soft
   delete policy.

Do not run a normal apply between the storage phases and state migration,
bypass the backend guard,
overwrite an existing destination state, delete legacy state before validation,
or perform the migration from a local terminal.

## Recovery

- Restore a deleted or overwritten state generation from the isolated bucket's
  version history or 30-day soft-delete inventory.
- Compare lineage, serial, outputs, and resource addresses with the last known
  good state before making it live.
- Route any state push, restore, or backend change through a reviewed,
  main-only workflow using the applicable prod or preview concurrency group.
- Record source and destination generations and retain the superseded version
  until the restored state has passed read-only plans and a protected no-op
  apply.
