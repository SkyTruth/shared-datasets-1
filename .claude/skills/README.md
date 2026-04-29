# Repo-Local Skills

This directory is the canonical checked-in skill catalog for this repository.

`.agents/skills` is a symlink mirror that points here for Codex-native discovery. Keep shared repo-local skills in the portable Agent Skills subset:

- YAML frontmatter with only `name` and `description`.
- Concise Markdown instructions.
- Optional bundled `references/`, `scripts/`, or `assets/`.
- Product-specific metadata in product-specific folders, such as `agents/openai.yaml`.

Do not keep a second live copy under a bare repo-root `skills/` directory.
