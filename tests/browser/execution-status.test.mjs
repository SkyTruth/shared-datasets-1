import assert from 'node:assert/strict';
import {test} from 'node:test';
import {executionStatusText} from '../../web/catalog/release-reference.js';

const observation = {
  schema_version: 1, job_name: 'projects/test/locations/test/jobs/wdpa-monthly', observed_at: '2026-10-01T12:00:00Z',
  latest_execution: {id: 'wdpa-new', state: 'running', started_at: '2026-10-01T11:59:00Z'},
  latest_completed_execution: {id: 'wdpa-old', state: 'failed', completed_at: '2026-10-01T11:58:00Z', reason_code: 'NON_ZERO_EXIT_CODE'},
};
test('a newer running execution does not conceal the failed completed execution', () => {
  assert.match(executionStatusText(observation, Date.parse('2026-10-01T12:01:00Z')), /running.*Last completed: failed/);
});
test('stale observation stays visible', () => {
  assert.match(executionStatusText(observation, Date.parse('2026-10-01T12:16:00Z')), /Stale observation/);
});
test('cancelled and successful execution states remain separate from publication', () => {
  for (const state of ['cancelled', 'succeeded']) {
    const document = {...observation, latest_execution: {...observation.latest_execution, state}};
    assert.match(executionStatusText(document), new RegExp(state));
  }
});
