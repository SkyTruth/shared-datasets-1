"""Run the repository validation contract on an isolated prospective merge.

Default: ci_preflight.py --base SHA --head SHA. The plan/run-suite/verify
subcommands are the same boundaries used by GitHub Actions. None grants release
authorization or uses production credentials.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Event
from time import monotonic
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_source_proof import image_group, prove_attempts, select_plan
from scripts.check_geospatial_test_results import check_junit
from scripts.deployment_revision import DeploymentError
from scripts.terraform_target_contracts import production_image_limits
from scripts.tested_image_bundle import BUILD_TARGETS, ImageError, validate_manifest, validate_local_manifest
from scripts.ci_contract import (
    ALWAYS, DEPLOYMENTS, NATIVE_TESTS, SUITES, TOOLCHAIN, contract_digest, expected_tools, delivery_outputs, deploy_image_targets,
    INGESTION_ROLLOUT_HOLDS, rollout_holds, documentation_only, resolve_selection, planned_images, verify_results, write_json,
)

LOCAL_COORDINATION_DIRECTORY = Path("/tmp/shared-datasets-1/coordination")
CONTAINER_LABEL = "shared-datasets-preflight"

# Host Python and Docker retain normal filesystem/API capabilities; this
# isolates default configuration and inherited credentials, not malicious code.
NETWORK_ENV = {
    "PATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "DOCKER_API_VERSION",
}
TLS_FILES = frozenset({"ca.pem", "cert.pem", "key.pem"})


def endpoint_kind(host: str) -> str:
    address = urlsplit(host)
    if address.username or address.password or address.query or address.fragment:
        raise ValueError("Docker endpoint must not embed authentication or query data")
    if address.scheme == "unix" and not address.netloc and address.path.startswith("/"):
        return "unix"
    if address.scheme == "tcp" and address.hostname and address.path in {"", "/"}:
        _ = address.port  # Reject malformed port declarations before running Docker.
        return "tcp"
    raise ValueError("Host image validation supports explicit Unix or TCP Docker transport; SSH/agent transport is unsupported")


def transport(caller: dict[str, str]) -> tuple[str, bool, bool, Path | None, set[str]]:
    # DOCKER_CONTEXT overrides DOCKER_HOST in the official CLI. Resolve it first
    # rather than redirecting an existing connection after removing user config.
    if caller.get("DOCKER_HOST") and not caller.get("DOCKER_CONTEXT"):
        host = caller["DOCKER_HOST"]
        tls = bool(caller.get("DOCKER_TLS") or caller.get("DOCKER_TLS_VERIFY"))
        verify = bool(caller.get("DOCKER_TLS_VERIFY"))
        source = None
        files = set()
        if tls:
            certificate_path = caller.get("DOCKER_CERT_PATH") or caller.get("DOCKER_CONFIG")
            if not certificate_path:
                if not caller.get("HOME"):
                    raise ValueError("Docker TLS requires an explicit certificate configuration")
                certificate_path = str(Path(caller["HOME"]) / ".docker")
            source = Path(certificate_path)
            files = {name for name in TLS_FILES if (source / name).is_file()}
        return host, tls, verify, source, files
    command = ["docker", "context", "inspect"]
    if caller.get("DOCKER_CONTEXT"):
        if caller["DOCKER_CONTEXT"].startswith("-"):
            raise ValueError("invalid Docker context name")
        command.append(caller["DOCKER_CONTEXT"])
    try:
        contexts = json.loads(subprocess.check_output(command, env=caller, text=True, stderr=subprocess.PIPE))
        if not isinstance(contexts, list) or len(contexts) != 1:
            raise ValueError("Docker context inspection must resolve exactly one context")
        context = contexts[0]
        endpoint = context["Endpoints"]["docker"]
        host = endpoint["Host"]
        if not isinstance(host, str) or type(endpoint["SkipTLSVerify"]) is not bool:
            raise ValueError("Docker context endpoint has invalid transport metadata")
        verify = not endpoint["SkipTLSVerify"]
        files = context.get("TLSMaterial", {}).get("docker", [])
        if not isinstance(files, list) or len(set(files)) != len(files) or not set(files) <= TLS_FILES:
            raise ValueError("Docker context contains unsupported TLS material")
        tls = bool(files or endpoint["SkipTLSVerify"])
        source = None
        if files:
            directory = Path(context["Storage"]["TLSPath"])
            if not directory.is_absolute():
                raise ValueError("Docker context TLS storage must be an explicit directory")
            # Official Docker CLI stores TLS files by context, then endpoint.
            source = directory / "docker"
        return host, tls, verify, source, set(files)
    except (KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        raise ValueError("Cannot resolve the Docker context; configure a supported explicit endpoint") from exc


@contextmanager
def isolated_host_environment(work: Path, caller: dict[str, str]):
    host, tls, verify, source, files = transport(caller)
    kind = endpoint_kind(host)
    if tls and kind != "tcp":
        raise ValueError("Docker TLS requires a TCP endpoint")
    if ("cert.pem" in files) != ("key.pem" in files):
        raise ValueError("Docker TLS client certificate and key must be supplied together")
    if not caller.get("PATH"):
        raise ValueError("Host image validation requires an explicit tool PATH")
    with tempfile.TemporaryDirectory(prefix="host-context-", dir=work) as temporary:
        root = Path(temporary)
        environment = {key: value for key, value in caller.items() if key in NETWORK_ENV}
        for key, directory in {
            "HOME": "home", "DOCKER_CONFIG": "docker", "XDG_CONFIG_HOME": "xdg", "CLOUDSDK_CONFIG": "gcloud",
        }.items():
            target = root / directory
            target.mkdir(mode=0o700)
            environment[key] = str(target)
        (root / "docker/config.json").write_text("{}\n")
        environment["DOCKER_HOST"] = host
        if tls:
            certificates = root / "docker-tls"
            certificates.mkdir(mode=0o700)
            for name in sorted(files):
                path = source / name
                if not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
                    raise ValueError(f"Missing or invalid Docker transport TLS file: {name}")
                target = certificates / name
                target.write_bytes(path.read_bytes())
                target.chmod(0o600)
            environment["DOCKER_CERT_PATH"] = str(certificates)
            environment["DOCKER_TLS_VERIFY" if verify else "DOCKER_TLS"] = "1"
        yield environment


def local_runtime() -> dict:
    server = json.loads(subprocess.check_output(["docker", "version", "--format", "{{json .Server}}"], text=True))
    architecture = server["Arch"]
    if server["Os"] != "linux" or architecture not in {"amd64", "arm64"}:
        raise ValueError("preflight requires a Linux Docker server on amd64 or arm64")
    cpus = int(subprocess.check_output(["docker", "info", "--format", "{{.NCPU}}"], text=True).strip())
    return {"platform": f"linux/{architecture}", "server_architecture": architecture,
            "docker_version": server["Version"], "cpus": cpus, "suite_workers": 3 if cpus >= 8 else 2}


def run_container(root: Path, work: Path, suite: str, image: str, platform: str, *, cancelled=None) -> dict:
    """Copy a clean checkout into an owned container and retain its evidence.

    No host directories, Docker socket, environment credentials or user config
    are mounted. Each architecture gets its own dependency environment.
    """
    image_platform = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image], text=True).strip()
    if image_platform != platform:
        raise ValueError(f"{suite}: image platform {image_platform} differs from native runtime {platform}")
    command = ["docker", "create", "--init", "--label", f"{CONTAINER_LABEL}={work.name}", "--platform", platform, "-w", "/workspace",
               "-e", "UV_PROJECT_ENVIRONMENT=/evidence/venv", "-e", "UV_CACHE_DIR=/opt/uv-cache",
               "-e", "UV_LINK_MODE=copy"]
    environment = {"CI": "true"}
    if platform == "linux/arm64":
        # Older Apple Linux VMs report CPU extensions that bundled OpenSSL
        # cannot execute. Select its portable implementation, preserving the
        # locked cryptography wheel and the complete test corpus.
        environment["OPENSSL_armcap"] = "0"
    if suite == "browser":
        # Exercise GitHub's CI-only reporter behavior with owned public
        # metadata. Never forward tokens, run IDs or the caller's real event.
        plan = json.loads((work / "plan.json").read_text())
        (work / "browser-event.json").write_text(json.dumps({"pull_request": {
            "title": "Local preflight prospective merge", "number": 0,
            "base": {"sha": plan["base"]}, "head": {"sha": plan["head"]},
        }}) + "\n")
        environment.update({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_PATH": "/browser-event.json",
                            "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "local/preflight",
                            "GITHUB_SHA": plan["tested_sha"]})
    for key, value in environment.items():
        command.extend(["-e", f"{key}={value}"])
    command.extend([image, "/usr/local/bin/python", "scripts/ci_preflight.py", "run-suite",
                    "--plan", "/plan.json", "--suite", suite, "--output", f"/evidence/{suite}"])
    identifier = subprocess.check_output(command, text=True).strip()
    record = {"platform": platform, "image": image, "container": identifier, "environment": environment}
    try:
        if cancelled is not None and cancelled.is_set():
            raise ValueError(f"{suite}: preflight cancelled")
        subprocess.run(["docker", "cp", f"{root}/.", f"{identifier}:/workspace"], check=True)
        subprocess.run(["docker", "cp", str(work / "plan.json"), f"{identifier}:/plan.json"], check=True)
        if suite == "browser":
            subprocess.run(["docker", "cp", str(work / "browser-event.json"), f"{identifier}:/browser-event.json"], check=True)
        subprocess.run(["docker", "start", "-a", identifier], check=False)
        state = json.loads(subprocess.check_output(["docker", "inspect", "--format", "{{json .State}}", identifier], text=True))
        if state["Running"] or state["Status"] != "exited":
            raise ValueError(f"{suite}: container did not reach a terminal state")
        record["exit_code"] = state["ExitCode"]
        record["image_id"] = subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}", identifier], text=True).strip()
        subprocess.run(["docker", "cp", f"{identifier}:/evidence/{suite}/.", str(work / suite)], check=True)
    finally:
        subprocess.run(["docker", "rm", "-f", identifier], check=True)
    if record["exit_code"] != 0:
        raise ValueError(f"{suite}: container failed ({record['exit_code']}); evidence: {work / suite}")
    return record


def git(root: Path, *arguments: str, raw: bool = False) -> str:
    output = subprocess.check_output(
        [os.environ.get("CI_GIT", "git"), "-c", f"safe.directory={root}", "-C", str(root), *arguments], text=True,
        stderr=subprocess.PIPE,
    )
    return output if raw else output.strip()


def require_complete_history(root: Path) -> None:
    if git(root, "rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("full checkout history is required; fetch --unshallow before preflight")


def ingestion_rollout_holds(root: Path, revision: str, *, historical=False) -> tuple[str, ...]:
    if not git(root, "ls-tree", "--name-only", revision, "--", INGESTION_ROLLOUT_HOLDS):
        return ()
    return rollout_holds(json.loads(git(root, "show", f"{revision}:{INGESTION_ROLLOUT_HOLDS}", raw=True)), historical=historical)


def make_plan(root: Path, base: str, head: str, *, original_head: str | None = None, comparison_head: str | None = None) -> dict:
    require_complete_history(root)
    tested_sha = git(root, "rev-parse", "HEAD")
    resolved_head = git(root, "rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}")
    if resolved_head != tested_sha:
        raise ValueError("head must be the checked-out tested revision")
    try:
        resolved_base = git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
        changed = git(root, "diff", "--no-renames", "--name-only", "-z", resolved_base, comparison_head or resolved_head, raw=True)
        paths = [path for path in changed.split("\0") if path]
    except subprocess.CalledProcessError:
        resolved_base = base
        paths = None
    holds = ingestion_rollout_holds(root, tested_sha)
    released = ()
    if paths and INGESTION_ROLLOUT_HOLDS in paths:
        released = tuple(set(ingestion_rollout_holds(root, resolved_base, historical=True)) - set(holds))
    catalog_snapshots = None
    if paths and "catalog/shared-datasets-catalog.csv" in paths:
        try:
            catalog_snapshots = tuple(git(root, "show", f"{revision}:catalog/shared-datasets-catalog.csv", raw=True) for revision in (resolved_base, tested_sha))
        except subprocess.CalledProcessError:
            # A new or unavailable snapshot conservatively selects its known consumers.
            pass
    return {
        "schema_version": 1, "base": resolved_base,
        "head": original_head or resolved_head, "tested_sha": tested_sha,
        "tree": git(root, "rev-parse", "HEAD^{tree}"),
        "contract_digest": contract_digest(root), "changed_paths": paths,
        "production_image_limits": production_image_limits(root),
        **resolve_selection(paths, base=resolved_base if paths is not None else None, root=root,
                            catalog_snapshots=catalog_snapshots, ingestion_holds=holds, released_holds=released),
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


def interpreter_command() -> list[str]:
    program = (
        "import platform\n"
        "actual = platform.python_version()\n"
        "print('Validation interpreter: ' + actual, flush=True)\n"
        f"if actual != {TOOLCHAIN['python']!r}:\n"
        f"    raise RuntimeError('Required validation interpreter {TOOLCHAIN['python']}; found ' + actual)\n"
    )
    return ["uv", "run", "--no-sync", "python", "-c", program]


def suite_commands(suite: str, root: Path, plan: dict, output: Path) -> list[tuple[list[str], Path]]:
    base, head = plan["base"], plan["tested_sha"]
    commands: list[tuple[list[str], Path]] = []

    def add(*args: str, cwd: Path = root) -> None:
        commands.append((list(args), cwd))
        if args[:2] == ("uv", "sync"):
            commands.append((interpreter_command(), cwd))

    if suite == "lint":
        if (root / INGESTION_ROLLOUT_HOLDS).exists():
            previous = None
            if INGESTION_ROLLOUT_HOLDS in (plan.get("changed_paths") or []):
                previous = json.loads(git(root, "show", f"{base}:{INGESTION_ROLLOUT_HOLDS}")) if git(root, "ls-tree", "--name-only", base, "--", INGESTION_ROLLOUT_HOLDS) else {}
            rollout_holds(json.loads((root / INGESTION_ROLLOUT_HOLDS).read_text()), previous=previous)
        if documentation_only(plan.get("changed_paths"), root=root):
            add("git", "diff", "--check", base, head)
            return commands
        add("uv", "sync", "--locked", "--all-groups")
        add("uv", "run", "--no-sync", "python", "scripts/deployment_permissions.py", "--static-base", base, "--static-head", head)
        add("uv", "run", "--no-sync", "python", "scripts/reviewed_dataset_plan.py", "check-diff", "--base", base, "--head", head)
        add("uv", "run", "--no-sync", "ruff", "check", ".")
        add("uv", "run", "--no-sync", "python", "scripts/check_workflow_syntax.py")
        add("terraform", "fmt", "-check", "-recursive", "terraform/")
        for environment in ("prod", "preview"):
            add("terraform", f"-chdir=terraform/envs/{environment}", "init", "-backend=false", "-input=false", "-lockfile=readonly")
            add("terraform", f"-chdir=terraform/envs/{environment}", "validate")
        add("uv", "run", "--no-sync", "python", "scripts/terraform_target_contracts.py", "--output", str(output / 'terraform-targets'))
        add("uv", "run", "--no-sync", "python", "scripts/release_contracts.py", "--target", "all")
    elif suite == "tests-control-plane":
        add("uv", "sync", "--locked", "--all-groups")
    elif suite == "tests":
        add("gitleaks", "git", "--redact", "--log-opts=HEAD", ".")
        add("uv", "sync", "--locked", "--all-groups")
        add("uv", "run", "--no-sync", "python", "scripts/catalog_docs.py", "check")
        add("uv", "run", "--no-sync", "python", "scripts/catalog_site.py", "--out", str(output / "catalog-web"))
        if documentation_only(plan.get("changed_paths"), root=root):
            return commands
        add("uv", "run", "--no-sync", "python", "scripts/admission_check.py", "--base", base, "--head", head)
        add("uv", "run", "--no-sync", "python", "scripts/cdn_plan_readiness.py", "check-diff", "--base", base, "--head", head)
        diff = ["uv", "run", "--no-sync", "python", "scripts/repo_guardrails.py", "check-diff", "--base", base, "--head", head]
        if os.environ.get("GITHUB_EVENT_PATH"):
            diff.extend(["--event-path", os.environ["GITHUB_EVENT_PATH"]])
        commands.append((diff, root))
        add("uv", "run", "--no-sync", "python", "scripts/repo_guardrails.py", "check-static")
        add("uv", "run", "--no-sync", "python", ".claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py", "--local-only", "--format", "markdown", "--fail-on-findings")
        add("uv", "run", "--no-sync", "python", "scripts/check_identity_resolutions.py", "--offline")
    elif suite in {"sdk-node22", "sdk-node24"}:
        package = root / "api/typescript"
        add("node", "scripts/release-policy.mjs", "changes", base, head, cwd=package)
        add("npm", "ci", cwd=package)
        add("npm", "test", cwd=package)
        add("npm", "run", "test:pack", cwd=package)
    elif suite == "browser":
        add("uv", "sync", "--locked", "--no-dev", "--group", "browser")
        # Installed dependencies use copy links; discard only this runtime's
        # download cache before Chromium's larger installation allocates space.
        add("uv", "cache", "clean")
        add("npm", "ci", "--ignore-scripts", "--prefix", "tests/browser")
        add("npm", "ci", "--ignore-scripts", "--prefix", "api/typescript")
        add("npm", "run", "build", "--prefix", "api/typescript")
        # Managed runners/images supply system libraries; the lockfile pins Chromium.
        add("npx", "--no-install", "playwright", "install", "chromium", cwd=root / "tests/browser")
        add("npm", "test", cwd=root / "tests/browser")
    elif suite == "geospatial-integration":
        add("gdalinfo", "--version")
        add("gdal_calc.py", "--help")
        add("tippecanoe", "--version")
        add("pmtiles", "version")
        add("uv", "sync", "--locked", "--all-groups", "--extra", "wdpa-native")
        add("uv", "run", "--no-sync", "pytest", *NATIVE_TESTS, "-o", "xfail_strict=true", f"--junitxml={output / 'pytest.xml'}")
    elif suite == "production-images":
        targets = planned_images(plan)
        if "image_target" in plan:
            if plan["image_target"] not in targets:
                raise ValueError("image target was not selected by the validation plan")
            targets = [plan["image_target"]]
        if not targets:
            raise ValueError("production image suite selected without image targets")
        for target in targets:
            add(sys.executable, "-I", "scripts/production_image_contracts.py",
                "--target", target, "--limits", json.dumps(plan["production_image_limits"][target]), "--output", str(output / target),
                *(["--local-images"] if plan.get("local_images") else []))
    else:
        raise ValueError(f"unknown suite: {suite}")
    if suite in {"tests", "tests-control-plane"} and not documentation_only(plan.get("changed_paths"), root=root):
        files = [str(path.relative_to(root)) for path in sorted(set((root / "tests").rglob("test_*.py")) | set((root / "tests").rglob("*_test.py")))
                 if ("ci_" in path.name or "deployment" in path.name) == (suite == "tests-control-plane")]
        add("terraform", "-chdir=terraform/envs/prod", "init", "-backend=false", "-input=false", "-lockfile=readonly")
        add("uv", "run", "--no-sync", "pytest", *files, "-n", "4", "--max-worker-restart", "0", "-o", "xfail_strict=true", f"--junitxml={output / 'pytest.xml'}")
    return commands


def probe_tools(suite: str, environment: dict[str, str]) -> dict[str, str]:
    versions = {}
    probes = {
        "python": [sys.executable, "--version"], "uv": ["uv", "--version"],
        "node": ["node", "--version"], "terraform": ["terraform", "version", "-json"],
        "gitleaks": ["gitleaks", "version"], "actionlint": ["actionlint", "-version"],
        "shellcheck": ["shellcheck", "--version"],
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


def run_suite(root: Path, plan: dict, suite: str, output: Path, *, image_target=None, cancelled=None, local_images=False) -> dict:
    if suite not in plan["suites"]:
        raise ValueError(f"suite {suite} was not selected")
    output.mkdir(parents=True, exist_ok=True)
    result = {key: plan[key] for key in ("base", "head", "tested_sha", "tree", "contract_digest")}
    result.update({"schema_version": 1, "suite": suite, "status": "failure", "commands": [], "tools": {}})
    if image_target is not None:
        if suite != "production-images":
            raise ValueError("image target requires the production-images suite")
        plan = {**plan, "image_target": image_target}
        result["image_target"] = image_target
    if os.environ.get("GITHUB_RUN_ID"):
        if local_images:
            raise ValueError("local image receipts cannot be used in Actions")
        result["source"] = {"run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])}
    if local_images:
        plan = {**plan, "local_images": True}
    environment = {**os.environ, "CI": "true", "UV_PYTHON": TOOLCHAIN["python"]}
    environment.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": str(root)})
    environment["SHARED_DATASETS_WORKDIR"] = str(output / "work")
    node_bin = environment.get("CI_NODE24_BIN" if suite == "sdk-node24" else "CI_NODE22_BIN")
    if node_bin:
        environment["PATH"] = node_bin + os.pathsep + environment["PATH"]
    if suite == "geospatial-integration":
        environment["RUN_GDAL_INTEGRATION_TESTS"] = "1"
    else:
        environment.pop("RUN_GDAL_INTEGRATION_TESTS", None)
    if suite == "browser":
        environment["UV_CACHE_DIR"] = str(output / "uv-cache")
    try:
        validate_checkout(root, plan)
        result["tools"] = probe_tools(suite, environment)
        executable_names = {"gdal": "gdalinfo", "python": sys.executable}
        result["tool_paths"] = {
            tool: shutil.which(executable_names.get(tool, tool), path=environment["PATH"])
            for tool in result["tools"]
        }
        if suite == "production-images":
            result["tool_paths"]["docker"] = shutil.which("docker", path=environment["PATH"])
            result["runtime"] = json.loads(subprocess.check_output(["docker", "version", "--format", "{{json .}}"], env=environment, text=True))
        owns_cancellation = cancelled is None
        cancelled = cancelled if cancelled is not None else Event()
        def execute(item):
            if cancelled.is_set():
                return None
            index, (command, cwd) = item
            print(f"[{suite}] {' '.join(command)}", flush=True)
            log = output / f"command-{index:02d}.log"
            with log.open("w") as stream:
                if suite != "production-images":
                    code = subprocess.run(command, cwd=cwd, env=environment, stdout=stream, stderr=subprocess.STDOUT, check=False).returncode
                else:
                    with subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream,
                                          stderr=subprocess.STDOUT, start_new_session=True) as process:
                        while process.poll() is None:
                            if cancelled.wait(0.1):
                                os.killpg(process.pid, signal.SIGTERM)
                                try:
                                    process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    os.killpg(process.pid, signal.SIGKILL)
                                break
                        code = process.wait()
                    if code:
                        cancelled.set()
            return {"argv": command, "exit_code": code, "log": log.name}

        # Only independent image targets run concurrently. Policy/setup commands
        # retain their serial, fail-fast ordering. Both paths keep one suite proof.
        with ThreadPoolExecutor(max_workers=2) if suite == "production-images" else nullcontext() as executor:
            commands = enumerate(suite_commands(suite, root, plan, output))
            reports = (future.result() for future in as_completed([executor.submit(execute, item) for item in commands])) if executor else map(execute, commands)
            try:
                for report in reports:
                    if report is None:
                        continue
                    result["commands"].append(report)
                    if report["exit_code"] != 0:
                        print((output / report["log"]).read_text()[-12000:], file=sys.stderr)
                        raise ValueError(f"{suite}: command {len(result['commands'])} failed ({report['exit_code']})")
            finally:
                if owns_cancellation:
                    cancelled.set()
        if suite == "geospatial-integration" or suite in {"tests", "tests-control-plane"} and not documentation_only(plan.get("changed_paths"), root=root):
            check_junit(output / "pytest.xml", native=suite == "geospatial-integration")
        if suite == "production-images":
            manifest = {"schema_version": 1, "tested_sha": plan["tested_sha"], "images": {}}
            if local_images:
                manifest["transport"] = "local-daemon"
            (output / "images").mkdir(exist_ok=True)
            for target in [image_target] if image_target else planned_images(plan):
                directory = output / target / "images"
                validator = validate_local_manifest if local_images else validate_manifest
                images = validator(json.loads((directory / "manifest.json").read_text()), plan["tested_sha"])
                if set(images) != {target}:
                    raise ValueError("tested production-image set differs from validation selection")
                manifest["images"].update(images)
                if not local_images:
                    (directory / images[target]["archive"]).replace(output / "images" / images[target]["archive"])
            write_json(output / "images/manifest.json", manifest)
            result["production_images"] = manifest
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
    except (ImageError, ValueError, OSError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
        print(f"{suite}: {exc}", file=sys.stderr)
    finally:
        write_json(output / "result.json", result)
    return result


def run_image_group(root: Path, plan: dict, group: str, output: Path) -> bool:
    """Share a runner while retaining independent target/attempt receipts."""
    targets = [target for target in planned_images(plan) if image_group(target) == group]
    if not targets:
        raise ValueError("image group was not selected by the validation plan")
    failed = Event()

    def execute(target):
        if failed.is_set():
            return False
        result = run_suite(root, plan, "production-images", output / target, image_target=target, cancelled=failed)
        if result["status"] != "success":
            failed.set()
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(execute, targets))
    return all(outcomes)


def isolated_merge(source: Path, base: str, head: str, destination: Path) -> tuple[Path, str, str]:
    require_complete_history(source)
    if git(source, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("source checkout must be clean; commit changes before preflight")
    base_sha = git(source, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    head_sha = git(source, "rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}")
    executable = os.environ.get("CI_GIT", "git")
    subprocess.run([executable, "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(source), str(destination)], check=True)
    # A base can be reachable only through the source's origin/* refs, which
    # clone does not copy. Fetch the exact commits into this owned clone.
    subprocess.run([executable, "-C", str(destination), "fetch", "--no-tags", str(source), base_sha, head_sha], check=True)
    subprocess.run([executable, "-C", str(destination), "checkout", "--detach", base_sha], check=True)
    timestamp = max(int(git(source, "show", "-s", "--format=%ct", revision)) for revision in (base_sha, head_sha))
    environment = {**os.environ, "GIT_AUTHOR_NAME": "CI preflight", "GIT_AUTHOR_EMAIL": "ci-preflight@invalid.example", "GIT_COMMITTER_NAME": "CI preflight", "GIT_COMMITTER_EMAIL": "ci-preflight@invalid.example",
                   "GIT_AUTHOR_DATE": f"{timestamp} +0000", "GIT_COMMITTER_DATE": f"{timestamp} +0000"}
    # Hooks and signing are user-owned configuration and do not run in this
    # process-owned checkout. Merge conflicts fail before any validation.
    subprocess.run([executable, "-C", str(destination), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "merge", "--no-ff", "--no-edit", head_sha], env=environment, check=True)
    return destination, base_sha, head_sha


@contextmanager
def local_preflight_slot(source: Path, base: str, head: str, *, wait=False):
    """One heavy preflight per local user/host, across worktrees and temp roots.

    The kernel owns the lock; retained owner text is diagnostic, never a lease.
    Do not unlink the file: replacing its inode would permit concurrent holders.
    """
    directory = LOCAL_COORDINATION_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"preflight-{os.getuid()}.lock"
    with path.open("a+") as stream:
        started = monotonic()
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.seek(0)
            owner = stream.read().strip()
            if not wait:
                raise ValueError(f"another local preflight owns {path}: {owner}; use --wait to queue") from None
            print(f"Waiting for preflight slot {path}: {owner}", flush=True)
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            waited = round(monotonic() - started, 3)
            print(f"Preflight slot wait: {waited:.3f}s", flush=True)
            stream.seek(0)
            stream.truncate()
            stream.write(json.dumps({"pid": os.getpid(), "repo": str(source.resolve()), "base": base, "head": head}) + "\n")
            stream.flush()
            yield waited
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def remove_containers(run_id: str | None = None) -> None:
    label = CONTAINER_LABEL + ("=" + run_id if run_id else "")
    identifiers = subprocess.check_output(["docker", "ps", "-aq", "--filter", "label=" + label], text=True).split()
    if identifiers:
        subprocess.run(["docker", "rm", "-f", *identifiers], check=True, stdout=subprocess.DEVNULL)


def cleanup_old_images() -> None:
    tags = subprocess.check_output(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"], text=True).splitlines()
    tags = sorted({tag for tag in tags if tag.split(":")[0] in {
        "shared-datasets-preflight", "shared-datasets-preflight-arm", "shared-datasets-native", "shared-datasets-native-arm",
    } or tag.startswith("shared-datasets-preflight/") if not tag.endswith(":<none>")})
    if not tags:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    images = json.loads(subprocess.check_output(["docker", "image", "inspect", *tags], text=True))
    expired = sorted({tag for image in images if datetime.fromisoformat(image["Created"].replace("Z", "+00:00")) < cutoff
                      for tag in image["RepoTags"] or [] if tag in tags})
    if expired:
        subprocess.run(["docker", "image", "rm", *expired], check=True, stdout=subprocess.DEVNULL)


def run_host_suite(command, *, cwd, env, cancelled):
    """Interrupt the image driver, allowing its producer cleanup to finish."""
    with subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True) as process:
        while process.poll() is None:
            if cancelled.wait(0.1):
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                break
        return subprocess.CompletedProcess(command, process.wait())


def local_evidence_path(base: str, head: str) -> Path:
    if any(re.fullmatch(r"[0-9a-f]{40}", value) is None for value in (base, head)):
        raise ValueError("local evidence requires resolved commit identities")
    return LOCAL_COORDINATION_DIRECTORY / f"validated-{base}-{head}.json"


def local_coverage(base: str, head: str, digest: str, seen=None) -> tuple[dict, set[str], set[str]]:
    """Read local receipts without rebinding an older result to a new SHA."""
    seen = set() if seen is None else seen
    if head in seen:
        raise ValueError("cyclic local validation evidence")
    seen.add(head)
    path = local_evidence_path(base, head)
    try:
        evidence = json.loads(path.read_text())
        if (evidence.get("status") != "success" or evidence.get("scope") != "local"
                or evidence.get("base") != base or evidence.get("head") != head
                or evidence.get("contract_digest") != digest):
            raise ValueError("local evidence base/head/contract differs; a full run is required")
        execution = evidence["execution_plan"]
        if any(execution.get(key) != evidence.get(key) for key in ("base", "head", "tested_sha", "tree", "contract_digest")):
            raise ValueError("local execution identity differs from its plan")
        verify_results(execution, evidence["results"], local=True)
        previous_suites, previous_images = set(), set()
        if evidence.get("since"):
            _, previous_suites, previous_images = local_coverage(base, evidence["since"], digest, seen)
        fresh = set(execution["suites"])
        reused = set(evidence["suites"]) - fresh
        if set(evidence["reused_suites"]) != reused or not reused <= previous_suites:
            raise ValueError("local reused suite coverage is incomplete")
        images = set(execution["production_image_targets"]) if "production-images" in fresh else previous_images
        if not set(evidence["production_image_targets"]) <= images:
            raise ValueError("local image coverage is incomplete")
        return evidence, set(evidence["suites"]) | fresh, images
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid local evidence: {path}") from exc


def local_execution_plan(root: Path, plan: dict, since: str | None) -> tuple[dict, dict]:
    if not since:
        return plan, {}
    since = git(root, "rev-parse", "--verify", "--end-of-options", f"{since}^{{commit}}")
    if since == plan["head"]:
        raise ValueError("--since must name an earlier validated head; this head already has evidence")
    git(root, "merge-base", "--is-ancestor", since, plan["head"])
    _, covered, images = local_coverage(plan["base"], since, plan["contract_digest"])
    delta = make_plan(root, since, "HEAD", original_head=plan["head"], comparison_head=plan["head"])
    selected = set(delta["suites"]) | ALWAYS | (set(plan["suites"]) - covered)
    if not set(plan["production_image_targets"]) <= images:
        selected.add("production-images")
    targets = (set(delta["production_image_targets"]) | set(plan["production_image_targets"])) if "production-images" in selected else set()
    execution = {**plan, "suites": [suite for suite in SUITES if suite in selected],
                 "production_image_targets": [target for target in BUILD_TARGETS if target in targets],
                 "deployments": [], "delta_paths": delta["changed_paths"]}
    reused = {suite: {"head": since, "evidence": str(local_evidence_path(plan["base"], since))}
              for suite in plan["suites"] if suite not in selected}
    return execution, reused


def append_ledger(record: dict) -> None:
    LOCAL_COORDINATION_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with (LOCAL_COORDINATION_DIRECTORY / "preflight-runs.jsonl").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def preflight(args: argparse.Namespace, *, slot_wait_s=0, record=None) -> int:
    source = Path(args.repo).resolve()
    work_root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1")) / "_scratch"
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ci-preflight-", dir=work_root))
    print(f"Preflight evidence: {work}", flush=True)
    record = {} if record is None else record
    record.update(evidence=str(work), per_suite_s={})
    phases = {}
    started = monotonic()
    cancelled = Event()
    succeeded = False
    try:
        root, base, original_head = isolated_merge(source, args.base, args.head, work / "repo")
        record.update(base=base, head=original_head)
        plan = make_plan(root, base, "HEAD", original_head=original_head)
        execution, reused = local_execution_plan(root, plan, args.since)
        write_json(work / "plan.json", execution)
        phases["clone_plan_s"] = round(monotonic() - started, 3)
        started = monotonic()
        subprocess.run([sys.executable, "scripts/publication_runtime_check.py"], cwd=root, check=True, timeout=40)
        phases["runtime_check_s"] = round(monotonic() - started, 3)
        if shutil.which("docker") is None:
            raise ValueError("Docker is required for the pinned Linux preflight toolchain")
        runtime = local_runtime()
        runtime.update(slot_wait_s=slot_wait_s, suites={}, phases=phases)
        write_json(work / "runtime.json", runtime)
        remove_containers()
        cleanup_old_images()
        suffix = "-arm" if runtime["server_architecture"] == "arm64" else ""
        image = f"shared-datasets-preflight{suffix}:{plan['contract_digest'][:16]}"
        started = monotonic()
        subprocess.run(["docker", "build", "--builder", "default", "--platform", runtime["platform"], "-f", ".github/docker/preflight.Dockerfile", "-t", image, "."], cwd=root, check=True)
        native_image = f"shared-datasets-native{suffix}:{plan['contract_digest'][:16]}"
        if "geospatial-integration" in execution["suites"]:
            subprocess.run(["docker", "build", "--builder", "default", "--platform", runtime["platform"], "-f", ".github/docker/geospatial-ci.Dockerfile", "-t", native_image, "."], cwd=root, check=True)
        phases["toolchain_build_s"] = round(monotonic() - started, 3)
        results = []
        failures = []

        def execute(suite):
            if cancelled.is_set():
                raise ValueError(f"{suite}: preflight cancelled before execution")
            suite_started = monotonic()
            try:
                output = work / suite
                output.mkdir()
                if suite == "production-images":
                    image_root = work / "production-image-repo"
                    shutil.copytree(root, image_root, symlinks=True)
                    with isolated_host_environment(work, dict(os.environ)) as environment:
                        environment.update({"CI": "true", "UV_PYTHON": TOOLCHAIN["python"],
                                            "UV_CACHE_DIR": str(work / "host-uv-cache"), "UV_PYTHON_INSTALL_DIR": str(work / "host-python"),
                                            "SHARED_DATASETS_PREFLIGHT_RUN": work.name})
                        command = ["uv", "run", "--no-project", "--isolated", "--managed-python", "--python", TOOLCHAIN["python"],
                                   "python", "-I", "scripts/ci_preflight.py", "run-suite", "--plan", str(work / "plan.json"), "--suite", suite,
                                   "--local-images", "--output", str(output)]
                        completed = run_host_suite(command, cwd=image_root, env=environment, cancelled=cancelled)
                    suite_runtime = {"transport": "host-docker", "exit_code": completed.returncode}
                    if completed.returncode:
                        raise ValueError(f"{suite}: host suite failed ({completed.returncode})")
                else:
                    selected_image = native_image if suite == "geospatial-integration" else image
                    suite_runtime = run_container(root, work, suite, selected_image, runtime["platform"], cancelled=cancelled)
                report = output / "result.json"
                if not report.exists():
                    raise ValueError(f"{suite} did not produce evidence")
                result = json.loads(report.read_text())
                if result.get("status") != "success":
                    raise ValueError(f"{suite} failed; retained evidence: {report}")
                return result, suite_runtime
            except BaseException as exc:
                failures.append(exc)
                cancelled.set()
                raise
            finally:
                record["per_suite_s"][suite] = round(monotonic() - suite_started, 3)
                print(f"Preflight {suite}: {record['per_suite_s'][suite]:.3f}s", flush=True)

        # Larger VMs start browser beside images and lint. Two-worker hosts
        # retain the original ordering so lint can fail before costly suites.
        priority = {"production-images": 0, "browser": 1} if runtime["suite_workers"] == 3 else {"production-images": 0}
        order = sorted(execution["suites"], key=lambda suite: priority.get(suite, 2))
        executor = ThreadPoolExecutor(max_workers=runtime["suite_workers"])
        futures = []
        started = monotonic()
        try:
            futures = [executor.submit(execute, suite) for suite in order]
            for future in as_completed(futures):
                try:
                    result, suite_runtime = future.result()
                except BaseException:
                    # A queued task may observe cancellation before the driver
                    # consumes the original failure. Retain that first cause.
                    if failures:
                        raise failures[0]
                    raise
                results.append(result)
                runtime["suites"][result["suite"]] = suite_runtime
                write_json(work / "runtime.json", runtime)
        finally:
            cancelled.set()
            for future in futures:
                future.cancel()
            try:
                remove_containers(work.name)
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
                phases["suites_s"] = round(monotonic() - started, 3)
        started = monotonic()
        verify_results(execution, results, local=True)
        if git(source, "rev-parse", f"{args.base}^{{commit}}") != base or git(source, "rev-parse", f"{args.head}^{{commit}}") != original_head:
            raise ValueError("base or head changed during preflight; evidence is stale")
        if git(source, "status", "--porcelain", "--untracked-files=normal"):
            raise ValueError("source changed during preflight; evidence is stale")
        remove_containers(work.name)
        runtime["production_image_archives_retained"] = False
        phases["verify_cleanup_s"] = round(monotonic() - started, 3)
        write_json(work / "runtime.json", runtime)
        evidence = {**plan, "status": "success", "scope": "local", "execution_plan": execution,
                    "results": results, "runtime": runtime, "since": git(root, "rev-parse", args.since) if args.since else None,
                    "reused_suites": reused}
        write_json(work / "preflight.json", evidence)
        write_json(local_evidence_path(base, original_head), evidence)
        succeeded = True
        print(f"All {len(results)} selected suites passed; {len(reused)} reused. Evidence: {work / 'preflight.json'}")
        return 0
    finally:
        cancelled.set()
        if not succeeded and shutil.which("docker"):
            remove_containers(work.name)
        if not succeeded:
            for archive in work.rglob("*.docker.tar"):
                archive.unlink()
        record["phases"] = phases


def load_plan(path: Path) -> dict:
    candidates = [json.loads(candidate.read_text()) for candidate in sorted(path.rglob("plan.json"))] if path.is_dir() else [json.loads(path.read_text())]
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        return select_plan(candidates, run_id=run_id, attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]))
    if len(candidates) != 1:
        raise ValueError("local validation requires exactly one plan")
    return candidates[0]


def run_local_preflight(args) -> int:
    record = {"head": args.head, "base": args.base, "start": datetime.now(timezone.utc).isoformat(),
              "slot_wait_s": 0, "per_suite_s": {}, "outcome": "failure"}
    def interrupt(*_):
        raise KeyboardInterrupt
    # Noninteractive shells can start background jobs with SIGINT ignored.
    # Explicit handlers make the documented driver-only stop reliable there.
    previous = {kind: signal.signal(kind, interrupt) for kind in (signal.SIGINT, signal.SIGTERM)}
    slot_started = monotonic()
    acquired = False
    try:
        with local_preflight_slot(Path(args.repo), args.base, args.head, wait=args.wait) as waited:
            acquired = True
            record["slot_wait_s"] = waited
            code = preflight(args, slot_wait_s=waited, record=record)
            record["outcome"] = "success" if code == 0 else "failure"
            return code
    except KeyboardInterrupt:
        record["outcome"] = "cancelled"
        raise
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)
        if not acquired:
            record["slot_wait_s"] = round(monotonic() - slot_started, 3)
        record["end"] = datetime.now(timezone.utc).isoformat()
        append_ledger(record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--wait", action="store_true", help="Wait for the exclusive local preflight slot")
    parser.add_argument("--since", help="Reuse compatible passing local evidence for this validated SHA")
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
    runner.add_argument("--image-target")
    runner.add_argument("--local-images", action="store_true")
    group_runner = commands.add_parser("run-image-group")
    group_runner.add_argument("--plan", type=Path, required=True)
    group_runner.add_argument("--group", choices=("short", "wdpa"), required=True)
    group_runner.add_argument("--output", type=Path, required=True)
    verifier = commands.add_parser("verify")
    verifier.add_argument("--plan", type=Path, required=True)
    verifier.add_argument("--results", type=Path, required=True)
    verifier.add_argument("--jobs", type=Path)
    verifier.add_argument("--output", type=Path)
    verifier.add_argument("--github-output", type=Path)
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
                    output.write("production-image-targets=" + json.dumps(planned_images(plan)) + "\n")
                    output.write("production-image-groups=" + json.dumps(sorted({image_group(target) for target in planned_images(plan)})) + "\n")
                    output.write("deploy-image-targets=" + json.dumps(deploy_image_targets(plan)) + "\n")
                    for suite in SUITES:
                        output.write(f"{suite}={'true' if suite in plan['suites'] else 'false'}\n")
                    for target in DEPLOYMENTS:
                        output.write(f"{target}={'true' if target in plan['deployments'] else 'false'}\n")
                    output.write(f"should_run={'true' if 'geospatial-integration' in plan['suites'] else 'false'}\n")
                    output.write(f"tested_sha={plan['tested_sha']}\n")
            return 0
        if args.command == "run-suite":
            plan = load_plan(args.plan)
            return 0 if run_suite(root, plan, args.suite, args.output, image_target=args.image_target, local_images=args.local_images)["status"] == "success" else 1
        if args.command == "run-image-group":
            return 0 if run_image_group(root, load_plan(args.plan), args.group, args.output) else 1
        if args.command == "verify":
            run_id = os.environ.get("GITHUB_RUN_ID")
            current_attempt = int(os.environ["GITHUB_RUN_ATTEMPT"]) if run_id else None
            plan = load_plan(args.plan)
            validate_checkout(root, plan)
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
            chosen = verify_results(plan, results, jobs, source_proofs=proofs, run_id=run_id,
                                    run_attempt=current_attempt)
            if args.output:
                evidence = {**plan, "status": "success"}
                if run_id:
                    evidence.update({
                        "source": {"run_id": run_id, "run_attempt": current_attempt},
                        "plan_artifact": f"ci-validation-plan-attempt{plan['source']['run_attempt']}",
                        "suite_artifacts": {suite: f"ci-result-{suite}-attempt{result['source']['run_attempt']}" for suite, result in chosen.items()},
                    })
                write_json(args.output, evidence)
            if args.github_output:
                with args.github_output.open('a') as output:
                    output.write('delivery=' + json.dumps(delivery_outputs(plan, os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REF'])) + '\n')
            print("Every selected suite passed for the exact tested revision and contract.")
            return 0
        if not args.base or not args.head:
            parser.error("--base and --head are required for isolated preflight")
        return run_local_preflight(args)
    except KeyboardInterrupt:
        print("Preflight cancelled; owned work cleaned up.", file=sys.stderr)
        return 130
    except (DeploymentError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
