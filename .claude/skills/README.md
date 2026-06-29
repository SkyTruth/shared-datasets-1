# Repo-Local Skills

This directory is the canonical checked-in skill catalog for this repository.

`.agents/skills` is a symlink mirror that points here for Codex-native discovery. Keep shared repo-local skills in the portable Agent Skills subset:

- YAML frontmatter with only `name` and `description`.
- Concise Markdown instructions.
- Optional bundled `references/`, `scripts/`, or `assets/`.
- Product-specific metadata in product-specific folders, such as `agents/openai.yaml`.

Do not keep a second live copy under a bare repo-root `skills/` directory.

Current skills:

- `align-virtual-environment`
- `deploy-scheduled-ingestion`
- `feature-preview`
- `gcp-shared-datasets`
- `invariant-first-engineering`
- `publish-shared-dataset`
- `protected-terraform-apply`
- `repo-alert-commit-messages`
- `shared-datasets-compliance-audit`
- `static-catalog-web-preview`
- `sync-docs-with-code`
- `update-feature-metadata-translations`
