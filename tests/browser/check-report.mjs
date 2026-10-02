import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { workDir } from './paths.mjs';

const report = JSON.parse(await readFile(resolve(workDir, 'report.json'), 'utf8'));
assert.equal(report.stats.expected, 12, 'All twelve browser smoke scenarios must pass');
assert.equal(report.stats.skipped, 0, 'Browser smoke tests must not skip');
assert.equal(report.stats.unexpected, 0, 'Unexpected browser results');
assert.equal(report.stats.flaky, 0, 'Browser smoke tests must not retry into success');
