# Local Temporary Workspace Standard

This standard keeps local generated files discoverable, reusable when needed,
and cleanable without guessing who created them.

## Policy

Temporary workspace hygiene is a repo-wide operating rule, not a separate skill.
It applies to agents, maintainers, scripts, tests, manual publish workflows,
catalog previews, source downloads, and one-off scratch work.

Use this root unless a tool has a stronger documented reason:

```text
${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}
```

Examples may use `$TMPDIR/shared-datasets-1/...` for readability, but scripts
should resolve `SHARED_DATASETS_WORKDIR` first and otherwise append
`shared-datasets-1` to the system temp directory.

## Directory Layout

Use predictable child directories:

| Purpose | Directory |
|---|---|
| Vector asset builds | `vector-assets/{asset-slug}/` |
| Catalog web bundle | `catalog-web/` |
| Exported bucket READMEs | `readmes/` |
| Remote/source downloads | `downloads/{asset-slug-or-source}/` |
| Release-index or catalog side inputs | `release-indexes/` |
| PMTiles maxzoom audits | `pmtiles-maxzoom-audit/{YYYYMMDDTHHMMSSZ}/` |
| One-off scratch work | `_scratch/{task-slug}-{YYYYMMDDTHHMMSSZ}/` |

Within asset build directories, use `source/`, `build/`, `publish/`, `logs/`,
and `profiles/` where practical. Final upload candidates belong in `publish/`.

## Rules

- Do not write generated data artifacts into the repository unless they are tiny
  intentional fixtures.
- Do not place ad hoc files directly in `/tmp`, `$HOME`, `Downloads`, `Desktop`,
  or the repo root.
- Do not create bare, ambiguous names such as `/tmp/out.pmtiles`,
  `/tmp/asset.README.md`, or `/tmp/test.geojson`.
- Keep task names stable and grep-friendly: lowercase kebab-case slugs and UTC
  timestamps for one-off scratch directories.
- Prefer streaming and sampling for large inputs. When intermediates are needed,
  keep them under the relevant work directory and remove or replace them through
  a deliberate cleanup step.
- For scripts, expose `--work-dir` or a repo-specific environment variable when
  output may be large or long-lived.
- For publishable bytes that may need auditability, preserve the small
  `logs/`, `profiles/`, or manifest files needed to explain the result.
- Final responses should name retained local work directories when they contain
  meaningful generated artifacts.

## Cleanup

Cleanups must be exact and reviewable.

- A script may use process-scoped temporary directories for disposable
  intermediates, but those directories must still live under the standard root
  when practical.
- Agents must not delete stale files or directories from prior tasks without
  explicit action-time confirmation.
- Before deleting stale work, report the exact paths, approximate size, age, and
  reason each path is safe to remove.
- Never run broad cleanup against `${TMPDIR}`, `/tmp`, the repository root, or
  the whole `shared-datasets-1` temp root.
- Prefer cleanup targets at the workflow-child level, for example one
  `_scratch/{task}` directory or one `vector-assets/{asset-slug}/build/`
  directory.

## Examples

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
OUT="$WORK_ROOT/catalog-web"
READMES="$WORK_ROOT/readmes"
SCRATCH="$WORK_ROOT/_scratch/pmtiles-header-check-$(date -u +%Y%m%dT%H%M%SZ)"
```

For a remote README edit:

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
EDIT_DIR="$WORK_ROOT/downloads/example-asset"
mkdir -p "$EDIT_DIR"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py download \
  "$URI" "$EDIT_DIR/README.md"
```
