import assert from 'node:assert/strict';
import { rmSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { checkConsumer, checkPackedFiles, createWorkDir, installConsumer, packPackage } from '../scripts/package-smoke.mjs';

test('packed file contract rejects missing declarations and accidentally shipped source', () => {
  const files = ['package.json', 'README.md', 'dist/index.js', 'dist/index.d.ts', 'dist/server.js', 'dist/server.d.ts', 'dist/maplibre.js', 'dist/maplibre.d.ts']
    .map(path => ({ path }));
  checkPackedFiles(files);
  assert.throws(() => checkPackedFiles(files.filter(file => file.path !== 'dist/server.d.ts')), /missing/);
  assert.throws(() => checkPackedFiles([...files, { path: 'src/server.ts' }]), /Unexpected/);
});

test('real packed consumer succeeds, then detects missing runtime and declarations', () => {
  const workDir = createWorkDir();
  try {
    const candidate = packPackage(workDir);
    const consumer = installConsumer(candidate, workDir);
    checkConsumer(consumer, candidate.version);
    const dist = join(consumer, 'node_modules/@skytruth/shared-datasets/dist');
    rmSync(join(dist, 'server.d.ts'));
    assert.throws(() => checkConsumer(consumer, candidate.version), /Command failed/);
    rmSync(join(dist, 'server.js'));
    assert.throws(() => checkConsumer(consumer, candidate.version), /Command failed/);
  } finally {
    // This test owns this freshly created directory, never prior task artifacts.
    rmSync(workDir, { recursive: true });
  }
});
