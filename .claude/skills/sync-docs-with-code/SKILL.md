---
name: sync-docs-with-code
description: Use when keeping repository documentation aligned with current code after merged PRs, release work, scheduled maintenance, or explicit docs-drift requests. Inspect implementation, tests, CLIs, workflows, Terraform, schemas, generated outputs, and repo conventions first; then update authoritative docs, regenerate derived docs when applicable, validate checks, and report remaining code/docs mismatches. In shared-datasets-1, applies to README.md, AGENTS.md, docs/, docs/assets/, ingestion READMEs, api/python/README.md, templates, repo-local skills, catalog generation, and GitHub workflow documentation.
---

# Sync Docs With Code

Keep documentation accurate to the behavior the repo actually ships. Treat code,
tests, schemas, command help, workflow files, Terraform, and generators as
evidence. Treat docs as user-facing contracts.

## Workflow

1. Establish scope from the user request, PR diff, merge range, or scheduled
   maintenance window. Prefer `git diff`, `git log`, `rg`, and relevant tests
   over guessing.

2. Identify public or operational behavior changes:
   - CLI flags, script behavior, environment variables, outputs, errors.
   - API interfaces, package usage, config keys, schemas, catalog fields.
   - GitHub workflow triggers, inputs, permissions, secrets, artifacts.
   - Terraform variables, outputs, resources, IAM, deployment paths.
   - Ingestion cadence, release behavior, validation, run records.
   - Dataset metadata, README requirements, templates, and generated catalogs.

3. Update the authoritative source, not stale derivatives:
   - Use `README.md` for repo purpose, quick start, and maintainer workflows.
   - Use `docs/standards/*` for durable dataset rules.
   - Use `docs/assets/{asset-slug}.md` for asset metadata and bucket README
     content; do not hand-edit `catalog/shared-datasets-catalog.csv`.
   - Use `api/python/README.md` for SDK and CLI usage.
   - Use `ingestion/*/README.md` for job-specific behavior.
   - Use `AGENTS.md` and `.claude/skills/*/SKILL.md` for agent workflow rules.
   - Use `templates/*` when generated bucket README structure changes.

4. Regenerate derived docs when their sources changed:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check
```

5. Validate with the narrowest checks that cover the changed contract. Prefer
   existing repo tests such as catalog docs, repo guardrails, workflow tests,
   SDK tests, ingestion tests, or Terraform validation as appropriate.

## Rules

- Do not invent behavior to make docs look complete. If code and docs disagree,
  decide from evidence or report the mismatch.
- Do not change production infrastructure, canonical GCS objects, releases, or
  Git history as part of docs sync unless the user explicitly asks and the
  relevant safety skill has been loaded.
- Keep edits focused. Fix stale commands, paths, examples, generated-field
  descriptions, and operational steps; avoid broad prose rewrites.
- Preserve generated markers and frontmatter structure.
- If the docs claim something is automated, verify the workflow, script, or test
  exists.

## Completion

Report:

- Docs changed and why.
- Code/config evidence used.
- Generated files refreshed.
- Validation commands run.
- Any remaining uncertain or intentionally deferred mismatches.
