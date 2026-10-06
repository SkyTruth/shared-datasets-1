"""Exercise the Bash cleanup against real local Git remotes; only GitHub is mocked."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/tidy_branches.sh"
GIT = os.environ.get("GIT_BIN", "git")


class TidyBranchesTests(unittest.TestCase):
    def setUp(self):
        root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1"))
        root = root / "_scratch" / "branch-cleanup-tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.bare = self.root / "origin.git"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "github.json"
        self.data = {"prs": [], "protected": ["main"], "branch_reads": 0}
        self.env = {
            **os.environ, "GIT_BIN": GIT, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "SHARED_DATASETS_WORKDIR": str(self.root / "reports"),
            "TEST_GITHUB_STATE": str(self.state), "TEST_REMOTE": str(self.bare),
        }
        self.git("init", "-q", "--bare", str(self.bare))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Branch Test")
        self.git("config", "user.email", "branch-test@example.invalid")
        self.commit("base", "base")
        self.git("remote", "add", "origin", str(self.bare))
        self.git("push", "-qu", "origin", "main")
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, subprocess, sys
p = pathlib.Path(os.environ["TEST_GITHUB_STATE"])
d = json.loads(p.read_text())
git = os.environ["GIT_BIN"]
remote = os.environ["TEST_REMOTE"]
args = sys.argv[1:]
if args[:2] == ["repo", "view"]:
    print(json.dumps({"nameWithOwner":"fixture/repo", "defaultBranchRef":{"name":"main"}, "url":"https://github.com/fixture/repo"}))
elif any("/branches?" in a for a in args):
    d["branch_reads"] += 1
    if d.get("api_fail"):
        sys.exit(1)
    if d["branch_reads"] == 2 and "advance" in d:
        name, sha = d["advance"]
        subprocess.run([git,"--git-dir="+remote,"update-ref","refs/heads/"+name,sha], check=True)
    if d["branch_reads"] == 2 and "protect_on_recheck" in d:
        d["protected"].append(d["protect_on_recheck"])
    p.write_text(json.dumps(d))
    refs = subprocess.check_output([git,"--git-dir="+remote,"for-each-ref","--format=%(refname:strip=2) %(objectname)","refs/heads"], text=True)
    print(json.dumps([[{"name": name, "commit":{"sha":sha}, "protected": name in d["protected"]} for name,sha in (line.split() for line in refs.splitlines())]]))
elif any("/pulls?" in a for a in args):
    print(json.dumps([d["prs"]]))
elif args[-2:] == ["--jq", ".default_branch"]:
    print("main")
else:
    raise SystemExit("unexpected gh arguments: " + repr(args))
''')
        gh.chmod(0o755)

    def git(self, *args, cwd=None):
        result = subprocess.run([GIT, *args], cwd=cwd or self.repo, env=self.env,
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def commit(self, name, content):
        (self.repo / name).write_text(content)
        self.git("add", name)
        self.git("commit", "-qm", content)
        return self.git("rev-parse", "HEAD")

    def topic(self, name="topic", *, merged=True, squash=False):
        self.git("switch", "-qc", name)
        head = self.commit(name.replace("/", "-"), name)
        self.git("push", "-qu", "origin", name)
        self.git("switch", "-q", "main")
        merge = None
        if merged:
            if squash:
                self.git("merge", "--squash", name)
                self.git("commit", "-qm", "squashed")
            else:
                self.git("merge", "--no-ff", "-qm", "merged", name)
            merge = self.git("rev-parse", "HEAD")
            self.git("push", "-q", "origin", "main")
        self.data["prs"].append({
            "number": len(self.data["prs"]) + 1,
            "state": "closed" if merged else "open", "merged_at": "date" if merged else None,
            "head": {"ref": name, "sha": head, "repo": {"full_name": "fixture/repo"}},
            "base": {"ref": "main", "repo": {"full_name": "fixture/repo"}},
            "merge_commit_sha": merge,
        })
        return head

    def run_cleanup(self, *args, success=True):
        self.state.write_text(json.dumps(self.data))
        result = subprocess.run(["bash", str(SCRIPT), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def local_names(self):
        return self.git("for-each-ref", "--format=%(refname:strip=2)", "refs/heads").splitlines()

    def remote_names(self):
        return self.git("--git-dir=" + str(self.bare), "for-each-ref", "--format=%(refname:strip=2)", "refs/heads").splitlines()

    def test_preview_preserves_branches_and_worktrees(self):
        head = self.topic()
        wt = self.root / "worktree with spaces"
        self.git("worktree", "add", str(wt), "topic")
        result = self.run_cleanup()
        self.assertIn("Preview only", result.stdout)
        self.assertIn("topic", self.local_names())
        self.assertIn("topic", self.remote_names())
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=wt), "topic")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=wt), head)

    def test_apply_detaches_clean_worktree_and_preserves_dirty_main(self):
        head = self.topic()
        wt = self.root / "worktree with\nnewline"
        self.git("worktree", "add", str(wt), "topic")
        (self.repo / "base").write_text("uncommitted main")
        self.run_cleanup("--apply")
        self.assertEqual(self.local_names(), ["main"])
        self.assertEqual(self.remote_names(), ["main"])
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=wt), head)
        self.assertEqual(self.git("status", "--porcelain", cwd=wt), "")
        self.assertEqual((self.repo / "base").read_text(), "uncommitted main")
        bundle = next((self.root / "reports").rglob("*.bundle"))
        self.git("bundle", "verify", str(bundle))

    def test_squash_merge_is_recoverable(self):
        head = self.topic(squash=True)
        self.run_cleanup("--apply")
        self.assertEqual(self.local_names(), ["main"])
        bundle = next((self.root / "reports").rglob("*.bundle"))
        self.assertIn(head, self.git("bundle", "list-heads", str(bundle)))

    def test_local_tip_behind_merged_pr_head_is_deleted(self):
        base = self.git("rev-parse", "main")
        self.topic()
        self.git("update-ref", "refs/heads/topic", base)
        self.run_cleanup("--apply")
        self.assertEqual(self.local_names(), ["main"])

    def test_unverified_squash_tree_and_new_open_pr_base_are_kept(self):
        head = self.topic(squash=True)
        self.data["prs"][0]["merge_commit_sha"] = self.git("rev-parse", "main^")
        self.topic("second")
        self.data["prs"].append({
            "state": "open", "merged_at": None,
            "head": {"ref": "unrelated", "repo": {"full_name": "fixture/repo"}},
            "base": {"ref": "second", "repo": {"full_name": "fixture/repo"}},
        })
        self.run_cleanup("--apply")
        self.assertEqual(self.git("rev-parse", "topic"), head)
        self.assertIn("second", self.remote_names())

    def test_dirty_locked_current_open_and_protected_branches_are_kept(self):
        for name in ("dirty", "locked", "current", "protected", "explicit", "open"):
            self.topic(name, merged=name != "open")
        for name in ("dirty", "locked"):
            wt = self.root / name
            self.git("worktree", "add", str(wt), name)
            if name == "dirty":
                (wt / "untracked").write_text("keep me")
            else:
                self.git("worktree", "lock", str(wt))
        self.git("switch", "current")
        self.data["protected"].append("protected")
        before = self.local_names()
        self.run_cleanup("--apply", "--keep", "explicit")
        self.assertEqual(self.local_names(), before)
        self.assertEqual(self.remote_names(), before)

    def test_local_ahead_and_new_branch_at_main_are_kept(self):
        self.topic()
        self.git("switch", "topic")
        self.commit("extra", "unpublished")
        self.git("switch", "main")
        self.git("branch", "new-work")
        self.run_cleanup("--apply")
        self.assertIn("topic", self.local_names())
        self.assertIn("topic", self.remote_names())
        self.assertIn("new-work", self.local_names())

    def test_local_only_merged_branch_is_deleted(self):
        self.topic()
        self.git("push", "origin", "--delete", "topic")
        self.run_cleanup("--apply")
        self.assertEqual(self.local_names(), ["main"])

    def test_remote_only_merged_branch_is_deleted(self):
        self.topic()
        self.git("branch", "-d", "topic")
        self.run_cleanup("--apply")
        self.assertEqual(self.remote_names(), ["main"])

    def test_api_failure_and_new_protection_fail_before_deletion(self):
        self.topic()
        self.data["api_fail"] = True
        self.run_cleanup("--apply", success=False)
        del self.data["api_fail"]
        self.data["protect_on_recheck"] = "topic"
        self.run_cleanup("--apply", success=False)
        self.assertIn("topic", self.local_names())
        self.assertIn("topic", self.remote_names())

    def test_atomic_remote_lease_failure_preserves_all_local_branches(self):
        self.topic("one")
        self.topic("two")
        # The remote advances after advertisement, inside Git's pre-push hook.
        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.write_text('#!/bin/sh\n"$GIT_BIN" --git-dir="$TEST_REMOTE" update-ref refs/heads/one refs/heads/main\n')
        hook.chmod(0o755)
        before = self.local_names()
        result = self.run_cleanup("--apply", success=False)
        self.assertIn("rejected", result.stderr + result.stdout)
        self.assertEqual(self.local_names(), before)
        self.assertEqual(self.remote_names(), before)

    def test_changed_remote_during_recheck_is_not_deleted(self):
        self.topic()
        self.data["advance"] = ["topic", self.git("rev-parse", "main")]
        self.run_cleanup("--apply", success=False)
        self.assertIn("topic", self.local_names())
        self.assertIn("topic", self.remote_names())

    def test_local_compare_and_delete_rejects_a_concurrently_advanced_tip(self):
        self.topic()
        advance = self.git("commit-tree", "main^{tree}", "-p", "main", "-m", "concurrent work")
        wrapper = self.bin / "racing-git"
        wrapper.write_text(f"#!{sys.executable}\n" + '''
import os, subprocess, sys
real = os.environ["REAL_GIT"]
if sys.argv[1:4] == ["update-ref", "--no-deref", "-d"]:
    subprocess.run([real, "update-ref", sys.argv[4], os.environ["ADVANCE_SHA"]], check=True)
os.execvp(real, [real, *sys.argv[1:]])
''')
        wrapper.chmod(0o755)
        self.env.update(GIT_BIN=str(wrapper), REAL_GIT=GIT, ADVANCE_SHA=advance)
        self.run_cleanup("--apply", success=False)
        self.assertEqual(self.git("rev-parse", "topic"), advance)

    def test_missing_worktree_is_preserved(self):
        self.topic()
        wt = self.root / "missing"
        self.git("worktree", "add", str(wt), "topic")
        shutil.rmtree(wt)
        self.run_cleanup("--apply")
        self.assertIn("topic", self.local_names())
        self.assertIn("topic", self.remote_names())


if __name__ == "__main__":
    unittest.main()
