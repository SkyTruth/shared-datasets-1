import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PACKAGE_NAME, checkChanges, checkRegistry, compareVersions, needsRelease,
  parseVersion, publicationDecision, validatePackage
} from '../scripts/release-policy.mjs';

const pkg = version => ({ name: PACKAGE_NAME, version });
const lock = version => ({ ...pkg(version), packages: { '': pkg(version) } });
const integrity = `sha512-${Buffer.alloc(64, 1).toString('base64')}`;
const otherIntegrity = `sha512-${Buffer.alloc(64, 2).toString('base64')}`;

test('stable versions compare numerically and reject malformed or unsupported versions', () => {
  assert.equal(compareVersions('0.10.0', '0.9.0'), 1);
  assert.equal(compareVersions('1.0.0', '0.99.999'), 1);
  assert.equal(compareVersions('0.9.0', '0.9.0'), 0);
  for (const version of [null, 1, '', 'v0.9.0', '0.9', '00.9.0', '0.9.0-beta', '0.9.0+build', '9007199254740992.0.0']) {
    assert.throws(() => parseVersion(version));
  }
});

test('package and both lockfile versions must agree', () => {
  validatePackage(pkg('0.9.0'), lock('0.9.0'));
  for (const badLock of [{}, lock('0.8.0'), { ...lock('0.9.0'), packages: {} },
    { ...lock('0.9.0'), name: 'another-package' },
    { ...lock('0.9.0'), packages: { '': pkg('0.8.0') } }]) {
    assert.throws(() => validatePackage(pkg('0.9.0'), badLock));
  }
  assert.throws(() => validatePackage({ ...pkg('0.9.0'), name: 'other' }, lock('0.9.0')));
});

test('every release-content path needs a reviewed version increase', () => {
  for (const file of ['src/catalog.ts', 'README.md', 'package.json', 'package-lock.json', 'tsconfig.json', 'scripts/copy-snapshot-contract.mjs']) {
    const changed = [`api/typescript/${file}`];
    assert.throws(() => needsRelease(pkg('0.8.0'), pkg('0.8.0'), changed), /increase/);
    assert.equal(needsRelease(pkg('0.8.0'), pkg('0.9.0'), changed), true);
  }
  assert.throws(() => needsRelease(pkg('0.9.0'), pkg('0.9.0'), ['web/catalog/workspace-contract.js']), /increase/);
  assert.throws(() => needsRelease(pkg('0.9.0'), pkg('0.8.0'), []), /decrease/);
});

test('unpacked tooling and unrelated files do not invent a release', () => {
  assert.equal(needsRelease(pkg('0.9.0'), pkg('0.9.0'), [
    'README.md', 'api/typescript/tests/example.test.mjs',
    'api/typescript/scripts/release-policy.mjs', '.github/workflows/publish-typescript-sdk.yml'
  ]), false);
});

test('change boundary reads exact base and head manifests and propagates Git errors', () => {
  const calls = [];
  const run = (command, args) => {
    calls.push([command, args]);
    if (args[0] === 'diff') return 'api/typescript/src/catalog.ts\0';
    const version = args[1].startsWith('base:') ? '0.8.0' : '0.9.0';
    return JSON.stringify(args[1].endsWith('package-lock.json') ? lock(version) : pkg(version));
  };
  assert.equal(checkChanges('base', 'head', run), true);
  assert.deepEqual(calls.at(-1), ['git', ['diff', '--name-only', '-z', 'base', 'head', '--']]);
  assert.throws(() => checkChanges('base', 'head', () => { throw new Error('Git failed'); }), /Git failed/);
  assert.throws(() => checkChanges('base', 'head', () => 'not JSON'));
  for (const base of ['', '0000000000000000000000000000000000000000', '--output=bad']) {
    assert.throws(() => checkChanges(base, 'head', run), /revisions/);
  }
});

test('publisher accepts a higher version and only identical retries', () => {
  assert.equal(publicationDecision('0.9.0', ['0.7.0', '0.8.0'], integrity), true);
  assert.equal(publicationDecision('0.9.0', ['0.8.0', '0.9.0'], integrity, integrity), false);
  assert.equal(publicationDecision('0.8.0', ['0.8.0', '0.9.0'], integrity, integrity), false);
  assert.throws(() => publicationDecision('0.9.0', ['0.9.0'], integrity, otherIntegrity), /different/);
  assert.throws(() => publicationDecision('0.9.0', ['0.9.0'], integrity), /missing/);
  assert.throws(() => publicationDecision('0.9.0', ['0.10.0'], integrity), /exceed/);
});

test('malformed registry metadata and candidate integrity fail closed', () => {
  for (const versions of [null, {}, '0.8.0', [], ['0.8.0', '0.8.0'], ['0.8.0', null], ['0.8.0-beta']]) {
    assert.throws(() => publicationDecision('0.9.0', versions, integrity));
  }
  for (const invalid of [undefined, '', 'sha512-notbase64', 123]) {
    assert.throws(() => publicationDecision('0.9.0', ['0.8.0'], invalid));
  }
});

test('registry boundary only reads metadata; outages are never missing releases', () => {
  const candidate = { version: '0.9.0', integrity };
  const calls = [];
  assert.equal(checkRegistry(candidate, (command, args) => {
    calls.push([command, args]);
    return JSON.stringify(args[2] === 'versions' ? ['0.8.0', '0.9.0'] : integrity);
  }), false);
  assert.equal(calls.length, 2);
  for (const [command, args] of calls) {
    assert.equal(command, 'npm');
    assert.equal(args[0], 'view');
    assert(args.includes('--registry=https://registry.npmjs.org'));
  }
  assert.deepEqual(calls[1][1].slice(0, 3), ['view', `${PACKAGE_NAME}@0.9.0`, 'dist.integrity']);
  for (const reason of ['E404', 'E401', 'ECONNRESET']) {
    assert.throws(() => checkRegistry(candidate, () => { throw new Error(reason); }), new RegExp(reason));
  }
  assert.throws(() => checkRegistry(candidate, () => 'not JSON'));
  assert.throws(() => checkRegistry(candidate, () => JSON.stringify({ error: 'denied' })));
});

test('registry CLI refreshes cached metadata after publication and preserves integrity rejection', async () => {
  const { execFileSync } = await import('node:child_process');
  const { createHash } = await import('node:crypto');
  const { mkdtempSync, writeFileSync, rmSync, readFileSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join, resolve } = await import('node:path');
  const root = mkdtempSync(join(tmpdir(), 'sdk-registry-cache-'));
  try {
    const bytes = Buffer.from('exact tested package');
    const candidate = { ...pkg('0.9.0'), tarball: join(root, 'sdk.tgz'),
      integrity: `sha512-${createHash('sha512').update(bytes).digest('base64')}` };
    writeFileSync(candidate.tarball, bytes);
    writeFileSync(join(root, 'candidate.json'), JSON.stringify(candidate));
    writeFileSync(join(root, 'package.json'), JSON.stringify(pkg('0.9.0')));
    writeFileSync(join(root, 'package-lock.json'), JSON.stringify(lock('0.9.0')));
    // Replace only the external npm transport: a fresh cache retains the
    // pre-publish version list until the caller requests an online check.
    writeFileSync(join(root, 'npm'), `#!/usr/bin/env node
const args = process.argv.slice(2);
if (process.env.REGISTRY_ERROR) throw new Error(process.env.REGISTRY_ERROR);
console.log(JSON.stringify(args[2] === 'versions'
  ? (args.includes('--prefer-online') ? ['0.8.0', '0.9.0'] : ['0.8.0'])
  : process.env.REGISTRY_INTEGRITY));
`, { mode: 0o755 });
    const output = join(root, 'outputs');
    const env = { ...process.env, PATH: `${root}:${process.env.PATH}`, GITHUB_OUTPUT: output,
      REGISTRY_INTEGRITY: candidate.integrity };
    const command = [resolve('scripts/release-policy.mjs'), 'registry', join(root, 'candidate.json')];
    assert.equal(execFileSync(process.execPath, command, { cwd: root, env, encoding: 'utf8' }).trim(), 'should_publish=false');
    assert.equal(readFileSync(output, 'utf8'), 'should_publish=false\n');
    assert.throws(() => execFileSync(process.execPath, command, { cwd: root,
      env: { ...env, REGISTRY_INTEGRITY: otherIntegrity }, stdio: 'pipe' }), /different or missing integrity/);
    assert.throws(() => execFileSync(process.execPath, command, { cwd: root,
      env: { ...env, REGISTRY_ERROR: 'ECONNRESET' }, stdio: 'pipe' }), /ECONNRESET/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
