#!/usr/bin/env bash
# Requires Bash 3.2+, Git 2.31+, gh and jq. Run without concurrent local Git writers.
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: bash scripts/tidy_branches.sh [--apply] [--remote NAME] [--keep BRANCH] [--https]

Preview cleanup by default; --apply deletes verified merged PR branches.
--keep is repeatable. --https uses gh-authenticated HTTPS without changing Git config.
Requires Git, authenticated GitHub CLI (gh), and jq; supports github.com remotes.
Set GIT_BIN to select a working Git executable. Reports and recovery bundles go under
${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/_scratch/.
Even a preview fetches/prunes remote-tracking refs and writes a report. It does not
delete branches or detach worktrees. No worktree directories are ever removed.
HELP
}
die() { printf 'tidy_branches: %s\n' "$*" >&2; exit 1; }
apply=false
https=false
remote=origin
kept=(main)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) apply=true; shift ;;
    --https) https=true; shift ;;
    --remote|--keep)
      [[ $# -ge 2 && -n "$2" ]] || die "$1 requires a value"
      if [[ "$1" == --remote ]]; then remote=$2; else kept+=("$2"); fi
      shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
GIT_BIN=${GIT_BIN:-git}
for tool in "$GIT_BIN" gh jq; do command -v "$tool" >/dev/null || die "missing tool: $tool"; done
g() { "$GIT_BIN" "$@"; }
g --version >/dev/null
root=$(g rev-parse --show-toplevel)
cd "$root"
[[ $(g rev-parse --is-shallow-repository) == false ]] || die 'full Git history is required'
g check-ref-format "refs/remotes/$remote/check" || die 'invalid remote name'
fetch_url=$(g remote get-url --all "$remote")
push_url=$(g remote get-url --push --all "$remote")
[[ "$fetch_url" == "$push_url" && "$fetch_url" != *$'\n'* ]] || die 'requires one identical fetch/push URL'
repo_json=$(gh repo view "$fetch_url" --json nameWithOwner,defaultBranchRef,url)
repo=$(jq -er '.nameWithOwner' <<< "$repo_json")
default=$(jq -er '.defaultBranchRef.name' <<< "$repo_json")
github_url=$(jq -er '.url' <<< "$repo_json")
[[ "$github_url" == "https://github.com/$repo" ]] || die 'only github.com is supported'
kept+=("$default")
transport=$fetch_url
if "$https"; then transport="$github_url.git"; fi
net() {
  if "$https"; then
    g -c credential.helper= -c 'credential.helper=!gh auth git-credential' "$@"
  else
    g "$@"
  fi
}
work_root=${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}
mkdir -p "$work_root/_scratch"
report=$(mktemp -d "$work_root/_scratch/branch-cleanup-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
printf 'Records: %s\n' "$report"
trap 'printf "Cleanup stopped. Inspect retained records: %s\n" "$report" >&2' ERR
net fetch --no-tags --prune "$transport" "+refs/heads/*:refs/remotes/$remote/*"
base=$(g rev-parse "refs/remotes/$remote/$default^{commit}")
current=$(g symbolic-ref -q HEAD || test "$?" -eq 1)
g worktree list --porcelain > "$report/worktrees-before.txt"
g status --porcelain=v1 --branch > "$report/status-before.txt"

github_snapshot() {
  local prefix=$1
  gh api --paginate --slurp "repos/$repo/branches?per_page=100" | jq 'add' > "$prefix-branches.json"
  gh api --paginate --slurp "repos/$repo/pulls?state=all&per_page=100" | jq 'add' > "$prefix-prs.json"
  jq -e 'type == "array" and all(.[]; (.name | type == "string") and
    (.protected | type == "boolean") and (.commit.sha | test("^[0-9a-f]{40}$")))' "$prefix-branches.json" >/dev/null
  jq -e 'type == "array"' "$prefix-prs.json" >/dev/null
  jq -r --arg repo "$repo" '.[] | select(.state == "open") |
    (if .head.repo.full_name == $repo then .head.ref else empty end),
    (if .base.repo.full_name == $repo then .base.ref else empty end)' "$prefix-prs.json" > "$prefix-open.txt"
}
github_snapshot "$report/before"
branch_sha() { jq -r --arg b "$2" '.[] | select(.name == $b) | .commit.sha' "$1"; }
is_open() { grep -Fxq -- "$2" "$1"; }
is_protected() { jq -e --arg b "$2" 'any(.[]; .name == $b and .protected)' "$1" >/dev/null; }

# Read NUL-delimited worktree records so spaces and newlines in paths remain intact.
# Returns a reason to keep the branch, and the sole clean worktree path if present.
worktree_state() {
  local target=$1 field path='' ref='' blocked='' count=0 state
  wt_path=''
  wt_reason=''
  g worktree list --porcelain -z > "$report/worktrees-current.bin"
  while IFS= read -r -d '' field; do
    case "$field" in
      'worktree '*) path=${field#worktree } ;;
      'branch '*) ref=${field#branch } ;;
      locked*|prunable*) blocked=$field ;;
      '')
        if [[ "$ref" == "refs/heads/$target" ]]; then
          count=$((count + 1))
          wt_path=$path
          if [[ -n "$blocked" || ! -d "$path" ]]; then
            wt_reason='locked or missing worktree'
          elif [[ -n $(g -C "$path" status --porcelain=v1 --untracked-files=all) ]]; then
            wt_reason='dirty worktree'
          else
            for state in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply BISECT_START; do
              if [[ -e $(g -C "$path" rev-parse --path-format=absolute --git-path "$state") ]]; then
                wt_reason='Git operation in progress'
              fi
            done
          fi
        fi
        path=''; ref=''; blocked='' ;;
    esac
  done < "$report/worktrees-current.bin"
  if [[ "$count" -gt 1 ]]; then wt_reason='multiple worktrees'; fi
}

g for-each-ref --format='%(refname:strip=2)' refs/heads > "$report/names.txt"
jq -r '.[].name' "$report/before-branches.json" >> "$report/names.txt"
LC_ALL=C sort -u "$report/names.txt" -o "$report/names.txt"
printf 'branch\tlocal_sha\tremote_sha\tpr\tmerged_head\tmerge_commit\n' > "$report/plan.tsv"
: > "$report/keep.tsv"
bundle_refs=()
while IFS= read -r branch; do
  g check-ref-format "refs/heads/$branch" || die 'invalid branch from GitHub'
  reason=''
  for keep in "${kept[@]}"; do if [[ "$branch" == "$keep" ]]; then reason='kept branch'; fi; done
  if [[ "$current" == "refs/heads/$branch" ]]; then reason='current branch'; fi
  if is_protected "$report/before-branches.json" "$branch"; then reason='protected branch'; fi
  if is_open "$report/before-open.txt" "$branch"; then reason='open PR'; fi
  worktree_state "$branch"
  if [[ -n "$wt_reason" ]]; then reason=$wt_reason; fi
  if [[ -n "$reason" ]]; then printf '%s\t%s\n' "$branch" "$reason" >> "$report/keep.tsv"; continue; fi
  # for-each-ref patterns also match descendants: ask for the exact ref only.
  local_sha=$(g for-each-ref --format='%(refname) %(objectname)' refs/heads |
    awk -v ref="refs/heads/$branch" '$1 == ref {print $2}')
  remote_sha=$(branch_sha "$report/before-branches.json" "$branch")
  if [[ -n "$remote_sha" ]]; then
    [[ $(g rev-parse "refs/remotes/$remote/$branch") == "$remote_sha" ]] || die "remote changed during inspection: $branch"
  fi
  jq -r --arg b "$branch" --arg repo "$repo" '.[] | select(.merged_at != null and
    .head.ref == $b and .head.repo.full_name == $repo and .base.repo.full_name == $repo) |
    [.number, .head.sha, .merge_commit_sha] | @tsv' "$report/before-prs.json" > "$report/pr-candidates.tsv"
  proof=''
  while IFS=$'\t' read -r pr head merge; do
    [[ "$pr" =~ ^[0-9]+$ && "$head" =~ ^[0-9a-f]{40}$ && "$merge" =~ ^[0-9a-f]{40}$ ]] || die 'invalid PR revision evidence'
    [[ -z "$remote_sha" || "$head" == "$remote_sha" ]] || continue
    g cat-file -e "$head^{commit}" 2>/dev/null || continue
    g cat-file -e "$merge^{commit}" 2>/dev/null || continue
    g merge-base --is-ancestor "$merge" "$base" || continue
    [[ -z "$local_sha" ]] || g merge-base --is-ancestor "$local_sha" "$head" || continue
    if g merge-base --is-ancestor "$head" "$base" || g diff --quiet "$head" "$merge"; then
      proof="$pr"$'\t'"$head"$'\t'"$merge"
      break
    fi
  done < "$report/pr-candidates.tsv"
  if [[ -z "$proof" ]]; then
    printf '%s\tno verified merged PR at this tip\n' "$branch" >> "$report/keep.tsv"
    continue
  fi
  printf '%s\t%s\t%s\t%s\n' "$branch" "${local_sha:--}" "${remote_sha:--}" "$proof" >> "$report/plan.tsv"
  if [[ -n "$local_sha" ]]; then bundle_refs+=("refs/heads/$branch"); fi
  if [[ -n "$remote_sha" ]]; then bundle_refs+=("refs/remotes/$remote/$branch"); fi
done < "$report/names.txt"
cat "$report/plan.tsv"
printf '\nRetained branches:\n'
cat "$report/keep.tsv"
if ! "$apply"; then printf '\nPreview only. Rerun with --apply to execute a freshly verified plan.\n'; exit 0; fi
if [[ $(wc -l < "$report/plan.tsv") -eq 1 ]]; then printf 'No branches to delete.\n'; exit 0; fi

# A full recovery bundle is verified before any branch deletion, including squash merges.
g bundle create "$report/deleted-branches.bundle" "${bundle_refs[@]}"
g bundle verify "$report/deleted-branches.bundle"
github_snapshot "$report/recheck"
[[ $(gh api "repos/$repo" --jq .default_branch) == "$default" ]] || die 'default branch changed; rerun'
[[ $(branch_sha "$report/recheck-branches.json" "$default") == "$base" ]] || die 'default branch advanced; rerun'
tail -n +2 "$report/plan.tsv" > "$report/actions.tsv"
leases=()
deletions=()
while IFS=$'\t' read -r branch local_sha remote_sha pr head merge; do
  is_protected "$report/recheck-branches.json" "$branch" && die "newly protected: $branch"
  is_open "$report/recheck-open.txt" "$branch" && die "new open PR: $branch"
  actual=$(branch_sha "$report/recheck-branches.json" "$branch")
  [[ "${actual:--}" == "$remote_sha" ]] || die "remote changed: $branch"
  worktree_state "$branch"
  [[ -z "$wt_reason" ]] || die "$branch: $wt_reason"
  [[ $(g symbolic-ref -q HEAD || test "$?" -eq 1) != "refs/heads/$branch" ]] || die "now current: $branch"
  actual=$(g for-each-ref --format='%(refname) %(objectname)' refs/heads |
    awk -v ref="refs/heads/$branch" '$1 == ref {print $2}')
  [[ "${actual:--}" == "$local_sha" ]] || die "local branch changed: $branch"
  if [[ "$remote_sha" != - ]]; then
    leases+=("--force-with-lease=refs/heads/$branch:$remote_sha")
    deletions+=(":refs/heads/$branch")
  fi
done < "$report/actions.tsv"
if [[ ${#deletions[@]} -gt 0 ]]; then
  net push --atomic "${leases[@]}" "$transport" "${deletions[@]}" 2>&1 | tee "$report/remote-delete.log"
  net fetch --no-tags --prune "$transport" "+refs/heads/*:refs/remotes/$remote/*"
fi
: > "$report/local-delete.log"
while IFS=$'\t' read -r branch local_sha remote_sha pr head merge; do
  [[ "$local_sha" != - ]] || continue
  worktree_state "$branch"
  [[ -z "$wt_reason" ]] || die "$branch: $wt_reason"
  [[ $(g symbolic-ref -q HEAD || test "$?" -eq 1) != "refs/heads/$branch" ]] || die "now current: $branch"
  if [[ -n "$wt_path" ]]; then
    [[ $(g -C "$wt_path" rev-parse HEAD) == "$local_sha" ]] || die "worktree advanced: $branch"
    g -C "$wt_path" switch --detach "$local_sha"
  fi
  # Compare-and-delete prevents dropping a tip advanced since inspection.
  g update-ref --no-deref -d "refs/heads/$branch" "$local_sha"
  printf '%s\t%s\n' "$branch" "$local_sha" >> "$report/local-delete.log"
done < "$report/actions.tsv"
net ls-remote --heads "$transport" > "$report/remote-after.txt"
g for-each-ref --format='%(refname) %(objectname)' refs/heads > "$report/local-after.txt"
while IFS=$'\t' read -r branch local_sha remote_sha pr head merge; do
  if [[ "$remote_sha" != - ]] && awk '{print $2}' "$report/remote-after.txt" | grep -Fxq "refs/heads/$branch"; then
    die "remote branch still exists or was recreated: $branch"
  fi
  if [[ "$local_sha" != - ]] && awk '{print $1}' "$report/local-after.txt" | grep -Fxq "refs/heads/$branch"; then
    die "local branch still exists or was recreated: $branch"
  fi
done < "$report/actions.tsv"
g worktree list --porcelain > "$report/worktrees-after.txt"
printf 'Cleanup verified. Records and recovery bundle: %s\n' "$report"
