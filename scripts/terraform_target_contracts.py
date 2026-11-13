"""Prove narrow IAM target ownership using Terraform's actual dependency graph.

Known prerequisites permit graph traversal, never mutation: the protected saved
plan allowlist remains authoritative. A new managed ancestor requires an explicit
owner and reviewed bootstrap sequencing before it can enter a narrow IAM plan.
"""
from __future__ import annotations

import argparse
from collections import deque
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ci_toolchain import TOOLCHAIN
from scripts.ingestion_targets import image_variables

ROOT = Path(__file__).resolve().parents[1]
CALLER = './.github/workflows/prod-terraform-target-apply.yml'
ADDRESS = re.compile(r'(?:module\.[A-Za-z0-9_-]+\.)*(?:data\.)?[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')
DATA = re.compile(r'^(?:module\.[A-Za-z0-9_-]+\.)*data\.')
QUOTED = r'"(?:[^"\\]|\\.)+"'
NODE = re.compile(r'^\s*(' + QUOTED + r')\s+\[label=')
EDGE = re.compile(r'^\s*(' + QUOTED + r')\s+->\s+(' + QUOTED + r');\s*$')


@lru_cache(maxsize=4)
def resource_index(root=ROOT, revision=None, *, paths=None):
    """Parse declarations once; comments/indentation cannot manufacture owners."""
    import hcl2
    from hcl2.utils import SerializationOptions
    from scripts.tested_image_bundle import repository_files

    files = ({name: (root / name).read_bytes() for name in paths} if paths is not None else
             {path.relative_to(root).as_posix(): path.read_bytes() for path in (root / 'terraform').rglob('*.tf')
              if '.terraform' not in path.parts} if revision is None else repository_files(revision, root))
    result = {}
    for path, raw in files.items():
        if not path.startswith('terraform/') or not path.endswith('.tf'):
            continue
        document = hcl2.loads(raw.decode(), serialization_options=SerializationOptions(with_comments=False, strip_string_quotes=True))
        declarations = {}
        for kind in ('resource', 'data', 'module'):
            for block in document.get(kind, []):
                for first, body in block.items():
                    for second, value in (body.items() if kind != 'module' else [(None, body)]):
                        name = '.'.join(filter(None, (None if kind == 'resource' else kind, first, second)))
                        if name in declarations:
                            raise ValueError(path + ': duplicate Terraform declaration ' + name)
                        declarations[name] = value
        result[path] = declarations
    return result


def scope_inputs(resources, root=ROOT, revision=None):
    """Registered scopes own source files and their local module source trees."""
    index = resource_index(root) if revision is None else resource_index(root, revision)
    sources = {name: path for path, rows in index.items() if Path(path).parent.as_posix() == 'terraform/envs/prod' for name in rows}
    selected = {sources[name] for resource in resources for name in sources
                if address(resource) == name or address(resource).startswith(name + '.')}
    pending = list(selected)
    while pending:
        path = pending.pop()
        for name, body in index[path].items():
            if name.startswith('module.'):
                directory = ((root / path).parent / body['source']).resolve().relative_to(root.resolve()).as_posix()
                selected.add(directory + '/')
                for child in index:
                    if Path(child).parent.as_posix() == directory and child not in selected:
                        selected.add(child)
                        pending.append(child)
    return selected | {path.removesuffix('.tf') + '_variables.tf' for path in selected if path.endswith('.tf')}


def production_image_limits(root=ROOT, *, targets=None):
    """Serialize literal production caps using the same parsed declaration index."""
    from scripts.tested_image_bundle import image_recipes
    selected = image_recipes(root) if targets is None else targets
    if not selected:
        return {}
    paths = tuple(sorted(f'terraform/envs/prod/{target.replace("-", "_")}.tf' for target in selected))
    index, result = resource_index(root, paths=paths), {}
    for target in selected:
        package = target.replace('-', '_')
        declarations = index[f'terraform/envs/prod/{package}.tf']
        if 'module.' + package + '_job' in declarations:
            limits = declarations['module.' + package + '_job']
        else:
            container, = declarations['google_cloud_run_v2_service.' + package]['template'][0]['containers']
            limits = container['resources'][0]['limits'] if 'resources' in container else {'cpu': '1', 'memory': '512Mi'}
        if any(not isinstance(limits.get(key), str) or not re.fullmatch(pattern, limits[key])
               for key, pattern in (('cpu', r'[0-9]+(?:\.[0-9]+)?'), ('memory', r'[0-9]+(?:\.[0-9]+)?(?:Mi|Gi)'))):
            raise ValueError(f'{target}: production limits must be literal CPU and memory quantities')
        result[target] = ['--cpu-period', '100000', '--cpu-quota', str(int(Decimal(limits['cpu']) * 100000)),
                          '--memory', limits['memory'].replace('Gi', 'g').replace('Mi', 'm')]
    return result


def registry(root: Path = ROOT, *, section='targets') -> dict[str, dict]:
    """One reviewed resource scope per stable, historically signed sync name."""
    value = json.loads((root / '.github/terraform-targets.json').read_text())
    if set(value) - {'schema_version', 'targets', 'plan_scopes', 'permission_probes', 'workflows', 'image_checks', 'pr_plan_exclusions', 'selection_exceptions'} or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Unsupported Terraform target registry')
    if section == 'deployments':
        return {row['selection']: {'workflow': filename, **row} for filename, row in value.get('workflows', {}).items() if 'selection' in row}
    return value[section] if section == 'targets' else value.get(section, {})


def plan_scope(workflow, scope, root=ROOT):
    """The runtime guard and static coverage consume one reviewed declaration."""
    item = registry(root, section='plan_scopes')[workflow][scope]
    if (not item or set(item) - {'exact', 'instances', 'modules', 'unindexed_instances'}
            or any(not isinstance(item.get(key, []), list) for key in ('exact', 'instances'))
            or not isinstance(item.get('modules', {}), dict)
            or type(item.get('unindexed_instances', False)) is not bool):
        raise ValueError('Invalid registered plan scope')
    for row in item.get('exact', []) + item.get('instances', []):
        address(row)
    for prefix, child in item.get('modules', {}).items():
        address(prefix + '.' + child)
    exact = set(item.get('exact', []))
    if item.get('unindexed_instances'):
        exact.update(item.get('instances', []))
    patterns = [re.compile('^' + re.escape(row) + r'\[') for row in item.get('instances', [])]
    patterns += [re.compile('^' + re.escape(row) + r'\.') for row in item.get('modules', {})]
    return exact, patterns


def resolve(sync_name: str, workflow: str, root: Path = ROOT, *, tf_vars: str = '') -> dict:
    item = registry(root)[sync_name]
    if item['workflow'] != workflow:
        raise ValueError('Terraform target does not belong to its trusted caller')
    resources = item['resources']
    if (set(item) - {'workflow', 'readiness_target', 'resources', 'block_deletes', 'delete_only',
                    'prepare_monitoring_slack', 'post_apply_wait_seconds', 'state_only', 'tf_vars', 'image_placeholder', 'prerequisites', 'retired_resources', 'deployment_hold'}
            or not isinstance(resources, list) or len(resources) != len(set(resources))
            or any(type(item[key]) is not bool for key in ('block_deletes', 'delete_only', 'prepare_monitoring_slack', 'state_only') if key in item)
            or bool(resources) == bool(item.get('state_only'))
            or item.get('block_deletes') and item.get('delete_only')):
        raise ValueError('Terraform targets require an exact mutation allowlist or a state-only scope')
    for row in resources:
        address(row)
    if 'deployment_hold' in item and (not isinstance(item['deployment_hold'], str)
                                     or not item['deployment_hold'].strip() or any(c in item['deployment_hold'] for c in '\r\n')):
        raise ValueError('Deployment hold requires a nonempty single-line reason')
    if item.get('state_only') and item.get('prepare_monitoring_slack'):
        raise ValueError('state-only reconciliation cannot prepare external mutations')
    result = {key: value for key, value in item.items() if key not in {
        'workflow', 'resources', 'image_placeholder', 'state_only', 'tf_vars', 'prerequisites', 'retired_resources', 'deployment_hold',
    }}
    placeholder = item.get('image_placeholder', 'unused-by-' + Path(workflow).stem)
    variables = [*item.get('tf_vars', []), *(name + '=' + placeholder for name in image_variables('', placeholder))]
    result.update(caller_workflow=workflow, sync_name=sync_name, targets='\n'.join(resources),
                  allowed_exact='\n'.join(resources), refusal_prefix='Refusing ' + sync_name,
                  tf_vars=tf_vars if item.get('state_only') else '\n'.join(variables))
    return result


def export(sync_name: str, workflow: str, *, tf_vars: str = '') -> None:
    """Serialize reviewed settings once for the workflow's plan/guard consumers."""
    values = resolve(sync_name, workflow, tf_vars=tf_vars)
    hold = registry()[sync_name].get('deployment_hold')
    if hold:
        raise ValueError(f'Deployment held for {sync_name}: {hold}')
    with Path(os.environ['GITHUB_ENV']).open('a') as stream:
        for key, value in values.items():
            if isinstance(value, bool):
                value = str(value).lower()
            delimiter = 'TERRAFORM_' + hashlib.sha256(str(value).encode()).hexdigest()
            if delimiter in str(value).splitlines():
                raise ValueError('Invalid Terraform environment delimiter')
            stream.write(f'{key.upper()}<<{delimiter}\n{value}\n{delimiter}\n')


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


def workflow(root: Path, filename: str) -> dict:
    """Resolve the same reviewed settings for selection and offline contracts."""
    import yaml

    value = yaml.safe_load((root / '.github/workflows' / filename).read_text())
    for job in value['jobs'].values():
        if job.get('uses') == CALLER:
            inputs = job['with']
            job['with'] = {**inputs, **resolve(inputs['sync_name'], inputs['caller_workflow'], root,
                                            tf_vars=inputs.get('tf_vars', ''))}
    return value


def callers(root: Path) -> dict[str, dict]:
    jobs = {}
    for path in sorted((root / '.github/workflows').iterdir()):
        if not path.is_file() or path.suffix not in {'.yml', '.yaml'}:
            continue
        for name, job in workflow(root, path.name)['jobs'].items():
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
    jobs = callers(root)
    syncs = registry(root)
    if len(jobs) != len(syncs) or {job['with']['sync_name'] for job in jobs.values()} != set(syncs) or any('prerequisites' not in row for row in syncs.values()):
        raise ValueError('IAM prerequisites must cover exactly every registered narrow caller')
    results, errors = {}, []
    for key, job in jobs.items():
        inputs = job['with']
        row = syncs[inputs['sync_name']]
        prerequisites, retired = row['prerequisites'], row.get('retired_resources', [])
        if inputs['targets'] == inputs['allowed_exact'] == '':
            # No -target arguments produces a full plan. Its empty resource
            # allowlist rejects every mutation, so only state/outputs can change.
            # Image/secret preparation would violate that state-only boundary.
            if (inputs.get('allowed_patterns', '') or inputs.get('prepare_monitoring_slack', False)
                    or prerequisites != [] or retired or job.get('needs')):
                raise ValueError(key + ': state-only reconciliation requires an empty mutation scope and no preparation or prerequisites')
            results[key] = {'targets': [], 'managed_prerequisites': [], 'state_only': True}
            continue
        targets, owned = declared(inputs['targets']), declared(inputs['allowed_exact'])
        if not targets <= owned or inputs.get('allowed_patterns', '').strip():
            raise ValueError(key + ': narrow targets require an exact mutation allowlist')
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
        if not isinstance(retired, list) or not all(isinstance(node, str) for node in retired) or len(retired) != len(set(retired)) or any(address(node) != node or node not in targets or node not in owned or node in graph for node in retired):
            raise ValueError(key + ': retired target must remain absent and explicitly owned by this workflow')
        paths = {}
        # A shared ancestor must report every affected target. One combined BFS
        # hides earlier refusal paths when a new target reaches the same node.
        for target in sorted(targets - set(retired)):
            reachable = closure(graph, {target})
            paths.update(reachable)
            for node, path in reachable.items():
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
            expected = 'google_storage_bucket.shared_bucket -> ' + failure['forbidden_resource']
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
    parser.add_argument('--sync-name')
    parser.add_argument('--workflow')
    args = parser.parse_args()
    try:
        if args.sync_name or args.workflow:
            if not args.sync_name or not args.workflow:
                raise ValueError('Both registered sync name and caller workflow are required')
            export(args.sync_name, args.workflow, tf_vars=os.environ.get('INPUT_TF_VARS', ''))
            return
        if args.output:
            evidence = verify(ROOT, args.output)
        else:
            workdir = Path(os.environ.get('SHARED_DATASETS_WORKDIR', str(Path(tempfile.gettempdir()) / 'shared-datasets-1'))) / '_scratch'
            workdir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='terraform-targets-', dir=workdir) as directory:
                evidence = verify(ROOT, Path(directory))
        print(f"Terraform target ownership passed for {len(evidence['jobs'])} narrow jobs; both historical activation controls were rejected.")
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
