"""Pre-merge acceptance and bounded workflow execution use shipped contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = 'SkyTruth/shared-datasets-1'
HEAD = 'b' * 40


@pytest.mark.parametrize('path,name,job', [(path, name, job)
    for path in sorted((ROOT / '.github/workflows').glob('*.yml'))
    for name, job in yaml.safe_load(path.read_text())['jobs'].items() if 'runs-on' in job],
    ids=lambda value: value.name if isinstance(value, Path) else value if isinstance(value, str) else None)
def test_every_runner_job_has_a_timeout(path, name, job):
    # Reusable callers cannot declare timeout-minutes; their runner jobs must.
    assert type(job.get('timeout-minutes')) is int and 0 < job['timeout-minutes'] <= 360, f'{path.name}/{name}'


def test_dependabot_requests_the_deployment_owner():
    config = yaml.safe_load((ROOT / '.github/dependabot.yml').read_text())
    assert all('jonaraphael' in item['reviewers'] for item in config['updates'])


@pytest.mark.parametrize('case,accepted', [
    ('approved', True), ('self', True), ('missing', False), ('stale', False),
    ('dismissed', False), ('changes', False), ('foreign', False), ('moved', False), ('api-failure', False),
])
def test_premerge_acceptance_uses_the_same_exact_head_policy_in_clean_python(tmp_path, case, accepted):
    repo = {'id': 123, 'full_name': REPO, 'default_branch': 'main'}
    pr = {'number': 27, 'state': 'open', 'merged': False, 'draft': False,
          'user': {'login': 'jonaraphael' if case == 'self' else 'dependabot[bot]'},
          'head': {'sha': 'c' * 40 if case == 'moved' else HEAD, 'repo': repo},
          'base': {'ref': 'main', 'repo': repo}}
    if case == 'foreign':
        pr['head']['repo'] = {**repo, 'id': 999, 'full_name': 'other/fork'}
    review = {'id': 1, 'pull_request_url': f'https://api.github.com/repos/{REPO}/pulls/27',
              'state': {'dismissed': 'DISMISSED', 'changes': 'CHANGES_REQUESTED'}.get(case, 'APPROVED'),
              'user': {'login': 'jonaraphael'}, 'commit_id': 'a' * 40 if case == 'stale' else HEAD,
              'submitted_at': '2026-11-04T00:00:00Z'}
    fixture = {f'repos/{REPO}': repo, f'repos/{REPO}/pulls/27': pr,
               f'repos/{REPO}/pulls/27/reviews?per_page=100': [[] if case == 'missing' else [review]]}
    gh = tmp_path / 'gh'
    gh.write_text(f'#!{sys.executable}\nimport json,sys\nfixture={fixture!r}\n' +
                  ('sys.exit(1)\n' if case == 'api-failure' else 'print(json.dumps(fixture[sys.argv[-1]]))\n'))
    gh.chmod(0o755)
    command = [sys.executable, '-I', '-S', '.github/actions/deployment-bootstrap/approval.py', '27']
    # The post-merge bootstrap deliberately supplies no argument. The PR check
    # supplies the expected head, never a synthetic merge SHA.
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
             'GITHUB_REPOSITORY': REPO, 'EXECUTOR_SHA': HEAD})
    assert (done.returncode == 0) is accepted, done.stdout + done.stderr
    if accepted:
        receipt = json.loads(done.stdout)
        assert receipt['executor_sha'] == HEAD and receipt['pr_number'] == 27
        assert receipt['acceptance']['reviewed_commit'] == HEAD
