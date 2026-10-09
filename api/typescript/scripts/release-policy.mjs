import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { appendFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const PACKAGE_NAME = '@skytruth/shared-datasets';
const packagePrefix = 'api/typescript/';
const packageFiles = new Set(['README.md', 'package.json', 'package-lock.json', 'tsconfig.json', 'scripts/copy-snapshot-contract.mjs']);

export function parseVersion(version) {
  if (typeof version !== 'string' || !/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)) {
    throw new Error(`Expected stable major.minor.patch version, received ${JSON.stringify(version)}`);
  }
  const parts = version.split('.').map(Number);
  if (!parts.every(Number.isSafeInteger)) throw new Error('Version component exceeds safe integer range');
  return parts;
}

export function compareVersions(left, right) {
  const a = parseVersion(left);
  const b = parseVersion(right);
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return Math.sign(a[i] - b[i]);
  }
  return 0;
}

export function validatePackage(pkg, lock) {
  parseVersion(pkg.version);
  if (pkg.name !== PACKAGE_NAME || lock.name !== pkg.name || lock.packages?.['']?.name !== pkg.name ||
      lock.version !== pkg.version || lock.packages[''].version !== pkg.version) {
    throw new Error('SDK package and lockfile names/versions must agree');
  }
}

export function needsRelease(before, after, changedFiles) {
  const comparison = compareVersions(after.version, before.version);
  if (comparison < 0) throw new Error('SDK version must not decrease');
  const contentChanged = changedFiles.some(file => file === 'web/catalog/workspace-contract.js' || file.startsWith(`${packagePrefix}src/`) ||
    (file.startsWith(packagePrefix) && packageFiles.has(file.slice(packagePrefix.length))));
  if (contentChanged && comparison === 0) {
    throw new Error('SDK package content changed: increase package.json and package-lock.json versions in this PR');
  }
  return comparison > 0;
}

export function publicationDecision(version, versions, candidateIntegrity, publishedIntegrity) {
  parseVersion(version);
  if (!Array.isArray(versions) || versions.length === 0 || new Set(versions).size !== versions.length) {
    throw new Error('Registry must return a nonempty unique published-version list');
  }
  versions.forEach(parseVersion);
  if (typeof candidateIntegrity !== 'string' || !/^sha512-[A-Za-z0-9+/]{86}==$/.test(candidateIntegrity)) {
    throw new Error('Packed artifact must have a SHA-512 integrity value');
  }
  if (versions.includes(version)) {
    if (publishedIntegrity !== candidateIntegrity) {
      throw new Error(`Version ${version} already exists with different or missing integrity; prepare a new reviewed version`);
    }
    return false;
  }
  if (versions.some(published => compareVersions(version, published) <= 0)) {
    throw new Error(`New version ${version} must exceed every published stable version`);
  }
  return true;
}

const commandText = (command, args) => execFileSync(command, args, {
  encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']
}).trim();
const readJson = path => JSON.parse(readFileSync(path, 'utf8'));

export function checkChanges(base, head, run = commandText) {
  if (!base || !head || /^0+$/.test(base) || base.startsWith('-') || head.startsWith('-')) {
    throw new Error('Explicit existing base and head revisions are required');
  }
  const manifests = [base, head].map(ref => {
    const pkg = JSON.parse(run('git', ['show', `${ref}:${packagePrefix}package.json`]));
    const lock = JSON.parse(run('git', ['show', `${ref}:${packagePrefix}package-lock.json`]));
    validatePackage(pkg, lock);
    return pkg;
  });
  const changedFiles = run('git', ['diff', '--name-only', '-z', base, head, '--']).split('\0').filter(Boolean);
  return needsRelease(...manifests, changedFiles);
}

export function checkRegistry(candidate, run = commandText) {
  const registry = '--registry=https://registry.npmjs.org';
  const versions = JSON.parse(run('npm', ['view', PACKAGE_NAME, 'versions', '--json', registry, '--prefer-online']));
  parseVersion(candidate.version);
  const publishedIntegrity = Array.isArray(versions) && versions.includes(candidate.version)
    ? JSON.parse(run('npm', ['view', `${PACKAGE_NAME}@${candidate.version}`, 'dist.integrity', '--json', registry, '--prefer-online']))
    : undefined;
  return publicationDecision(candidate.version, versions, candidate.integrity, publishedIntegrity);
}

function writeOutput(name, value) {
  console.log(`${name}=${value}`);
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `${name}=${value}\n`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [mode, ...args] = process.argv.slice(2);
  if (mode === 'changes' && args.length === 2) {
    writeOutput('release_needed', checkChanges(...args));
  } else if (mode === 'registry' && args.length === 1) {
    const pkg = readJson('package.json');
    validatePackage(pkg, readJson('package-lock.json'));
    const candidate = readJson(args[0]);
    if (candidate.name !== pkg.name || candidate.version !== pkg.version) {
      throw new Error('Packed candidate must match checked-out SDK name and version');
    }
    const integrity = `sha512-${createHash('sha512').update(readFileSync(candidate.tarball)).digest('base64')}`;
    if (integrity !== candidate.integrity) throw new Error('Packed candidate bytes changed after validation');
    writeOutput('should_publish', checkRegistry(candidate));
  } else {
    throw new Error('Usage: release-policy.mjs changes BASE HEAD | registry CANDIDATE_JSON');
  }
}
