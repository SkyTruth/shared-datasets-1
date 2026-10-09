# Repo-Local Skills

Keep the single [skill routing catalog in AGENTS.md](../../AGENTS.md#repo-local-skills)
as the entry point for choosing a workflow.

`.claude/skills/` is the canonical checked-in home. `.agents/skills` must be a
symlink mirror to `../.claude/skills` for Codex-native discovery. Do not keep a
second live copy under a bare repo-root `skills/` directory.

Use the portable Agent Skills subset:

- YAML frontmatter with only `name` and `description`.
- Concise Markdown instructions.
- Optional bundled `references/`, `scripts/`, or `assets/`.
- Product-specific metadata in product-specific folders, such as `agents/openai.yaml`.
