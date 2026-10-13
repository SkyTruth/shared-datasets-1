# scripts/

Use `uv run` for repo Python tools. Procedures live in the [skill catalog](../AGENTS.md#repo-local-skills); each CLI's top-level and subcommand `--help` own flags. JSON reports and exit codes supply validation evidence. Internal libraries have no standalone CLI; use their owning command below.

## Branch cleanup

`tidy_branches.sh` previews verified merged-branch cleanup and retains a recovery report/bundle before authorized deletion. Use it without concurrent Git writers; preview fetches/prunes refs. Interface: `bash scripts/tidy_branches.sh --help`.

## Dataset operations

`publishing_concierge.py` guides first uploads and preview bundles through evidence gates, then renders reports or immutable plan documents; it performs no remote or Git operations. Use the [publish routes](../.claude/skills/publish-shared-dataset/SKILL.md). Interface: `uv run python scripts/publishing_concierge.py --help`.

`gcs_asset.py` inspects objects, validates paths and performs generation-safe transfers. Agents stage production bytes; only the protected publisher mutates canonical destinations. Existing release planning uses its dry-run path. Interface: `uv run python scripts/gcs_asset.py --help`.

`reviewed_dataset_plan.py` validates manually prepared publish/delete payloads and writes immutable documents with matching PR fences. Use when concierge rendering is unsuitable; [approval contract](../.github/dataset-plans/README.md). Interface: `uv run python scripts/reviewed_dataset_plan.py --help`.

`vector_asset.py` builds and validates local FGB/PMTiles and recommends maxzoom; use for vector preparation before staging. Reports retain native validation and tool evidence; no GCS writes. Interface: `uv run python scripts/vector_asset.py --help`.

`raster_asset.py` validates COG/Zarr candidates before staging; use for approved raster/array contracts. Interface: `uv run python scripts/raster_asset.py --help`.

`validate_feature_metadata.py` checks a local sidecar/schema/manifest bundle, hashes and feature counts without Firestore or remote writes. Interface: `uv run python scripts/validate_feature_metadata.py --help`.

`catalog_docs.py` generates/checks catalog and managed asset-doc blocks, and exports bucket READMEs. Update asset docs first; use it before proposal staging. Interface: `uv run python scripts/catalog_docs.py --help`.

`catalog_site.py` builds the static catalog bundle from asset/catalog metadata and optional release indexes. Use for local runtime verification; the [catalog skill](../.claude/skills/static-catalog-web-preview/SKILL.md) owns deployment. Interface: `uv run python scripts/catalog_site.py --help`.

`collect_release_indexes.py` collects generation-observed release indexes for catalog hydration; use before a release-backed catalog build. Interface: `uv run python scripts/collect_release_indexes.py --help`.

`catalog_web_publish.py` publishes a validated web bundle under the protected publisher; local agents use the reviewed staging procedure. Interface: `uv run python scripts/catalog_web_publish.py --help`.

`catalog_drift_guard.py` compares generated and live catalog contracts read-only; use after protected catalog publication. Interface: `uv run python scripts/catalog_drift_guard.py --help`.

`publish_workflow.py` supplies the protected mutation workflow's acceptance, validation, promotion, deletion and notification steps. It grants no local mutation authority. Interface: `uv run python scripts/publish_workflow.py --help`.

`finalize_promoted_release_metadata.py` binds prepared metadata to observed promoted artifacts and replaces only the captured generation; use inside protected publication, never as a local recovery shortcut. Interface: `uv run python scripts/finalize_promoted_release_metadata.py --help`.

`scratch_cleanup.py` audits abandoned pending-publish prefixes using the [standing age/generation policy](../.claude/skills/gcp-shared-datasets/references/scratch-cleanup.md). Its scheduled workflow owns authorized cleanup. Interface: `uv run python scripts/scratch_cleanup.py --help`.

`dataset_alerts.py` checks schema compatibility and prepares dataset notifications; use read-only checks before publication. The [alert policy](../docs/alert-routing.md#dataset-and-repository-notifications) owns send behavior. Interface: `uv run python scripts/dataset_alerts.py --help`.

`repo_alerts.py` previews/posts exceptional functionality announcements from approved commit fences; maintenance defaults to no repo alert. Interface: `uv run python scripts/repo_alerts.py --help`.

`slack_notify.py` delivers structured workflow summaries; use only with notification authorization and the owning workflow policy. This argument-only helper has no `--help`; use `uv run python scripts/publish_workflow.py --help` for its workflow entry point.

`slack_incidents.py` owns signed incident state and quiet reconciliation after verified recovery. Use the [incident contract](../docs/slack-incidents.md). Interface: `uv run python scripts/slack_incidents.py --help`.

## Localized metadata sidecars

`feature_metadata_machine_translate.py` fills exactly the curator-approved asset/locales/fields in a translation CSV using the maintained machine provider. Use the [translation skill](../.claude/skills/update-feature-metadata-translations/SKILL.md). Interface: `uv run python scripts/feature_metadata_machine_translate.py --help`.

`feature_metadata_localization.py` materializes deterministic locale sidecars and coverage reports from canonical metadata and translation CSVs, rejecting duplicate/stale keys. Stage the complete CSV/locale/manifest bundle together. Interface: `uv run python scripts/feature_metadata_localization.py --help`.

`feature_metadata_document_translate.py` imports reviewed document translations into the same source contract; use when the document provider is selected. Interface: `uv run python scripts/feature_metadata_document_translate.py --help`.

`feature_metadata_translation_gap_documents.py` exports unresolved current source values for review; it does not grant translation or publication approval. Interface: `uv run python scripts/feature_metadata_translation_gap_documents.py --help`.

## Release comparison

`compare_releases.py` compares complete pinned canonical bundles with a task-scoped SQLite index. CLI and authenticated viewer share the [comparison semantics and budgets](../docs/compare-releases.md); it does no remote writes/downloads. Interface: `uv run python scripts/compare_releases.py --help`.

## Validation and deployment tools

`ci_preflight.py` validates a frozen clean committed head against current main in an isolated prospective merge; [preflight contract](../docs/ci-preflight.md). Interface: `uv run python scripts/ci_preflight.py --help`.

`terraform_plan_allowlist.py` rejects saved plans outside a protected workflow's mutation scope; use in the owning constrained job. Interface: `uv run python scripts/terraform_plan_allowlist.py --help`.

`terraform_target_contracts.py` checks credential-free dependency closures and negative controls against reviewed prerequisites; it never expands apply allowlists. Interface: `uv run python scripts/terraform_target_contracts.py --help`.

`terraform_prod_apply.py` is reserved for explicitly named break-glass emergencies; normal production changes use the [protected skill](../.claude/skills/protected-terraform-apply/SKILL.md). Interface: `uv run python scripts/terraform_prod_apply.py --help`.

`dataset_usage_cold_start.py` validates the actual usage-worker image offline, including disabled logging and persisted run consistency; use in production-image validation. This offline fixture has no help parser; use `uv run python scripts/ci_preflight.py --help` for its validation entry point.

`local_ingestion_smoke.py` builds tiny native ingestion fixtures locally without publication; use for conversion feedback. Interface: `uv run python scripts/local_ingestion_smoke.py --help`.

`local_wdpa_sample.py` replays the production conversion chain locally without a publisher; use the owning deploy skill and runtime limits. Interface: `uv run python scripts/local_wdpa_sample.py --help`.

`wdpa_processing_gate.py` checks retained-build acceptance and the hash-bound terminal execution snapshot before protected monthly deployment; the one-time October build campaign is retired. Interface: `uv run python scripts/wdpa_processing_gate.py --help`.

`wdpa_build_authorization.py` verifies the immutable owned WDPA build plan and exact-head promotion authority inside the protected workflow; it grants no approval itself. This GitHub-dependent workflow entry point has no help parser; use `uv run python scripts/wdpa_processing_gate.py --help` for the local acceptance interface.

## Shared libraries

`release_feature_model.py` constructs stable IDs/hashes, serializes sidecars and creates manifests for publishing/ingestion callers; it has no CLI. Use `uv run python scripts/validate_feature_metadata.py --help` for the public bundle-validation entry point.

`catalog_csv.py` owns catalog row parsing for script callers; it has no CLI. Use `uv run python scripts/catalog_docs.py --help` for catalog generation/checking.

`concierge_profiling.py` owns source readers, attribute profiles and curator field recommendations; it has no CLI. Use `uv run python scripts/publishing_concierge.py start --help` and read the saved plan's field options.

`feature_metadata_translation_reuse.py` handles canonical translation-source reuse for ingestion/localization callers; it has no CLI. Use `uv run python scripts/feature_metadata_localization.py --help` for materialization.
