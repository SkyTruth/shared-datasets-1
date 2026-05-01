---
name: repo-alert-commit-messages
description: Use when committing staged changes in shared-datasets-1 to decide whether the commit merits a fenced repo-alert Slack announcement block, generate custom alert copy, and keep staged scope isolated from unrelated work.
---

# Repo Alert Commit Messages

Use this skill when an agent is asked to commit staged changes, amend a commit message, prepare a commit message, or reason about repo-alert Slack notification blocks in `shared-datasets-1`.

## Intent

Commit-time alerts should be agent-generated and high-signal. The committing agent reviews the staged diff, decides whether the change adds substantially exciting new repository functionality, and, only when warranted, appends one or more fenced `repo-alert` blocks to the commit message.

The main-branch GitHub workflow posts fenced `repo-alert` blocks from commit messages. It does not decide significance.

## Git Safety Rule

NEVER STAGE, UNSTAGE, OR COMMIT changes unless the user explicitly asks for that exact Git operation.

Treat the Git index and commit history as user-owned state. Reading status, diffs, logs, and commit messages is allowed. Mutating the index or history is not allowed without an explicit user request.

## When To Use

Use this skill when:

- The user asks you to commit staged changes.
- The user asks you to amend or generate a commit message.
- The staged change adds or may add a meaningful new repository capability.
- You need to avoid staging or committing another agent's unrelated work.
- You need to include Slack-ready alert content in a commit message.

## When Not To Use

Do not add a `repo-alert` block for:

- Routine fixes, docs-only edits, typo fixes, test-only changes, small refactors, dependency churn, or formatting-only updates.
- Dataset uploads where the relevant notification is a dataset upload alert, not a repo functionality alert.
- Unstaged or unrelated files not included in the commit.
- Human-requested marketing copy that is not grounded in the staged diff.

Negative examples:

- A README typo fix should not get a repo-alert block.
- A test-only patch for existing behavior should not get a repo-alert block.
- A dependency lockfile refresh with no new user-facing capability should not get a repo-alert block.
- A local dataset catalog row update should use dataset notification workflow, not a repo functionality alert, unless it also adds new repo behavior.

## Workflow

1. Inspect staged scope first:

```bash
git diff --cached --name-status
git diff --cached --stat
```

If unrelated files are staged, stop and clarify. Do not unstage unrelated work unless the user explicitly asks you to unstage it.

2. Review the staged diff:

```bash
git diff --cached
```

Decide whether the commit adds substantially exciting new repository functionality. Prefer alerting for new capabilities such as SDKs, automation workflows, publishing tools, ingestion frameworks, infrastructure modules, reusable APIs, or major operational improvements.

3. If no alert is warranted, write a normal concise commit message.

4. If alert-worthy, append one or more fenced blocks exactly like:

````text
```repo-alert
emoji: 🗺️
headline: Vector publishing helper added
summary: A new command builds FlatGeobuf and PMTiles artifacts from source vectors.
why_excited: Manual publishes are faster, more repeatable, and easier to review.
```
````

Fields:

- `emoji`: one visual emoji that nods to the functionality.
- `headline`: short, clear, announcement-style title.
- `summary`: one brief sentence describing what changed.
- `why_excited`: one brief sentence explaining why the team should care.

5. Keep alert copy grounded in the staged diff. Do not exaggerate beyond what the commit actually adds.

6. After committing, verify the message:

```bash
git show --no-patch --format=full HEAD
```

Confirm fenced alert blocks are intact.

## Commit Message Shape

Recommended structure:

````text
Short imperative subject

Brief commit body explaining the change and why it belongs together.

```repo-alert
emoji: 📣
headline: Agent-written repo alerts added
summary: Agents can now include Slack-ready release notes directly in commit messages for substantially exciting repository changes.
why_excited: Maintainers get custom, high-signal updates when important capabilities land on main without alerting on every commit.
```
````

## Completion Criteria

Before finishing, report:

- Commit SHA if a commit was created.
- Whether a `repo-alert` block was included and why.
- Staged/unstaged files left behind, especially unrelated work from another agent.
- Verification command run, usually `git show --no-patch --format=full HEAD`.
