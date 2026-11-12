"""Execute the shipped review callback without candidate code or cloud credentials."""
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('event,source_event,conclusion,title,allocated', [
    ('workflow_run', 'pull_request', 'failure', 'CI / ready PR', True),
    ('workflow_run', 'pull_request', 'success', 'CI / ready PR', False),
    ('workflow_run', 'pull_request', 'cancelled', 'CI / ready PR', False),
    ('workflow_run', 'push', 'failure', 'CI / ready PR', False),
    ('workflow_run', 'workflow_dispatch', 'failure', 'CI / ready PR', False),
    ('workflow_run', 'schedule', 'failure', 'CI / ready PR', False),
    ('pull_request_review', '', '', '', True),
    ('workflow_run', 'pull_request', 'failure', 'Draft CI / draft PR', False),
])
def test_owner_acceptance_refresh_allocates_only_relevant_events(event, source_event, conclusion, title, allocated):
    workflow = yaml.safe_load((ROOT / '.github/workflows/owner-acceptance-refresh.yml').read_text())
    condition = workflow['jobs']['refresh']['if'].removeprefix('${{').removesuffix('}}').strip()
    github = {'event_name': event, 'event': {'workflow_run': {'event': source_event, 'conclusion': conclusion, 'display_title': title}}}
    result = subprocess.run(['node', '-e',
        'process.stdout.write(String(Function("github", "startsWith", "return " + process.argv[1])(JSON.parse(process.argv[2]), (value,prefix)=>value.startsWith(prefix))))',
        condition, json.dumps(github)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(allocated).lower()


def test_review_refresh_targets_only_exact_head_gate_and_revokes_dismissed_approval():
    path = ROOT / '.github/workflows/owner-acceptance-refresh.yml'
    workflow = yaml.safe_load(path.read_text())
    assert workflow['permissions'] == {'actions': 'write', 'pull-requests': 'read'}
    triggers = workflow.get('on', workflow.get(True))
    assert triggers['pull_request_review']['types'] == ['submitted', 'dismissed']
    assert 'pull_request_target' not in triggers
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
  for (const problem of [null, 'draft', 'fork', 'closed', 'base', 'missing', 'stale', 'dismissed-failed', 'changes-failed',
                        'foreign-review', 'moved-run', 'running', 'success', 'other-failure', 'no-run', 'push',
                        'different-pr', 'repeat-completion', 'old-completion', 'old-review', 'dismissed-green', 'changes-green']) {
    const repo = {owner:'SkyTruth', repo:'shared-datasets-1'};
    const pr = {number:27, state:problem === 'closed' ? 'closed':'open', draft:problem === 'draft',
      head:{sha:'b'.repeat(40), repo:{full_name:problem === 'fork' ? 'other/fork':'SkyTruth/shared-datasets-1'}},
      base:{ref:problem === 'base' ? 'feature':'main'}, user:{login:'dependabot[bot]'}};
    let reviews = [{id:1, submitted_at:'2026-11-05T00:00:00Z', state:'APPROVED',
      commit_id:problem === 'stale' ? 'a'.repeat(40):pr.head.sha,
      user:{login:problem === 'foreign-review' ? 'another':'jonaraphael'}}];
    if (problem === 'missing') reviews = [];
    if (problem?.startsWith('dismissed') || problem?.startsWith('changes')) reviews.push({...reviews[0], id:2,
      submitted_at:'2026-11-05T01:00:00Z', state:problem.startsWith('changes') ? 'CHANGES_REQUESTED':'DISMISSED'});
    const calls = [];
    const github = {rest:{pulls:{get:async () => ({data:pr}), listReviews:'reviews'}, actions:{
      listWorkflowRuns:async args => {
        assert.equal(args.head_sha,pr.head.sha); assert.equal(args.event,'pull_request');
        assert.equal(args.workflow_id,'ci.yml'); assert.equal(args.per_page,1);
        return {data:{workflow_runs:problem === 'no-run' ? []:[{id:73, run_attempt:problem === 'repeat-completion' ? 2:1,
          pull_requests:[{number:problem === 'different-pr' ? 28:27}],
          head_sha:problem === 'moved-run' ? 'c'.repeat(40):pr.head.sha,
          status:problem === 'running' ? 'in_progress':'completed'}]}};
      }, listJobsForWorkflowRun:'jobs', reRunJobForWorkflowRun:async args => calls.push(args)
    }}, paginate:async (endpoint,args) => {
      if (endpoint === 'reviews') return reviews;
      assert.equal(args.run_id,73); assert.equal(args.filter,'latest');
      return [{id:11,name:'tests',conclusion:'failure'}, {id:12,name:'ci-ready',
        conclusion:['success', 'dismissed-green', 'changes-green'].includes(problem) ? 'success':'failure',
        steps:[{name:'Owner approval of this exact head required before merge', started_at:'2026-11-05T00:01:00Z',
          conclusion:['success','other-failure','dismissed-green','changes-green'].includes(problem) ? 'success':'failure'}]}];
    }};
    const completion = {event:'pull_request', conclusion:'failure', head_sha:pr.head.sha, pull_requests:[{number:27}]};
    const review = {commit_id:problem === 'old-review' ? 'a'.repeat(40):pr.head.sha,
      user:{login:problem === 'foreign-review' ? 'another':'jonaraphael'}};
    const payload = problem === 'repeat-completion' ? {workflow_run:completion} :
      problem === 'old-completion' ? {workflow_run:{...completion,head_sha:'a'.repeat(40)}} :
      problem === 'push' ? {workflow_run:{...completion,event:'push'}} : {pull_request:pr,review};
    await script(github,{repo,payload});
    const rerun = [null,'dismissed-green','changes-green'].includes(problem);
    assert.deepEqual(calls, rerun ? [{...repo,job_id:12}]:[],String(problem));
    if (problem === null) {
      calls.length = 0;
      await script(github,{repo,payload:{workflow_run:completion}});
      assert.deepEqual(calls,[{...repo,job_id:12}]); // approval arrived before CI completed
    }
  }
})().catch(error => {console.error(error);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness, json.dumps(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_selected_control_producer_must_succeed():
    from scripts.ci_contract import resolve_selection, verify_results
    from test_ci_preflight import plan, results_for, jobs_for
    selected = resolve_selection(['scripts/ci_preflight.py'])
    assert {'tests', 'tests-control-plane'} <= set(selected['suites'])
    validation = plan(selected['suites'])
    jobs = jobs_for(validation)
    for state in ('failure', 'cancelled', 'skipped', None):
        jobs['tests-control-plane'] = {'result': state}
        with pytest.raises(ValueError, match='tests-control-plane'):
            verify_results(validation, results_for(validation), jobs)
