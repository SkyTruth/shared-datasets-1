"""Run the repository validation contract on an isolated prospective merge.

Default: ci_preflight.py --base SHA --head SHA. The plan/run-suite/verify
subcommands are the same boundaries used by GitHub Actions. None grants release
authorization or uses production credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_geospatial_test_results import check_results
from scripts.ci_runtime import local_runtime, prove_runtime, run_container, suite_platform
from scripts.ci_source_proof import prove_attempts, select_plan
from scripts.ci_contract import (
    NATIVE_TESTS, SUITES, check_junit, contract_digest, expected_tools,
    select_suites, verify_results, write_json,
)


def git(root: Path, *arguments: str, raw: bool = False) -> str:
    output = subprocess.check_output(
        [os.environ.get("CI_GIT", "git"), "-c", f"safe.directory={root}", "-C", str(root), *arguments], text=True,
        stderr=subprocess.PIPE,
    )
    return output if raw else output.strip()


def require_complete_history(root: Path) -> None:
    if git(root, "rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("full checkout history is required; fetch --unshallow before preflight")


def make_plan(root: Path, base: str, head: str, *, original_head: str | None = None) -> dict:
    require_complete_history(root)
    tested_sha = git(root, "rev-parse", "HEAD")
    resolved_head = git(root, "rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}")
    if resolved_head != tested_sha:
        raise ValueError("head must be the checked-out tested revision")
    try:
        resolved_base = git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
        changed = git(root, "diff", "--name-only", "-z", resolved_base, resolved_head, raw=True)
        paths = [path for path in changed.split("\0") if path]
    except subprocess.CalledProcessError:
        # An absent comparison selects all, but individual history-dependent
        # commands still fail when their required base cannot be resolved.
        resolved_base = base
        paths = None
    suites, reason = select_suites(paths)
    return {
        "schema_version": 1, "base": resolved_base,
        "head": original_head or resolved_head, "tested_sha": tested_sha,
        "tree": git(root, "rev-parse", "HEAD^{tree}"),
        "contract_digest": contract_digest(root), "suites": suites,
        "selection_reason": reason, "changed_paths": paths,
    }


def validate_checkout(root: Path, plan: dict) -> None:
    require_complete_history(root)
    if git(root, "rev-parse", "HEAD") != plan["tested_sha"]:
        raise ValueError("checkout is not the planned tested SHA")
    if git(root, "rev-parse", "HEAD^{tree}") != plan["tree"]:
        raise ValueError("checkout tree changed")
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("validation requires a clean checkout")
    if contract_digest(root) != plan["contract_digest"]:
        raise ValueError("validation contract changed")


def suite_commands(suite: str, root: Path, plan: dict, output: Path) -> list[tuple[list[str], Path]]:
    base, head = plan["base"], plan["tested_sha"]
    commands: list[tuple[list[str], Path]] = []

    def add(*args: str, cwd: Path = root) -> None:
        commands.append((list(args), cwd))

    if suite == "lint":
        add("uv", "sync", "--locked", "--all-groups")
        add("uv", "run", "--no-sync", "ruff", "check", ".")
        add("uv", "run", "--no-sync", "python", "scripts/check_workflow_syntax.py")
        add("terraform", "fmt", "-check", "-recursive", "terraform/")
        for environment in ("prod", "preview", "metadata-retirement-iam"):
            add("terraform", f"-chdir=terraform/envs/{environment}", "init", "-backend=false", "-input=false", "-lockfile=readonly")
            add("terraform", f"-chdir=terraform/envs/{environment}", "validate")
        add("terraform", "-chdir=terraform/envs/metadata-retirement-iam", "test")
    elif suite == "tests":
        add("gitleaks", "git", "--redact", "--log-opts=HEAD", ".")
        add("uv", "sync", "--locked", "--all-groups")
        add("uv", "run", "--no-sync", "python", "scripts/admission_check.py", "--base", base, "--head", head)
        diff = ["uv", "run", "--no-sync", "python", "scripts/repo_guardrails.py", "check-diff", "--base", base, "--head", head]
        if os.environ.get("GITHUB_EVENT_PATH"):
            diff.extend(["--event-path", os.environ["GITHUB_EVENT_PATH"]])
        commands.append((diff, root))
        add("uv", "run", "--no-sync", "python", "scripts/repo_guardrails.py", "check-static")
        add("uv", "run", "--no-sync", "pytest", "-o", "xfail_strict=true", f"--junitxml={output / 'pytest.xml'}")
    elif suite in {"sdk-node22", "sdk-node24"}:
        package = root / "api/typescript"
        add("node", "scripts/release-policy.mjs", "changes", base, head, cwd=package)
        add("npm", "ci", cwd=package)
        add("npm", "test", cwd=package)
        add("npm", "run", "test:pack", cwd=package)
    elif suite == "browser":
        add("uv", "sync", "--locked", "--no-dev", "--group", "browser")
        add("npm", "ci", "--ignore-scripts", "--prefix", "tests/browser")
        add("npm", "ci", "--ignore-scripts", "--prefix", "api/typescript")
        add("npm", "run", "build", "--prefix", "api/typescript")
        # The lockfile pins Chromium; both boundaries use the same installer.
        add("npx", "--no-install", "playwright", "install", "--with-deps", "chromium", cwd=root / "tests/browser")
        add("npm", "test", cwd=root / "tests/browser")
    elif suite == "geospatial-integration":
        add("gdalinfo", "--version")
        add("gdal_calc.py", "--help")
        add("tippecanoe", "--version")
        add("pmtiles", "version")
        add("uv", "sync", "--locked", "--all-groups", "--extra", "wdpa-native")
        add("uv", "run", "--no-sync", "pytest", *NATIVE_TESTS, "-o", "xfail_strict=true", f"--junitxml={output / 'pytest.xml'}")
    else:
        raise ValueError(f"unknown suite: {suite}")
    return commands


def probe_tools(suite: str, environment: dict[str, str]) -> dict[str, str]:
    versions = {}
    probes = {
        "python": [sys.executable, "--version"], "uv": ["uv", "--version"],
        "node": ["node", "--version"], "terraform": ["terraform", "version", "-json"],
        "gitleaks": ["gitleaks", "version"], "actionlint": ["actionlint", "-version"],
        "gdal": ["gdalinfo", "--version"],
        "tippecanoe": ["tippecanoe", "--version"], "pmtiles": ["pmtiles", "version"],
    }
    for tool, expected in expected_tools(suite).items():
        output = subprocess.check_output(probes[tool], env=environment, text=True, stderr=subprocess.STDOUT)
        if tool == "terraform":
            actual = json.loads(output)["terraform_version"]
        else:
            match = re.search(r"\d+\.\d+\.\d+", output)
            if match is None:
                raise ValueError(f"{tool}: missing version in probe output")
            actual = match.group()
        if actual != expected:
            raise ValueError(f"{tool}: required {expected}, found {actual}")
        versions[tool] = actual
    return versions


def run_suite(root: Path, plan: dict, suite: str, output: Path) -> dict:
    if suite not in plan["suites"]:
        raise ValueError(f"suite {suite} was not selected")
    output.mkdir(parents=True, exist_ok=True)
    result = {key: plan[key] for key in ("base", "head", "tested_sha", "tree", "contract_digest")}
    result.update({"schema_version": 1, "suite": suite, "status": "failure", "commands": [], "tools": {}})
    if os.environ.get("GITHUB_RUN_ID"):
        result["source"] = {"run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])}
    environment = dict(os.environ)
    # This exact process-owned path is bind-mounted across differing host and
    # container UIDs. The setting also applies to Git invoked by SDK policy.
    environment.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": str(root)})
    environment["SHARED_DATASETS_WORKDIR"] = str(output / "work")
    node_bin = environment.get("CI_NODE24_BIN" if suite == "sdk-node24" else "CI_NODE22_BIN")
    if node_bin:
        environment["PATH"] = node_bin + os.pathsep + environment["PATH"]
    if suite == "geospatial-integration":
        environment["RUN_GDAL_INTEGRATION_TESTS"] = "1"
    else:
        environment.pop("RUN_GDAL_INTEGRATION_TESTS", None)
    try:
        validate_checkout(root, plan)
        result["tools"] = probe_tools(suite, environment)
        executable_names = {"gdal": "gdalinfo", "python": sys.executable}
        result["tool_paths"] = {
            tool: shutil.which(executable_names.get(tool, tool), path=environment["PATH"])
            for tool in result["tools"]
        }
        for index, (command, cwd) in enumerate(suite_commands(suite, root, plan, output)):
            print(f"[{suite}] {' '.join(command)}", flush=True)
            log = output / f"command-{index:02d}.log"
            with log.open("w") as stream:
                completed = subprocess.run(command, cwd=cwd, env=environment, stdout=stream, stderr=subprocess.STDOUT, check=False)
            result["commands"].append({"argv": command, "exit_code": completed.returncode, "log": log.name})
            if completed.returncode != 0:
                print(log.read_text()[-12000:], file=sys.stderr)
                raise ValueError(f"{suite}: command {index + 1} failed ({completed.returncode})")
        if suite in {"tests", "geospatial-integration"}:
            check_junit(output / "pytest.xml", native=suite == "geospatial-integration")
            if suite == "geospatial-integration":
                check_results(output / "pytest.xml")
        if suite in {"sdk-node22", "sdk-node24"}:
            candidates = list((output / "work/_scratch").glob("sdk-package-smoke-*/candidate.json"))
            if len(candidates) != 1:
                raise ValueError("expected exactly one tested SDK candidate")
            candidate = json.loads(candidates[0].read_text())
            tarball = Path(candidate["tarball"])
            package_output = output / "package"
            package_output.mkdir()
            shutil.copyfile(tarball, package_output / tarball.name)
            candidate["tarball"] = tarball.name
            candidate["sha256"] = hashlib.sha256(tarball.read_bytes()).hexdigest()
            candidate["tested_sha"] = plan["tested_sha"]
            write_json(package_output / "candidate.json", candidate)
            result["package"] = candidate
        validate_checkout(root, plan)
        result["status"] = "success"
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
        print(f"{suite}: {exc}", file=sys.stderr)
    finally:
        write_json(output / "result.json", result)
    return result


def isolated_merge(source: Path, base: str, head: str, destination: Path) -> tuple[Path, str, str]:
    require_complete_history(source)
    if git(source, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("source checkout must be clean; commit changes before preflight")
    base_sha = git(source, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    head_sha = git(source, "rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}")
    executable = os.environ.get("CI_GIT", "git")
    subprocess.run([executable, "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(source), str(destination)], check=True)
    subprocess.run([executable, "-C", str(destination), "checkout", "--detach", base_sha], check=True)
    environment = {**os.environ, "GIT_AUTHOR_NAME": "CI preflight", "GIT_AUTHOR_EMAIL": "ci-preflight@invalid.example", "GIT_COMMITTER_NAME": "CI preflight", "GIT_COMMITTER_EMAIL": "ci-preflight@invalid.example"}
    # Hooks and signing are user-owned configuration and do not run in this
    # process-owned checkout. Merge conflicts fail before any validation.
    subprocess.run([executable, "-C", str(destination), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "merge", "--no-ff", "--no-edit", head_sha], env=environment, check=True)
    return destination, base_sha, head_sha


def preflight(args: argparse.Namespace) -> int:
    source = Path(args.repo).resolve()
    work_root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1")) / "_scratch"
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ci-preflight-", dir=work_root))
    print(f"Preflight evidence: {work}", flush=True)
    root, base, original_head = isolated_merge(source, args.base, args.head, work / "repo")
    plan = make_plan(root, base, "HEAD", original_head=original_head)
    write_json(work / "plan.json", plan)
    if shutil.which("docker") is None:
        raise ValueError("Docker is required for the pinned Linux preflight toolchain")
    runtime_arguments, runtime = local_runtime(work)
    write_json(work / "runtime.json", runtime)
    image = f"shared-datasets-preflight:{plan['contract_digest'][:16]}"
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "-f", ".github/docker/preflight.Dockerfile", "-t", image, "."], cwd=root, check=True)
    prove_runtime(runtime_arguments, runtime, image)
    write_json(work / "runtime.json", runtime)
    arm_image = f"shared-datasets-preflight-arm:{plan['contract_digest'][:16]}"
    if any(suite_platform(suite, runtime) == "linux/arm64" for suite in plan["suites"]):
        subprocess.run(["docker", "build", "--platform", "linux/arm64", "-f", ".github/docker/preflight.Dockerfile", "-t", arm_image, "."], cwd=root, check=True)
    native_image = f"shared-datasets-native:{plan['contract_digest'][:16]}"
    if "geospatial-integration" in plan["suites"]:
        subprocess.run(["docker", "build", "--platform", "linux/amd64", "-f", ".github/docker/geospatial-ci.Dockerfile", "-t", native_image, "."], cwd=root, check=True)
    results = []
    runtime["suites"] = {}
    for suite in plan["suites"]:
        output = work / suite
        output.mkdir()
        platform = suite_platform(suite, runtime)
        selected_image = native_image if suite == "geospatial-integration" else arm_image if platform == "linux/arm64" else image
        runtime["suites"][suite] = run_container(root, work, suite, selected_image, platform, runtime_arguments)
        write_json(work / "runtime.json", runtime)
        report = output / "result.json"
        if not report.exists():
            raise ValueError(f"{suite} did not produce evidence")
        result = json.loads(report.read_text())
        results.append(result)
        if result.get("status") != "success":
            raise ValueError(f"{suite} failed; retained evidence: {report}")
    verify_results(plan, results)
    # Recheck the source identities after potentially lengthy validation.
    if git(source, "rev-parse", f"{args.base}^{{commit}}") != base or git(source, "rev-parse", f"{args.head}^{{commit}}") != original_head:
        raise ValueError("base or head changed during preflight; evidence is stale")
    if git(source, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("source changed during preflight; evidence is stale")
    write_json(work / "preflight.json", {**plan, "status": "success", "results": results, "runtime": runtime})
    print(f"All {len(results)} selected suites passed. Evidence: {work / 'preflight.json'}")
    return 0


def load_plan(path: Path) -> dict:
    candidates = [json.loads(candidate.read_text()) for candidate in sorted(path.rglob("plan.json"))] if path.is_dir() else [json.loads(path.read_text())]
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        return select_plan(candidates, run_id=run_id, attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]))
    if len(candidates) != 1:
        raise ValueError("local validation requires exactly one plan")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--repo", default=".")
    commands = parser.add_subparsers(dest="command")
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--base", required=True)
    plan_parser.add_argument("--head", default="HEAD")
    plan_parser.add_argument("--source-head")
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--github-output", type=Path)
    runner = commands.add_parser("run-suite")
    runner.add_argument("--plan", type=Path, required=True)
    runner.add_argument("--suite", choices=SUITES, required=True)
    runner.add_argument("--output", type=Path, required=True)
    verifier = commands.add_parser("verify")
    verifier.add_argument("--plan", type=Path, required=True)
    verifier.add_argument("--results", type=Path, required=True)
    verifier.add_argument("--jobs", type=Path)
    verifier.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path.cwd()
    try:
        if args.command == "plan":
            plan = make_plan(root, args.base, args.head, original_head=args.source_head)
            if os.environ.get("GITHUB_RUN_ID"):
                plan["source"] = {"run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])}
            write_json(args.output, plan)
            if args.github_output:
                with args.github_output.open("a") as output:
                    for suite in SUITES:
                        output.write(f"{suite}={'true' if suite in plan['suites'] else 'false'}\n")
                    output.write(f"should_run={'true' if 'geospatial-integration' in plan['suites'] else 'false'}\n")
                    output.write(f"tested_sha={plan['tested_sha']}\n")
            return 0
        if args.command == "run-suite":
            plan = load_plan(args.plan)
            return 0 if run_suite(root, plan, args.suite, args.output)["status"] == "success" else 1
        if args.command == "verify":
            run_id = os.environ.get("GITHUB_RUN_ID")
            current_attempt = int(os.environ["GITHUB_RUN_ATTEMPT"]) if run_id else None
            plan = load_plan(args.plan)
            validate_checkout(root, plan)
            if make_plan(root, plan["base"], "HEAD", original_head=plan["head"]) != {key: value for key, value in plan.items() if key != "source"}:
                raise ValueError("selection plan does not match the current comparison")
            results = [json.loads(report.read_text()) for report in sorted(args.results.rglob("result.json"))]
            jobs = json.loads(args.jobs.read_text()) if args.jobs else None
            proofs = None
            if run_id:
                attempts = {plan["source"]["run_attempt"], *(result.get("source", {}).get("run_attempt") for result in results)}
                if any(type(attempt) is not int or not 1 <= attempt <= current_attempt for attempt in attempts):
                    raise ValueError("invalid result source attempt")
                proofs = prove_attempts(os.environ["GITHUB_REPOSITORY"], run_id, attempts, plan)
                if "geospatial-changes" not in proofs[plan["source"]["run_attempt"]]:
                    raise ValueError("selected plan has no successful source selection job")
            chosen = verify_results(plan, results, jobs, source_proofs=proofs, run_id=run_id, run_attempt=current_attempt)
            if args.output:
                evidence = {**plan, "status": "success"}
                if run_id:
                    evidence.update({
                        "source": {"run_id": run_id, "run_attempt": current_attempt},
                        "plan_artifact": f"ci-validation-plan-attempt{plan['source']['run_attempt']}",
                        "suite_artifacts": {suite: f"ci-result-{suite}-attempt{result['source']['run_attempt']}" for suite, result in chosen.items()},
                    })
                write_json(args.output, evidence)
            print("Every selected suite passed for the exact tested revision and contract.")
            return 0
        if not args.base or not args.head:
            parser.error("--base and --head are required for isolated preflight")
        return preflight(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
