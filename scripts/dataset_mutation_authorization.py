#!/usr/bin/env python3
"""Bind mutation documents to accepted revisions and immutable Actions handoffs.

Only this module talks to GitHub. Plan schemas/serialization live in
reviewed_dataset_plan; storage transactions and receipts belong to the publisher.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import reviewed_dataset_plan as plans

Error = plans.PlanValidationError
REVIEWER = "jonaraphael"
WORKFLOW = ".github/workflows/publish-dataset.yml"
ENVELOPE_FILE = "authorization.json"
SHA_RE = re.compile(r"[0-9a-f]{40}")
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


def require(condition: Any, message: str) -> None:
    if not condition:
        raise Error(message)


def sha(value: Any) -> str:
    require(isinstance(value, str) and SHA_RE.fullmatch(value), "expected a full commit SHA")
    return value


def positive_int(value: Any) -> int:
    require(type(value) is int and value > 0, "expected a positive integer identifier")
    return value


class GitHub:
    """Thin gh transport. --paginate follows every Link; --slurp preserves pages."""

    def get(self, path: str) -> Any:
        return plans.strict_json_loads(subprocess.check_output(["gh", "api", path]))

    def pages(self, path: str, *, field: str | None = None) -> list[dict[str, Any]]:
        pages = plans.strict_json_loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp", path]))
        require(isinstance(pages, list) and bool(pages), "missing paginated API response")
        total = None
        if field is not None:
            require(all(isinstance(page, dict) for page in pages), "invalid named API pages")
            total = pages[0].get("total_count")
            require(type(total) is int and all(page.get("total_count") == total for page in pages), "inconsistent API total_count")
            pages = [page.get(field) for page in pages]
        require(all(isinstance(page, list) for page in pages), "invalid API pages")
        rows = [row for page in pages for row in page]
        require(all(isinstance(row, dict) for row in rows), "invalid API page entry")
        require(total is None or len(rows) == total, "incomplete API enumeration")
        return rows

    def archive(self, repository: str, artifact_id: int) -> bytes:
        return subprocess.check_output(["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}/zip"])


def repo_matches(payload: Any, repository: dict[str, Any]) -> bool:
    return isinstance(payload, dict) and all(payload.get(k) == repository[k] for k in ("id", "full_name"))


def repository_context(event: dict[str, Any]) -> dict[str, Any]:
    repo = event["repository"]
    positive_int(repo["id"])
    require(
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo["full_name"]),
        "invalid repository",
    )
    require(repo.get("default_branch") == "main", "expected main default branch")
    return {"id": repo["id"], "full_name": repo["full_name"], "default_branch": "main"}


def validate_pr(pr: dict[str, Any], repository: dict[str, Any], number: int, *, merged: bool) -> None:
    require(pr.get("number") == positive_int(number), "PR number mismatch")
    require(
        repo_matches(pr.get("head", {}).get("repo"), repository),
        "PR head repository mismatch",
    )
    require(
        repo_matches(pr.get("base", {}).get("repo"), repository),
        "PR base repository mismatch",
    )
    require(pr["base"].get("ref") == "main", "PR must target main")
    require(pr.get("draft") is False, "draft PR cannot authorize mutation")
    sha(pr["head"].get("sha"))
    if merged:
        require(
            pr.get("state") == "closed" and pr.get("merged") is True and pr.get("merged_at"),
            "PR must be closed and merged",
        )
        sha(pr.get("merge_commit_sha"))
    else:
        require(pr.get("state") == "open", "preview requires an open PR")


def effective_acceptance(pr: dict[str, Any], reviews: list[dict[str, Any]], repository: str) -> dict[str, Any]:
    head = sha(pr["head"]["sha"])
    if pr.get("user", {}).get("login") == REVIEWER:
        return {"kind": "self_authored_merge", "reviewed_commit": head}
    expected_url = f"https://api.github.com/repos/{repository}/pulls/{pr['number']}"
    seen: set[int] = set()
    decisive = []
    for review in reviews:
        review_id = positive_int(review.get("id"))
        require(review_id not in seen, "duplicate review ID")
        seen.add(review_id)
        require(
            review.get("pull_request_url") == expected_url,
            "review PR association mismatch",
        )
        state = review.get("state")
        require(
            state in {"PENDING", "COMMENTED", "APPROVED", "CHANGES_REQUESTED", "DISMISSED"},
            "unknown review state",
        )
        if review.get("user", {}).get("login") != REVIEWER or state in {
            "PENDING",
            "COMMENTED",
        }:
            continue
        try:
            submitted = dt.datetime.fromisoformat(review["submitted_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError) as exc:
            raise Error("decisive review lacks valid submitted_at") from exc
        require(submitted.tzinfo is not None, "review timestamp needs timezone")
        decisive.append((submitted, review_id, review))
    require(decisive, f"no decisive review from {REVIEWER}")
    review = max(decisive, key=lambda item: item[:2])[2]
    require(review["state"] == "APPROVED", "latest effective review is not APPROVED")
    require(review.get("commit_id") == head, "approval must target the exact final head")
    return {"kind": "review", "review_id": review["id"], "reviewed_commit": head}


def git_file(api: GitHub, repository: str, revision: str, path: str) -> tuple[bytes, str]:
    tree = api.get(f"repos/{repository}/git/trees/{sha(revision)}?recursive=1")
    require(tree.get("truncated") is False, "Git tree enumeration truncated")
    matches = [entry for entry in tree["tree"] if entry.get("path") == path]
    require(len(matches) == 1, f"missing/ambiguous file at {revision}: {path}")
    entry = matches[0]
    require(
        entry.get("mode") == "100644" and entry.get("type") == "blob",
        "plan/context must be a regular data file",
    )
    blob_sha = sha(entry.get("sha"))
    blob = api.get(f"repos/{repository}/git/blobs/{blob_sha}")
    require(
        blob.get("sha") == blob_sha and blob.get("encoding") == "base64",
        "invalid Git blob response",
    )
    try:
        raw = base64.b64decode("".join(blob["content"].split()), validate=True)
    except (ValueError, KeyError) as exc:
        raise Error("invalid Git blob bytes") from exc
    require(len(raw) == blob.get("size"), "Git blob size mismatch")
    import hashlib

    require(
        hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() == blob_sha,
        "Git blob hash mismatch",
    )
    return raw, blob_sha


def discover_document(
    api: GitHub, pr: dict[str, Any], repository: str, *, merged: bool, check_body: bool
) -> tuple[dict[str, Any], str, str] | None:
    files = api.pages(f"repos/{repository}/pulls/{pr['number']}/files?per_page=100")
    require(
        len(files) == pr.get("changed_files") and len(files) < 3000,
        "incomplete PR files enumeration",
    )
    names = [item.get("filename") for item in files]
    require(len(names) == len(set(names)), "duplicate PR file")
    candidates = [
        item
        for item in files
        if str(item.get("filename", "")).startswith(plans.PLAN_DIRECTORY + "/") and item["filename"].endswith(".json")
    ]
    if not candidates:
        body = pr.get("body") or ""
        if check_body:
            require(
                not any(plans.find_fenced_json(body, f"shared-datasets-{kind}-plan") for kind in ("publish", "delete")),
                "legacy body-only plan: prepare a checked-in document in a new reviewed commit/PR",
            )
        return None
    require(
        len(candidates) == 1 and candidates[0].get("status") == "added",
        "PR must add exactly one immutable plan document",
    )
    path = candidates[0]["filename"]
    raw, blob_sha = git_file(api, repository, pr["head"]["sha"], path)
    require(candidates[0].get("sha") == blob_sha, "PR file blob does not match head")
    document = plans.read_document(raw, path=path)
    if merged:
        merge_raw, merge_blob = git_file(api, repository, pr["merge_commit_sha"], path)
        require(
            (merge_raw, merge_blob) == (raw, blob_sha),
            "head and merge plan bytes differ",
        )
    if check_body:
        plans.check_rendered_body(pr.get("body") or "", document)
    return document, path, blob_sha


def executor_contract(envelope: dict[str, Any]) -> dict[str, str]:
    return {
        "trusted_executor_sha": sha(envelope["trusted_executor_sha"]),
        "finalization_version": envelope["document"]["finalization_version"],
    }


def approval_identity(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        key: envelope[key]
        for key in (
            "repository",
            "pr_number",
            "head_sha",
            "merge_sha",
            "normalized_plan_sha256",
        )
    }


def identity_digests(envelope: dict[str, Any]) -> dict[str, str]:
    identity = approval_identity(envelope)
    return {
        "proposal_key": plans.sha256(plans.canonical_bytes(identity)),
        "execution_contract_sha256": plans.sha256(
            plans.canonical_bytes({**identity, "executor_contract": executor_contract(envelope)})
        ),
    }


def require_same_execution_contract(original: dict[str, Any], attempt: dict[str, Any]) -> None:
    require(
        approval_identity(original) == approval_identity(attempt),
        "attempt changes immutable approval identity",
    )
    require(
        executor_contract(original) == executor_contract(attempt),
        "executor contract changed; resume with original pinned executor, never replan this transaction",
    )


def workflow_run(
    api: GitHub, repository: dict[str, Any], run_id: int, attempt: int, *, success: bool
) -> dict[str, Any]:
    repo = repository["full_name"]
    run = api.get(f"repos/{repo}/actions/runs/{positive_int(run_id)}/attempts/{positive_int(attempt)}")
    workflow = api.get(f"repos/{repo}/actions/workflows/publish-dataset.yml")
    require(
        workflow.get("path") == WORKFLOW and run.get("workflow_id") == positive_int(workflow.get("id")),
        "wrong source workflow identity",
    )
    require(run.get("path") == WORKFLOW, "wrong source workflow path")
    require(
        repo_matches(run.get("repository"), repository) and repo_matches(run.get("head_repository"), repository),
        "wrong source run repository",
    )
    require(
        run.get("id") == run_id and run.get("run_attempt") == attempt,
        "wrong source run/attempt",
    )
    require(
        run.get("event") in {"pull_request", "workflow_dispatch"},
        "wrong source run event",
    )
    if run["event"] == "workflow_dispatch":
        require(run.get("head_branch") == "main", "dispatch source run must use main")
    sha(run.get("head_sha"))
    if success:
        require(
            run.get("status") == "completed" and run.get("conclusion") == "success",
            "source publication run did not succeed",
        )
    return run


def validate_envelope(envelope: Any) -> None:
    fields = {
        "outcome",
        "authorization_version",
        "repository",
        "pr_number",
        "head_sha",
        "merge_sha",
        "plan_path",
        "plan_blob_sha",
        "normalized_plan_sha256",
        "document",
        "acceptance",
        "trusted_executor_sha",
        "source_run",
        "proposal_key",
        "execution_contract_sha256",
    }
    require(isinstance(envelope, dict), "authorization must be an object")
    require(
        envelope.get("outcome") in {"mutation", "no_mutation"},
        "invalid authorization outcome",
    )
    if envelope["outcome"] == "no_mutation":
        fields = {
            "outcome",
            "authorization_version",
            "repository",
            "pr_number",
            "head_sha",
            "merge_sha",
            "trusted_executor_sha",
            "source_run",
        }
    plans.require_fields(envelope, fields, label="authorization envelope")
    require(
        set(envelope) == fields
        and type(envelope["authorization_version"]) is int
        and envelope["authorization_version"] == 1,
        "invalid authorization envelope fields/version",
    )
    require(
        envelope["repository"] == repository_context({"repository": envelope["repository"]}),
        "invalid repository context",
    )
    positive_int(envelope["pr_number"])
    source = envelope["source_run"]
    require(
        set(source) == {"id", "run_attempt", "workflow_id", "path", "head_sha", "event"},
        "invalid source run fields",
    )
    for key in ("id", "run_attempt", "workflow_id"):
        positive_int(source[key])
    require(
        source["path"] == WORKFLOW and source["event"] in {"pull_request", "workflow_dispatch"},
        "invalid source workflow identity",
    )
    sha(source["head_sha"])
    if source["event"] == "pull_request":
        require(
            source["head_sha"] in {envelope["head_sha"], envelope["merge_sha"]},
            "source PR run head mismatch",
        )
    for key in ("head_sha", "merge_sha", "trusted_executor_sha"):
        sha(envelope[key])
    if envelope["outcome"] == "no_mutation":
        return
    acceptance = envelope["acceptance"]
    allowed_acceptance = (
        {"kind", "reviewed_commit", "review_id"} if acceptance.get("kind") == "review" else {"kind", "reviewed_commit"}
    )
    require(
        set(acceptance) == allowed_acceptance and acceptance.get("kind") in {"review", "self_authored_merge"},
        "invalid acceptance identity",
    )
    require(
        acceptance["reviewed_commit"] == envelope["head_sha"],
        "acceptance head mismatch",
    )
    if acceptance["kind"] == "review":
        positive_int(acceptance["review_id"])
    document = plans.normalize_document(envelope["document"])
    require(document == envelope["document"], "envelope document is not normalized")
    require(
        envelope["normalized_plan_sha256"] == plans.sha256(plans.canonical_bytes(document)),
        "plan digest mismatch",
    )
    require(envelope["plan_path"] == plans.document_path(document), "plan path mismatch")
    for key in ("head_sha", "merge_sha", "plan_blob_sha", "trusted_executor_sha"):
        sha(envelope[key])
    require(
        all(envelope[k] == v for k, v in identity_digests(envelope).items()),
        "execution identity digest mismatch",
    )


def capture(api: GitHub, event: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    repository = repository_context(event)
    repo = repository["full_name"]
    require(
        env["GITHUB_REPOSITORY"] == repo and env["GITHUB_REF"] == "refs/heads/main",
        "execution must run from this repository/main",
    )
    require(
        env["GITHUB_WORKFLOW_REF"] == f"{repo}/{WORKFLOW}@refs/heads/main",
        "unexpected workflow ref",
    )
    executor = sha(env["GITHUB_WORKFLOW_SHA"])
    if env["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        require(env["GITHUB_ACTOR"] == REVIEWER, "dispatch is restricted to jonaraphael")
        value = event.get("inputs", {}).get("pr_number", "")
        require(
            isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value),
            "dispatch requires a positive PR number",
        )
        number = int(value)
    else:
        require(
            env["GITHUB_EVENT_NAME"] == "pull_request" and event.get("action") == "closed",
            "unexpected publication event",
        )
        number = positive_int(event["pull_request"]["number"])
    pr = api.get(f"repos/{repo}/pulls/{number}")
    validate_pr(pr, repository, number, merged=True)
    if env["GITHUB_EVENT_NAME"] == "pull_request":
        original = event["pull_request"]
        require(
            original.get("merged") is True
            and original.get("merge_commit_sha") == pr["merge_commit_sha"]
            and original["head"]["sha"] == pr["head"]["sha"],
            "event head/merge mismatch",
        )
        require(
            env["GITHUB_SHA"] == pr["merge_commit_sha"],
            "event must identify the actual merge commit",
        )
    compare = api.get(f"repos/{repo}/compare/{pr['merge_commit_sha']}...{executor}")
    require(
        compare.get("status") in {"ahead", "identical"}
        and compare.get("merge_base_commit", {}).get("sha") == pr["merge_commit_sha"],
        "merge is outside executor main lineage",
    )
    found = discover_document(api, pr, repo, merged=True, check_body=True)
    run = workflow_run(
        api,
        repository,
        int(env["GITHUB_RUN_ID"]),
        int(env["GITHUB_RUN_ATTEMPT"]),
        success=False,
    )
    expected_heads = (
        {pr["head"]["sha"], pr["merge_commit_sha"]} if run["event"] == "pull_request" else {env["GITHUB_SHA"]}
    )
    require(run["head_sha"] in expected_heads, "source run head mismatch")
    envelope = {
        "authorization_version": 1,
        "repository": repository,
        "pr_number": number,
        "head_sha": pr["head"]["sha"],
        "merge_sha": pr["merge_commit_sha"],
        "trusted_executor_sha": executor,
        "source_run": {key: run[key] for key in ("id", "run_attempt", "workflow_id", "path", "head_sha", "event")},
        "outcome": "mutation" if found else "no_mutation",
    }
    if found is None:
        validate_envelope(envelope)
        return envelope
    document, path, blob = found
    reviews = api.pages(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    envelope.update(
        {
            "plan_path": path,
            "plan_blob_sha": blob,
            "normalized_plan_sha256": plans.sha256(plans.canonical_bytes(document)),
            "document": document,
            "acceptance": effective_acceptance(pr, reviews, repo),
        }
    )
    envelope.update(identity_digests(envelope))
    validate_envelope(envelope)
    return envelope


def revalidate(api: GitHub, envelope: dict[str, Any]) -> None:
    validate_envelope(envelope)
    repo = envelope["repository"]["full_name"]
    pr = api.get(f"repos/{repo}/pulls/{envelope['pr_number']}")
    validate_pr(pr, envelope["repository"], envelope["pr_number"], merged=True)
    require(
        pr["head"]["sha"] == envelope["head_sha"] and pr["merge_commit_sha"] == envelope["merge_sha"],
        "authorized head/merge changed",
    )
    if envelope["outcome"] == "no_mutation":
        require(
            discover_document(api, pr, repo, merged=True, check_body=False) is None,
            "no-mutation outcome disagrees with committed PR files",
        )
        return
    reviews = api.pages(f"repos/{repo}/pulls/{pr['number']}/reviews?per_page=100")
    require(
        effective_acceptance(pr, reviews, repo) == envelope["acceptance"],
        "effective acceptance changed; run gate again",
    )
    for revision in (envelope["head_sha"], envelope["merge_sha"]):
        raw, blob = git_file(api, repo, revision, envelope["plan_path"])
        require(
            raw == plans.canonical_bytes(envelope["document"]) and blob == envelope["plan_blob_sha"],
            "authorized plan bytes changed",
        )


def artifact_name(run_id: int, attempt: int) -> str:
    return f"dataset-authorization-{run_id}-{attempt}"


def from_run(api: GitHub, event: dict[str, Any]) -> dict[str, Any]:
    repository = repository_context(event)
    source = event["workflow_run"]
    run = workflow_run(api, repository, source["id"], source["run_attempt"], success=True)
    require(
        all(source.get(k) == run[k] for k in ("id", "run_attempt", "workflow_id", "head_sha", "path", "event")),
        "upstream event/run mismatch",
    )
    repo = repository["full_name"]
    artifacts = api.pages(f"repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100", field="artifacts")
    matches = [a for a in artifacts if a.get("name") == artifact_name(run["id"], run["run_attempt"])]
    require(len(matches) == 1, "missing/ambiguous upstream authorization artifact")
    artifact = matches[0]
    require(
        artifact.get("expired") is False,
        "authorization artifact expired; no body fallback",
    )
    require(
        artifact.get("workflow_run", {}).get("id") == run["id"],
        "artifact belongs to another run",
    )
    require(type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_ARTIFACT_BYTES, "oversized/invalid authorization artifact")
    raw = api.archive(repo, positive_int(artifact.get("id")))
    require(
        len(raw) <= MAX_ARTIFACT_BYTES and artifact.get("digest") == "sha256:" + plans.sha256(raw),
        "artifact digest/size mismatch",
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(
            archive.namelist() == [ENVELOPE_FILE],
            "artifact must contain only authorization.json",
        )
        require(
            archive.getinfo(ENVELOPE_FILE).file_size <= MAX_ARTIFACT_BYTES,
            "oversized authorization",
        )
        data = archive.read(ENVELOPE_FILE)
    envelope = plans.strict_json_loads(data)
    validate_envelope(envelope)
    require(data == plans.canonical_bytes(envelope), "noncanonical authorization envelope")
    require(
        envelope["repository"] == repository
        and envelope["source_run"]
        == {k: run[k] for k in ("id", "run_attempt", "workflow_id", "path", "head_sha", "event")},
        "envelope source provenance mismatch",
    )
    revalidate(api, envelope)
    return envelope


def write_outputs(values: dict[str, Any], path: str | None) -> None:
    if path:
        with Path(path).open("a") as output:
            for key, value in values.items():
                require("\n" not in str(value), "invalid workflow output")
                output.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def save_envelope(envelope: dict[str, Any], directory: Path, output: str | None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    raw = plans.canonical_bytes(envelope)
    (directory / ENVELOPE_FILE).write_bytes(raw)
    write_outputs(
        {
            "has_publish_plan": "publish" in envelope.get("document", {}),
            "has_delete_plan": "delete" in envelope.get("document", {}),
            "envelope_sha256": plans.sha256(raw),
            "executor_sha": envelope["trusted_executor_sha"],
            "artifact_name": artifact_name(envelope["source_run"]["id"], envelope["source_run"]["run_attempt"]),
        },
        output,
    )


def extract_payloads(envelope: dict[str, Any], output: Path) -> None:
    if envelope["outcome"] == "no_mutation":
        return
    output.mkdir(parents=True, exist_ok=True)
    for kind in ("publish", "delete"):
        if kind in envelope["document"]:
            (output / f"{kind}-plan.json").write_bytes(plans.canonical_bytes(envelope["document"][kind]))
    # Existing catalog collector now sees only these immutable revisions.
    (output / "reviewed-pr-event.json").write_bytes(
        plans.canonical_bytes(
            {
                "pull_request": {
                    "head": {"sha": envelope["head_sha"]},
                    "merge_commit_sha": envelope["merge_sha"],
                }
            }
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "verify", "from-run", "preview"))
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--directory", type=Path, default=Path("authorization"))
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--expected-sha256")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)
    api = GitHub()
    try:
        if args.command in {"capture", "from-run", "preview"}:
            event = plans.strict_json_loads(Path(args.event_path).read_bytes())
            if args.command == "preview":
                repository = repository_context(event)
                pr = api.get(f"repos/{repository['full_name']}/pulls/{event['pull_request']['number']}")
                validate_pr(pr, repository, event["pull_request"]["number"], merged=False)
                require(
                    pr["head"]["sha"] == event["pull_request"]["head"]["sha"],
                    "preview head changed; rerun",
                )
                found = discover_document(api, pr, repository["full_name"], merged=False, check_body=True)
                document = found[0] if found else {}
                for kind in ("publish", "delete"):
                    if kind in document:
                        (args.output_dir / f"{kind}-plan.json").write_bytes(plans.canonical_bytes(document[kind]))
                write_outputs(
                    {
                        "has_publish_plan": "publish" in document,
                        "has_delete_plan": "delete" in document,
                    },
                    args.github_output,
                )
                return 0
            if args.command == "capture":
                require(subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == os.environ["GITHUB_WORKFLOW_SHA"], "checkout does not match workflow executor")
                envelope = capture(api, event, dict(os.environ))
            else:
                envelope = from_run(api, event)
            save_envelope(envelope, args.directory, args.github_output)
        else:
            require(
                sorted(p.name for p in args.directory.iterdir()) == [ENVELOPE_FILE],
                "authorization directory has missing/extra files",
            )
            raw = (args.directory / ENVELOPE_FILE).read_bytes()
            require(
                bool(args.expected_sha256) and plans.sha256(raw) == args.expected_sha256,
                "authorization handoff hash mismatch",
            )
            envelope = plans.strict_json_loads(raw)
            require(raw == plans.canonical_bytes(envelope), "noncanonical envelope")
            require(
                subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
                == envelope["trusted_executor_sha"],
                "checked-out executor differs from authorization",
            )
            require(
                envelope["repository"]["full_name"] == os.environ["GITHUB_REPOSITORY"],
                "envelope repository mismatch",
            )
            if os.environ.get("GITHUB_EVENT_NAME") != "workflow_run":
                require(
                    envelope["source_run"]["id"] == int(os.environ["GITHUB_RUN_ID"])
                    and envelope["source_run"]["run_attempt"] == int(os.environ["GITHUB_RUN_ATTEMPT"]),
                    "handoff belongs to another run/attempt",
                )
            revalidate(api, envelope)
            extract_payloads(envelope, args.output_dir)
        return 0
    except (
        Error,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"Mutation authorization refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
