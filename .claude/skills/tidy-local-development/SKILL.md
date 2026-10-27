---
name: tidy-local-development
description: Use for merged local and remote Git branch cleanup, main synchronization included in that cleanup, and removal of obsolete repository-owned Docker artifacts. Interpret short repeat requests within established cleanup scope. Protect active work, recovery evidence, toolchains, and persistent data.
---

# Tidy Local Development

## Intent

Remove completed development references and obsolete Docker artifacts while
preserving unpublished work, active validation, reusable toolchains, and
persistent data.

All paths below are repository-relative.

## When To Use

Use for:

- Cleaning merged local or remote Git branches.
- Updating main as part of an authorized cleanup.
- Removing obsolete repository-owned Docker images or unused build cache.
- Repeating an established cleanup after an "again" or "tidy" request.

Refresh live evidence on every sweep. Earlier observations do not establish
that a target is still safe to remove.

## When Not To Use

Do not infer development cleanup from an unrelated request to tidy something.

Negative examples:

- "Tidy the catalog descriptions" belongs to the relevant documentation workflow.
- "Delete old releases from the bucket" requires the GCS and reviewed deletion
  workflows.
- "Fix the failed publication" belongs to incident recovery.

This skill grants no authority to commit, reset, publish, deploy, message
another task, or delete persistent data.

## Establish Scope And Ownership

Read AGENTS.md, docs/agent-integration.md, and
docs/standards/local-temp-workspaces.md.

Carry forward established user authorization and exclusions. Distinguish:

- Local branches, remote branches, and remote-tracking refs.
- Worktree registrations and their directory contents.
- Docker tags, unique images, containers, cache, and volumes.

Inspect current Git status, worktrees, open PRs, and relevant task activity.
Protect active owners and keep validating checkouts frozen. Do not take over
or message another task without authorization.

Save sanitized evidence in a named child of the standard temporary workspace
root. Display summaries instead of dumping large inventories or secrets.

## Clean Branches Through The Existing Script

Read scripts/tidy_branches.sh and its help before constructing cleanup commands.

Use its preview and apply modes, adding explicit --keep entries for active work.
Preview fetches and prunes remote-tracking refs; it is not purely read-only.

Run without concurrent local Git writers. Explicit keeps do not replace that
requirement. If activity prevents safe execution, finish independent inspection
and report the deferred operation.

Preserve the script's merged-PR proof, current-tip checks, recovery bundle,
expected-OID deletion guards, and final verification. A merged PR name alone
does not prove a subsequently advanced branch is removable.

Treat skipped branches as retained pending investigation. Do not force deletion
to make the report empty.

If main synchronization remains authorized, use a fast-forward operation that
preserves the active checkout. Check other worktree attachments first. Do not
reset or switch another task's checkout.

Resolve executable failures with cheap probes and a supported executable
override; avoid unrelated global configuration changes.

## Investigate Worktree Leftovers Separately

A prunable registration does not prove its directory is absent.

Inspect the exact path, Git link, branch tip, staged changes, tracked changes,
untracked files, size, age, and ownership. Distinguish unpublished source work
from missing tracked files and generated caches.

The branch script preserves directories. Branch deletion does not authorize
directory deletion.

Follow the temporary workspace standard before deleting prior-task files:
present exact paths, size, age, and safety evidence, then obtain the required
action-time confirmation. Recreated directories require fresh assessment.
Never broad-delete the shared temporary root.

If automatic approval review rejects deletion, identify the rejected action and
reason. Complete unaffected work without disguising or repeatedly retrying the
same deletion.

## Clean Docker From An Exact Plan

Confirm the Docker context and inventory image IDs, tags, creation times,
container image use, mounts, volumes, and disk usage.

Inspect only necessary fields. Do not dump container environments or credentials.

Protect:

- Images referenced by any container.
- Persistent volumes and unrelated project resources.
- Current toolchain images required by main and active checkouts.
- Current service images and images whose ownership or use is uncertain.

Derive toolchain identities from current repository producers and contracts.
Do not hard-code historical hashes. Age and a missing tag are insufficient
evidence of obsolescence.

Create an exact removal manifest with immutable IDs, observed tags, and reasons.
Recheck container use and tag identity immediately before removal. Use image
removal without force and record failures or skipped targets.

Preserve containers and volumes unless their removal is separately within
authorized scope and supported by ownership and data-retention evidence.
Avoid blanket system pruning.

Check live build and validation activity before pruning unused cache. Follow
the existing coordination contract; stale PID text alone proves neither
activity nor inactivity. Never delete coordination locks. Preserve cache when
activity is present or uncertain.

## Verify And Report

For nonempty plans, verify that every planned row was processed. A successful
exit or printed marker does not establish completion.

Reinspect local and remote refs, protected worktree state, retained images,
containers, mounts, and volumes. Separate removed tags from removed unique
image IDs.

Use before-and-after Docker accounting for approximate reclaimed storage.
Do not sum virtual image sizes or imply that logical reclamation equals host
disk compaction.

Report completed counts, requested main status, retained or deferred targets,
remaining authorization needs, and recovery/evidence directories. State
uncertainty directly.
