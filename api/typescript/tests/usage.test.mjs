import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createUsageReporter} from '../dist/index.js';
import {createGcsUsageReporter} from '../dist/server.js';

test('daily application observations coalesce across cache returns and UTC rollover', async () => {
  let at = new Date('2026-10-14T23:59:59Z');
  const writes = [];
  const reporter = createUsageReporter({producer: 'example-production', now: () => at,
    writeReceipt: async (name, receipt) => writes.push({name, receipt})});
  reporter.recordUsage('example'); reporter.recordUsage('example');
  assert.equal(await reporter.flush(), true);
  reporter.recordUsage('example'); await reporter.flush();
  assert.equal(writes.length, 1);
  at = new Date('2026-10-15T00:00:00Z'); reporter.recordUsage('example');
  reporter.recordUsage('example', 'interest'); await reporter.flush();
  assert.equal(writes.length, 3);
  assert.match(writes[0].name, /^app-usage\/example-production\/v1\/2026-10-14\/[a-f0-9]{32}\.json$/);
  assert.deepEqual(Object.keys(writes[0].receipt).sort(), ['activity', 'asset_slug', 'observed_at', 'schema_version']);
});

test('telemetry failure is bounded and never fails application work', async () => {
  let attempts = 0;
  const diagnostics = [];
  const reporter = createUsageReporter({producer: 'example-production', diagnostic: code => diagnostics.push(code),
    writeReceipt: async () => { attempts++; throw new Error('denied'); }});
  reporter.recordUsage('example'); assert.equal(await reporter.flush(), false);
  assert.equal(attempts, 2); assert.deepEqual(diagnostics, ['Error']);
  assert.equal(await reporter.flush(), true); assert.equal(attempts, 2);
});

test('an unresponsive transport is aborted and cannot hold the application forever', async () => {
  let aborted = 0;
  const reporter = createUsageReporter({producer: 'example-production', diagnostic: () => {},
    writeReceipt: (_name, _receipt, signal) => new Promise(() => signal.addEventListener('abort', () => aborted++))});
  reporter.recordUsage('example');
  assert.equal(await reporter.flush(), false);
  assert.equal(aborted, 2);
});

test('GCS adapter is create-only and retries a lost acknowledgement idempotently', async () => {
  const calls = [];
  const storage = {bucket: name => ({file: path => ({save: async (body, options) => {
    calls.push({name, path, body, options}); throw {code: 412};
  }})})};
  const reporter = createGcsUsageReporter({producer: 'example-production', storage});
  reporter.recordUsage('example'); assert.equal(await reporter.flush(), true);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].options.preconditionOpts, {ifGenerationMatch: 0});
  assert.equal(calls[0].options.resumable, false);
  assert.equal(calls[0].options.timeout, 1000);
});

test('producer paths and activity names cannot be supplied as arbitrary identifiers', () => {
  assert.throws(() => createUsageReporter({producer: '../forged', writeReceipt: async () => {}}));
  const reporter = createUsageReporter({producer: 'example-production', writeReceipt: async () => {}});
  assert.throws(() => reporter.recordUsage('../example'));
  assert.throws(() => reporter.recordUsage('example', 'download'));
});
