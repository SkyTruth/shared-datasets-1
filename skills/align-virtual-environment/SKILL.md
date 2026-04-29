---
name: align-virtual-environment
description: Align, repair, update, or document a repository's Python environment using the project-declared tooling. Use when Codex needs to create or sync a virtual environment, install Python dependencies, choose between uv/pip/venv/conda/mamba/poetry, update lockfiles, migrate dependency docs, recover from ad hoc environment mistakes, or verify that repo Python tools run reproducibly.
---

# Align Virtual Environment

## Overview

Use the repository's declared environment contract as the source of truth. Leave future agents with one documented, reproducible path rather than a trail of improvised virtualenv, pip, mamba, or cache state.

## Workflow

1. Discover the environment contract before installing anything:
   - Read high-authority instructions: user request, `AGENTS.md`, repo README, setup docs, and language/tooling docs.
   - Inspect dependency files with `rg --files`: `pyproject.toml`, `uv.lock`, `requirements*.txt`, `poetry.lock`, `Pipfile`, `environment.yml`, `conda-lock.yml`, `setup.py`, `tox.ini`, `noxfile.py`, and CI configs.
   - Check existing ignored env/cache patterns before creating new files.

2. Choose the tool by authority, not habit:
   - If the repo declares `uv` or has `uv.lock`, use `uv sync`, `uv lock`, and `uv run ...`.
   - If the repo declares conda/mamba through `environment.yml` or conda lockfiles, use that stack.
   - If the repo declares pip/venv through `requirements*.txt` and docs, use the documented venv path.
   - If signals conflict, stop and surface the conflict. Do not silently substitute another environment manager because one command failed.

3. Keep environment state local and intentional:
   - Prefer project-local envs/caches that are ignored by git, such as `.venv/` and `.uv-cache/`.
   - If sandboxing blocks home caches, set a local cache explicitly, for example `UV_CACHE_DIR=.uv-cache uv run ...`.
   - Do not create multiple competing environments for the same task.
   - Do not delete user-created environments or global package caches unless the user explicitly asks.

4. Update the project contract when practices change:
   - If adding `uv`, add or update `pyproject.toml`, `uv.lock`, README/setup docs, and relevant agent/skill docs together.
   - Remove stale dependency entry points only when the replacement is complete. For example, delete `requirements.txt` only after dependencies are represented in `pyproject.toml` and docs no longer reference pip.
   - Keep `.gitignore` aligned with generated local artifacts.

5. Verify the environment through real commands:
   - Run the declared sync/lock command.
   - Run the repo command that motivated the setup, such as `uv run python scripts/tool.py --help`, tests, typechecks, or import checks.
   - If a command fails because of sandboxed network or filesystem access, rerun with the required approval instead of switching tooling.

6. Clean up mistakes immediately:
   - Remove only artifacts you created, such as a mistaken `.venv/`, `.uv-cache/`, or temporary conda prefix.
   - Use safe deletion practices and request approval when deleting outside the workspace or when scope is broad.
   - Re-check `git status --short` so only intentional source-controlled changes remain.

## Tool Notes

### uv

Prefer `uv` when it is the repo's declared standard or when introducing a new Python project workflow with user approval.

Common commands:

```bash
uv sync
uv lock
uv run python path/to/script.py --help
UV_CACHE_DIR=.uv-cache uv run python path/to/script.py --help
```

Commit `pyproject.toml` and `uv.lock` when they define the project. Do not commit `.venv/` or uv caches.

### pip and venv

Use pip/venv only when the repo already declares that workflow or the user asks for it. Avoid mixing pip installs into conda or uv-managed environments unless the repo documents that hybrid approach.

### conda and mamba

Use conda/mamba when the repo declares native/system packages, geospatial stacks, CUDA, or `environment.yml`/conda lockfiles. Do not use mamba as a fallback for Python-only dependency setup unless the project contract says to.

## Completion Criteria

Before finishing, report:
- The declared environment manager and why it was selected.
- Files changed for dependency metadata, lockfiles, ignore rules, and docs.
- Verification commands run.
- Any environment artifacts created or removed.
- Remaining blockers, especially network, permissions, or unresolved tooling conflicts.
