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
- The staged change updates catalog metadata for a new asset slug or meaningful
  dataset release and announcement status should be reported without blocking
  the commit.
- You need to avoid staging or committing another agent's unrelated work.
- You need to include Slack-ready alert content in a commit message.

## When Not To Use

Do not add a `repo-alert` block for:

- Routine fixes, docs-only edits, typo fixes, test-only changes, small refactors, dependency churn, or formatting-only updates.
- Operational fixes to existing automation, alerting, CI, deployment, secrets, retries, permissions, or error handling unless the commit adds a genuinely new capability users or maintainers did not have before.
- Repairs that make a recently added feature work as intended.
- Dataset uploads where the relevant notification is a dataset upload alert, not a repo functionality alert.
- Unstaged or unrelated files not included in the commit.
- Human-requested marketing copy that is not grounded in the staged diff.

Negative examples:

- A README typo fix should not get a repo-alert block.
- A test-only patch for existing behavior should not get a repo-alert block.
- A dependency lockfile refresh with no new user-facing capability should not get a repo-alert block.
- Fixing a GitHub Actions secret, retry guard, webhook delivery, or clearer error message for an existing alert workflow should not get a repo-alert block.
- Making a newly added workflow actually deliver after a configuration mistake should not get a repo-alert block; it is repair work, not a new capability.
- A local dataset catalog row update should use dataset notification workflow, not a repo functionality alert, unless it also adds new repo behavior.

Decision rule: a repo-alert is for net-new capability, not restoration. If the best headline starts with "Fix", "Restore", "Retry", "Handle missing", "Make X work", or "Improve diagnostics", do not add an alert unless the staged diff also introduces a distinct new feature.

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

If the staged diff updates `docs/assets/{asset-slug}.md`,
`catalog/shared-datasets-catalog.csv`, or `docs/assets/index.md` for a new
asset slug or meaningful dataset release, do not use the commit as a dataset
announcement state machine. Dataset upload announcements are operational
notifications, not Git commit gates. Report whether an upload summary was sent,
skipped, or uncertain, but do not block a requested commit to send or verify an
announcement. Do not send duplicate dataset upload announcements for corrective
same-release follow-ups unless explicitly asked.

Dataset upload announcements are separate from fenced `repo-alert` blocks.
Dataset-only catalog updates should normally use the dataset upload announcement
workflow, not a repo functionality alert.

Decide whether the commit adds substantially exciting new repository functionality. Prefer alerting for new capabilities such as SDKs, automation workflows, publishing tools, ingestion frameworks, infrastructure modules, reusable APIs, or major operational improvements.

3. If no alert is warranted, write a normal concise commit message.

4. If alert-worthy, inspect the last 30 commit messages before choosing an
   emoji:

```bash
git log -30 --format=%B
```

Collect any `emoji:` values from recent fenced `repo-alert` blocks, including
the current `HEAD` message when amending. Do not reuse any of those emojis. If
the most obvious emoji is already present in those 30 messages, choose a fresh
association that still fits the staged functionality.
Prefer a fresh, memorable association over the most literal possible symbol
when both are honest fits for the staged functionality. Adjacent metaphors,
tooling vibes, or outcome-oriented emojis are welcome; keep the choice legible,
not random or cute for its own sake.

5. Append one or more fenced blocks exactly like:

````text
```repo-alert
emoji: 🧰
headline: Vector publishing helper added
summary: A new command builds FlatGeobuf and PMTiles artifacts from source vectors.
why_excited: Manual publishes are faster, more repeatable, and easier to review.
```
````

Fields:

- `emoji`: one visual emoji that nods to the functionality or its impact.
  The emoji does not need to be the literal object named by the feature; a
  slightly creative association is allowed when it remains easy to explain.
  Do not use map or globe emojis, including `🗺️`, `🌍`, `🌎`, `🌏`, or `🌐`;
  these are overused in this repository and are not valid repo-alert choices.
  Do not use any emoji found in `repo-alert` blocks from the last 30 commit
  messages.
- `headline`: short, clear, announcement-style title.
- `summary`: one brief sentence describing what changed.
- `why_excited`: one brief sentence explaining why the team should care.

6. Keep alert copy grounded in the staged diff. Do not exaggerate beyond what the commit actually adds.

7. After committing, verify the message:

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
