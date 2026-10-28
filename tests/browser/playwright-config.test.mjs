import assert from 'node:assert/strict';
import {test} from 'node:test';
import config from './playwright.config.mjs';

test('CI Git metadata capture cannot truncate the validation checkout', () => {
  assert.deepEqual(config.captureGitInfo, {commit: false, diff: false});
});
