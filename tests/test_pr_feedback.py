"""Execute the shipped review callback without candidate code or cloud credentials."""
import json
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_review_refresh_targets_only_latest_exact_head_acceptance_and_its_dependents():
    path = ROOT / '.github/workflows/owner-acceptance-refresh.yml'
    workflow = yaml.safe_load(path.read_text())
    assert len(path.read_text().splitlines()) <= 60
    assert workflow['permissions'] == {'actions': 'write', 'pull-requests': 'read'}
    triggers = workflow.get('on', workflow.get(True))
    assert triggers['pull_request_review']['types'] == ['submitted', 'dismissed']
    assert triggers['pull_request_target']['types'] == ['ready_for_review']
    assert triggers['workflow_run'] == {'workflows': ['CI / Validate and deliver'], 'types': ['completed']}
    job = workflow['jobs']['refresh']
    assert job['timeout-minutes'] == 5
    assert not {'environment', 'secrets', 'permissions'} & job.keys()
    assert len(job['steps']) == 1 and job['steps'][0]['uses'] == 'actions/github-script@v7'
    script = job['steps'][0]['with']['script']
    harness = r'''
const assert = require('node:assert/strict');
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const script = new AsyncFunction('github', 'context', JSON.parse(process.argv[1]));
(async () => {
  for (const failure of [null, 'draft', 'fork', 'closed', 'base', 'missing', 'stale', 'dismissed', 'changes',
                         'foreign-review', 'moved-run', 'running', 'success', 'no-run', 'push', 'older-approval', 'different-pr', 'repeat-completion', 'old-completion', 'old-review']) {
    const repo = {owner:'SkyTruth', repo:'shared-datasets-1'};
    const pr = {number:27, state: failure === 'closed' ? 'closed':'open', draft:failure === 'draft',
      head:{sha:'b'.repeat(40), repo:{full_name:failure === 'fork' ? 'other/fork':'SkyTruth/shared-datasets-1'}},
      base:{ref:failure === 'base' ? 'feature':'main'}, user:{login:'dependabot[bot]'}};
    let reviews = [{id:1, submitted_at:'2026-11-05T00:00:00Z', state:'APPROVED',
      commit_id:failure === 'stale' ? 'a'.repeat(40):pr.head.sha,
      user:{login:failure === 'foreign-review' ? 'another':'jonaraphael'}}];
    if (failure === 'missing') reviews = [];
    if (['dismissed','changes','older-approval'].includes(failure)) reviews.push({...reviews[0], id:2,
      submitted_at:'2026-11-05T01:00:00Z', state:failure === 'changes' ? 'CHANGES_REQUESTED':'DISMISSED'});
    const calls = [];
    const github = {rest:{pulls:{get:async () => ({data:pr}), listReviews:'reviews'}, actions:{
      listWorkflowRuns:async args => {
        assert.equal(args.head_sha,pr.head.sha); assert.equal(args.event,'pull_request');
        assert.equal(args.workflow_id,'ci.yml'); assert.equal(args.per_page,1);
        return {data:{workflow_runs:failure === 'no-run' ? []:[{id:73, run_attempt:failure === 'repeat-completion' ? 2:1, pull_requests:[{number:failure === 'different-pr' ? 28:27}],
          head_sha:failure === 'moved-run' ? 'c'.repeat(40):pr.head.sha,
          status:failure === 'running' ? 'in_progress':'completed'}]}};
      }, listJobsForWorkflowRun:'jobs',
      reRunJobForWorkflowRun:async args => calls.push(args)
    }}, paginate:async (endpoint,args) => {
      if (endpoint === 'reviews') return reviews;
      assert.equal(args.run_id,73); assert.equal(args.filter,'latest');
      return [{id:11,name:'tests',conclusion:'failure'}, {id:12,name:'ci-ready',conclusion:'failure'},
        {id:13,name:'owner-acceptance',started_at:'2026-11-05T00:01:00Z',conclusion:failure === 'success' ? 'success':'failure'}];
    }};
    const completion = {event:'pull_request', head_sha:pr.head.sha, pull_requests:[{number:27}]};
    const payload = failure === 'repeat-completion' ? {workflow_run:completion} :
      failure === 'old-completion' ? {workflow_run:{...completion,head_sha:'a'.repeat(40)}} :
      failure === 'old-review' ? {pull_request:pr,review:{commit_id:'a'.repeat(40)}} :
      failure === 'push' ? {workflow_run:{...completion,event:'push'}} : {pull_request:pr};
    await script(github,{repo,payload});
    assert.deepEqual(calls, failure === null ? [{...repo,job_id:13}]:[],failure);
    if (failure === null) {
      calls.length = 0;
      await script(github,{repo,payload:{workflow_run:completion}});
      assert.deepEqual(calls,[{...repo,job_id:13}]); // approval arrived before CI completed
    }
  }
})().catch(error => {console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness, json.dumps(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_shared_shard_commands_partition_every_test_and_require_both_producers(tmp_path):
    from scripts.ci_contract import resolve_selection, verify_results
    from scripts.ci_preflight import suite_commands
    from test_ci_preflight import plan, results_for, jobs_for
    import pytest
    selected = resolve_selection(['tests/test_pr_feedback.py'])
    assert {'tests', 'tests-control-plane'} <= set(selected['suites'])
    validation = plan()
    partitions = []
    for suite in ('tests', 'tests-control-plane'):
        commands = [argv for argv, _ in suite_commands(suite, ROOT, validation, tmp_path / suite)]
        pytest_command = next(argv for argv in commands if 'pytest' in argv)
        partitions.append({arg for arg in pytest_command if arg.startswith('tests/')})
        init = next(i for i, argv in enumerate(commands) if argv[0] == 'terraform' and 'init' in argv)
        assert init < commands.index(pytest_command)
        assert ['uv', 'sync', '--locked', '--all-groups'] in commands
    assert all(partitions) and not partitions[0] & partitions[1]
    assert set.union(*partitions) == {str(path.relative_to(ROOT)) for path in set((ROOT / 'tests').rglob('test_*.py')) | set((ROOT / 'tests').rglob('*_test.py'))}
    jobs = jobs_for(validation)
    for state in ('failure', 'cancelled', 'skipped', None):
        jobs['tests-control-plane'] = {'result': state}
        with pytest.raises(ValueError, match='tests-control-plane'):
            verify_results(validation, results_for(validation), jobs)
