"""Runner diagnostics preserve fixed capacity and event-bound selection."""
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from workflow_helpers import workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
PURE = {'tests', 'tests-control-plane', 'browser'}


def evaluate(expression, event, inputs):
    expression = expression.removeprefix('${{').removesuffix('}}').strip()
    github = {'event_name': event, 'event': {'pull_request': {'base': {'sha': 'pr-base'}},
              'before': 'push-base' if event == 'push' else ''}, 'sha': 'checked-head'}
    program = "console.log(Function('github','inputs','return '+process.argv[1])(JSON.parse(process.argv[2]),JSON.parse(process.argv[3])));"
    return subprocess.check_output(['node', '-e', program, expression, json.dumps(github), json.dumps(inputs)], text=True).strip()


@pytest.mark.parametrize('event,x64,expected', [
    ('pull_request', True, 'ubuntu-24.04-arm'), ('push', True, 'ubuntu-24.04-arm'),
    ('workflow_dispatch', False, 'ubuntu-24.04-arm'), ('workflow_dispatch', True, 'ubuntu-24.04'),
])
def test_only_manual_diagnostics_can_switch_the_three_pure_runners(event, x64, expected):
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    jobs = workflow['jobs']
    expressions = {jobs[name]['runs-on'] for name in PURE}
    assert len(expressions) == 1
    assert evaluate(expressions.pop(), event, {'x64_validation': x64}) == expected
    for name in ('geospatial-changes', 'lint', 'geospatial-integration', 'production-images', 'sdk-validation', 'ci-ready'):
        assert jobs[name]['runs-on'] == 'ubuntu-24.04'
    inputs = workflow_triggers(workflow)['workflow_dispatch']['inputs']
    assert inputs['x64_validation']['type'] == 'boolean'
    assert inputs['x64_validation']['default'] is False
    assert inputs['base_sha']['type'] == 'string'


@pytest.mark.parametrize('event,base,expected', [
    ('pull_request', 'diagnostic-base', 'pr-base'), ('push', 'diagnostic-base', 'push-base'),
    ('workflow_dispatch', 'diagnostic-base', 'diagnostic-base'), ('workflow_dispatch', '', 'checked-head'),
])
def test_diagnostic_base_override_cannot_change_pr_or_push_comparisons(event, base, expected):
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    selector = next(step for step in jobs['geospatial-changes']['steps'] if step.get('id') == 'plan')
    assert evaluate(selector['env']['BASE_SHA'], event, {'base_sha': base}) == expected
