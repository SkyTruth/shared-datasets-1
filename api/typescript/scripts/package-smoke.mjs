import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { appendFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const packageName = '@skytruth/shared-datasets';
const run = (command, args, cwd) => {
  const result = spawnSync(command, args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Command failed: ${command} ${args.join(' ')}\n${result.stdout}${result.stderr}`);
  return result.stdout;
};

export function createWorkDir() {
  const root = join(process.env.SHARED_DATASETS_WORKDIR || join(tmpdir(), 'shared-datasets-1'), '_scratch');
  mkdirSync(root, { recursive: true });
  return mkdtempSync(join(root, 'sdk-package-smoke-'));
}

export function checkPackedFiles(files) {
  const paths = new Set(files.map(file => file.path));
  for (const required of ['package.json', 'README.md', 'dist/index.js', 'dist/index.d.ts', 'dist/server.js', 'dist/server.d.ts']) {
    assert(paths.has(required), `Packed package is missing ${required}`);
  }
  for (const path of paths) {
    assert(path === 'package.json' || path === 'README.md' || /^dist\/.*\.(js|d\.ts|d\.ts\.map)$/.test(path),
      `Unexpected packed file: ${path}`);
  }
}

export function packPackage(workDir) {
  const [packed] = JSON.parse(run('npm', ['pack', '--ignore-scripts', '--json', '--pack-destination', workDir, '--cache', join(workDir, 'npm-cache')], packageDir));
  checkPackedFiles(packed.files);
  assert.equal(packed.name, packageName);
  return { name: packed.name, version: packed.version, integrity: packed.integrity, tarball: join(workDir, packed.filename) };
}

export function installConsumer(candidate, workDir) {
  const consumer = join(workDir, 'consumer');
  mkdirSync(consumer);
  writeFileSync(join(consumer, 'package.json'), JSON.stringify({ private: true, type: 'module' }));
  run('npm', ['install', candidate.tarball, '--offline', '--ignore-scripts', '--no-audit', '--no-fund',
    '--package-lock=false', '--cache', join(workDir, 'npm-cache')], consumer);
  return consumer;
}

export function checkConsumer(consumer, expectedVersion) {
  writeFileSync(join(consumer, 'runtime.mjs'), `
import assert from 'node:assert/strict';
import { realpathSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { getPmtilesFetchCredentials, validateWorkspaceSnapshot, resolveSnapshotLayer } from '${packageName}';
import { getExpiredPmtilesCookies, authorizeSnapshotArtifact } from '${packageName}/server';
const root = realpathSync('./node_modules/${packageName}') + '/';
for (const name of ['${packageName}', '${packageName}/server']) {
  assert(realpathSync(fileURLToPath(import.meta.resolve(name))).startsWith(root));
}
assert.equal(JSON.parse(readFileSync(root + 'package.json')).version, ${JSON.stringify(expectedVersion)});
assert.equal(getPmtilesFetchCredentials('https://tiles.skytruth.org/pmtiles/private/example.pmtiles'), 'include');
assert.equal(getExpiredPmtilesCookies().length, 2);
assert.equal(typeof resolveSnapshotLayer, "function");
assert.equal(typeof authorizeSnapshotArtifact, "function");
assert.throws(() => validateWorkspaceSnapshot({schema_version: 999}));
`);
  run(process.execPath, ['runtime.mjs'], consumer);
  writeFileSync(join(consumer, 'types.mts'), `
import { getPmtilesFetchCredentials, type SharedDatasetAccessTier } from '${packageName}';
import { getExpiredPmtilesCookies, type PmtilesCdnSessionConfigInput } from '${packageName}/server';
const tier: SharedDatasetAccessTier = 'public';
const config: PmtilesCdnSessionConfigInput = { ttlSeconds: 60 };
const credentials: RequestCredentials = getPmtilesFetchCredentials('https://example.org/' + tier);
const cookies: string[] = getExpiredPmtilesCookies(config);
void credentials; void cookies;
`);
  run(process.execPath, [join(packageDir, 'node_modules/typescript/bin/tsc'), '--noEmit', '--strict',
    '--module', 'NodeNext', '--moduleResolution', 'NodeNext', '--target', 'ES2022', '--lib', 'ES2022,DOM',
    '--types', 'node', '--typeRoots', join(packageDir, 'node_modules/@types'), 'types.mts'], consumer);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const workDir = createWorkDir();
  console.log(`Packed SDK smoke artifacts: ${workDir}`);
  run('npm', ['run', 'build'], packageDir);
  const candidate = packPackage(workDir);
  checkConsumer(installConsumer(candidate, workDir), candidate.version);
  const manifest = join(workDir, 'candidate.json');
  writeFileSync(manifest, `${JSON.stringify(candidate, null, 2)}\n`);
  if (process.env.GITHUB_OUTPUT) {
    appendFileSync(process.env.GITHUB_OUTPUT, `tarball=${candidate.tarball}\ncandidate=${manifest}\n`);
  }
  console.log(`Packed root/server runtime and declaration imports passed: ${manifest}`);
}
