import assert from 'node:assert/strict';
import {test} from 'node:test';
import config from './playwright.config.mjs';

test('CI Git metadata capture cannot truncate the validation checkout', () => {
  assert.deepEqual(config.captureGitInfo, {commit: false, diff: false});
});

// One fixture build/server, per-test browser contexts and per-owner comparison jobs.
test('parallel browser validation retains zero retries and a single static server', () => {
  assert.equal(config.fullyParallel, true);
  assert.equal(config.workers, 2);
  assert.equal(config.retries, 0);
  assert.equal(config.webServer.reuseExistingServer, false);
});
