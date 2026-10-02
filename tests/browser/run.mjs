import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { packageDir } from './paths.mjs';

const unit = spawnSync(process.execPath, ['--test', resolve(packageDir, 'execution-status.test.mjs')], { stdio: 'inherit' });
if (unit.error) throw unit.error;
if (unit.status !== 0) process.exit(unit.status || 1);

const result = spawnSync(process.execPath, [resolve(packageDir, 'node_modules/playwright/cli.js'), 'test', ...process.argv.slice(2)], { stdio: 'inherit' });
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status || 1);
await import('./check-report.mjs');
