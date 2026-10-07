"""Prove narrow IAM target ownership using Terraform's actual dependency graph.

Known prerequisites permit graph traversal, never mutation: the protected saved
plan allowlist remains authoritative. A new managed ancestor requires an explicit
owner and reviewed bootstrap sequencing before it can enter a narrow IAM plan.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ci_toolchain import TOOLCHAIN

ROOT = Path(__file__).resolve().parents[1]
CALLER = './.github/workflows/prod-terraform-target-apply.yml'
ADDRESS = re.compile(r'(?:module\.[A-Za-z0-9_-]+\.)*(?:data\.)?[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')
DATA = re.compile(r'^(?:module\.[A-Za-z0-9_-]+\.)*data\.')
QUOTED = r'"(?:[^"\\]|\\.)+"'
NODE = re.compile(r'^\s*(' + QUOTED + r')\s+\[label=')
EDGE = re.compile(r'^\s*(' + QUOTED + r')\s+->\s+(' + QUOTED + r');\s*$')


def address(value: str) -> str:
    # The simplified Terraform graph contains resource declarations, not their
    # count/for_each instances. Traversing the whole declaration is conservative.
    result = re.sub(r'\[(?:"[^"\n]+"|\d+)\]', '', value)
    if not ADDRESS.fullmatch(result):
        raise ValueError('Invalid Terraform resource address: ' + value)
    return result


def parse_graph(dot: str) -> dict[str, set[str]]:
    graph = {}
    edges = []
    if not dot.startswith('digraph G {') or not dot.rstrip().endswith('}'):
        raise ValueError('Terraform graph is incomplete')
    for line in dot.splitlines()[1:-1]:
        node, edge = NODE.match(line), EDGE.fullmatch(line)
        if node:
            name = address(json.loads(node[1]))
            if name in graph:
                raise ValueError('Duplicate Terraform graph node: ' + name)
            graph[name] = set()
        elif edge:
            edges.append((address(json.loads(edge[1])), address(json.loads(edge[2]))))
        elif line.strip() not in {'rankdir = "RL";', 'node [shape = rect, fontname = "sans-serif"];', 'fontname = "sans-serif"', '}'} and not line.strip().startswith(('subgraph ', 'label = ')):
            raise ValueError('Unrecognized Terraform graph statement: ' + line.strip())
    if not graph:
        raise ValueError('Terraform graph has no resources')
    for source, dependency in edges:
        if source not in graph or dependency not in graph:
            raise ValueError('Terraform graph edge has no declared node')
        graph[source].add(dependency)
    return graph


def closure(graph: dict[str, set[str]], targets: set[str]) -> dict[str, list[str]]:
    paths = {target: [target] for target in sorted(targets)}
    pending = deque(paths)
    while pending:
        current = pending.popleft()
        if current not in graph:
            raise ValueError('Target is absent from Terraform graph: ' + current)
        for dependency in sorted(graph[current]):
            if dependency not in paths:
                paths[dependency] = [*paths[current], dependency]
                pending.append(dependency)
    return paths


def callers(root: Path) -> dict[str, dict]:
    jobs = {}
    for path in sorted((root / '.github/workflows').iterdir()):
        if not path.is_file() or path.suffix not in {'.yml', '.yaml'}:
            continue
        workflow = yaml.safe_load(path.read_text())
        for name, job in workflow['jobs'].items():
            if job.get('uses') == CALLER:
                jobs[path.name + '/' + name] = job
    if not jobs:
        raise ValueError('No narrow Terraform callers were found')
    return jobs


def declared(values) -> set[str]:
    if not isinstance(values, str) or not values.strip():
        raise ValueError('Explicit nonempty Terraform resource scope is required')
    return {address(row.strip()) for row in values.splitlines() if row.strip()}


def check_graph(root: Path, graph: dict[str, set[str]]) -> dict:
    policy = json.loads((root / 'terraform/iam-plan-prerequisites.json').read_text())
    jobs = callers(root)
    if set(policy) != {'schema_version', 'jobs', 'retired_targets'} or type(policy['schema_version']) is not int or policy['schema_version'] != 1 or set(policy.get('jobs', {})) != set(jobs):
        raise ValueError('IAM prerequisite policy must cover exactly every narrow caller job')
    retired_by_job = policy.get('retired_targets', {})
    if not isinstance(retired_by_job, dict) or not set(retired_by_job) <= set(jobs):
        raise ValueError('Retired targets must belong to a registered narrow caller')
    results, errors = {}, []
    for key, job in jobs.items():
        inputs = job['with']
        targets, owned = declared(inputs['targets']), declared(inputs['allowed_exact'])
        if not targets <= owned or inputs.get('allowed_patterns', '').strip():
            raise ValueError(key + ': narrow targets require an exact mutation allowlist')
        prerequisites = policy['jobs'][key]
        if not isinstance(prerequisites, list) or not all(isinstance(row, str) for row in prerequisites) or len(prerequisites) != len(set(prerequisites)) or any(address(row) != row for row in prerequisites):
            raise ValueError(key + ': invalid prerequisite identities')
        permitted = owned | set(prerequisites)
        # Existing same-workflow bootstrap jobs are explicit ordering edges. Their
        # owned resources are prerequisites of this job, not new mutation scope.
        needs = job.get('needs', [])
        for predecessor in ([needs] if isinstance(needs, str) else needs):
            parent = key.rsplit('/', 1)[0] + '/' + predecessor
            if parent not in jobs:
                raise ValueError(key + ': bootstrap predecessor is not a narrow caller')
            permitted.update(declared(jobs[parent]['with']['allowed_exact']))
        retired = retired_by_job.get(key, [])
        if not isinstance(retired, list) or not all(isinstance(node, str) for node in retired) or len(retired) != len(set(retired)) or any(address(node) != node or node not in targets or node not in owned or node in graph for node in retired):
            raise ValueError(key + ': retired target must remain absent and explicitly owned by this workflow')
        paths = closure(graph, targets - set(retired))
        for node, path in paths.items():
            if not DATA.match(node) and node not in permitted:
                errors.append(key + ': undeclared managed prerequisite: ' + ' -> '.join(path))
        results[key] = {'targets': sorted(targets), 'managed_prerequisites': sorted(node for node in paths if not DATA.match(node) and node not in owned)}
    if errors:
        raise ValueError('TERRAFORM_TARGET_OWNERSHIP:\n' + '\n'.join(errors))
    return results


def isolated_environment(directory: Path) -> dict[str, str]:
    allowed = {'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY', 'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy'}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(HOME=str(directory / 'home'), TF_CLI_CONFIG_FILE=str(directory / 'terraform.rc'), TF_IN_AUTOMATION='1', TF_INPUT='0', GODEBUG='asyncpreemptoff=1')
    (directory / 'home').mkdir()
    return environment


def prepare(root: Path, directory: Path, providers: Path) -> Path:
    if not providers.is_dir():
        raise ValueError('Initialize the pinned prod providers with -backend=false before graph validation')
    for name in ('terraform', 'catalog', 'ingestion'):
        shutil.copytree(root / name, directory / name, ignore=shutil.ignore_patterns('.terraform', '*.tfstate*', '__pycache__'))
    prod = directory / 'terraform/envs/prod'
    override = prod / 'ci_local_backend_override.tf.json'
    if override.exists():
        raise ValueError('Reserved offline backend override already exists')
    override.write_text(json.dumps({'terraform': {'backend': {'local': {}}}}))
    # Only the existing lock-verified provider installation is visible. No
    # network provider installation or caller CLI/cloud configuration is used.
    (directory / 'terraform.rc').write_text('provider_installation { filesystem_mirror { path = ' + json.dumps(str(providers.resolve())) + ' } }\n')
    return prod


def graph(prod: Path, executable: str, environment: dict[str, str], directory: Path) -> str:
    def run(*args):
        completed = subprocess.run([executable, '-chdir=' + str(prod), *args], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        (directory / (args[0] + '.stderr.log')).write_text(completed.stderr)
        if completed.returncode:
            raise ValueError('Offline Terraform ' + args[0] + ' failed:\n' + completed.stdout + completed.stderr)
        return completed.stdout
    (directory / 'init.log').write_text(run('init', '-input=false', '-lockfile=readonly', '-no-color'))
    dot = run('graph')
    (directory / 'graph.dot').write_text(dot)
    return dot


def verify(root: Path, output: Path, *, executable='terraform', providers: Path | None = None) -> dict:
    version = json.loads(subprocess.check_output([executable, 'version', '-json'], text=True))['terraform_version']
    if version != TOOLCHAIN['terraform']:
        raise ValueError('Terraform graph validation requires pinned version ' + TOOLCHAIN['terraform'])
    providers = providers or Path(os.environ.get('TF_DATA_DIR', root / 'terraform/envs/prod/.terraform')) / 'providers'
    output.mkdir(parents=True, exist_ok=True)
    positive = output / 'current'
    positive.mkdir()
    prod = prepare(root, positive, providers)
    environment = isolated_environment(positive)
    dot = graph(prod, executable, environment, positive)
    evidence = {'terraform': version, 'graph_sha256': hashlib.sha256(dot.encode()).hexdigest(), 'jobs': check_graph(root, parse_graph(dot)), 'negative_controls': []}
    fixture_dir = root / 'tests/fixtures/terraform_targets'
    failure = json.loads((fixture_dir / 'failed-main.json').read_text())
    raw = (fixture_dir / failure['fixture']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != failure['sha256'] or failure['path'] != 'terraform/envs/prod/shared_bucket_public.tf':
        raise ValueError('Historical Terraform failure fixture changed')
    for enabled in (False, True):
        negative = output / ('failed-enabled-' + str(enabled).lower())
        negative.mkdir()
        broken_prod = prepare(root, negative, providers)
        (negative / failure['path']).write_bytes(raw)
        config_path = negative / 'catalog/dataset-usage.json'
        config = json.loads(config_path.read_text())
        config['collection_enabled'] = enabled
        config_path.write_text(json.dumps(config))
        broken_dot = graph(broken_prod, executable, isolated_environment(negative), negative)
        try:
            check_graph(root, parse_graph(broken_dot))
        except ValueError as exc:
            expected = 'google_storage_bucket_iam_member.wdpa_reset_translation_reader -> google_storage_bucket.shared_bucket -> ' + failure['forbidden_resource']
            if expected not in str(exc):
                raise ValueError('Historical negative control did not fail on its observed dependency chain') from exc
            (negative / 'refusal.txt').write_text(str(exc) + '\n')
        else:
            raise ValueError('Historical negative control unexpectedly passed')
        evidence['negative_controls'].append({'revision': failure['revision'], 'collection_enabled': enabled, 'forbidden_resource': failure['forbidden_resource'], 'graph_sha256': hashlib.sha256(broken_dot.encode()).hexdigest(), 'rejected': True})
    (output / 'evidence.json').write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n')
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.output:
            evidence = verify(ROOT, args.output)
        else:
            workdir = Path(os.environ.get('SHARED_DATASETS_WORKDIR', str(Path(tempfile.gettempdir()) / 'shared-datasets-1'))) / '_scratch'
            workdir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='terraform-targets-', dir=workdir) as directory:
                evidence = verify(ROOT, Path(directory))
        print(f"Terraform target ownership passed for {len(evidence['jobs'])} narrow jobs; both historical activation controls were rejected.")
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
