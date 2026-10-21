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

const visibilitySchema = 'shared-datasets-npm-visibility-v1';
const transientStatuses = new Set([500, 502, 503, 504]);
const transportFailures = new Map([
  ['ECONNRESET', 'connection_reset'], ['ECONNREFUSED', 'connection_refused'], ['EAI_AGAIN', 'dns_temporary'],
  ['ETIMEDOUT', 'request_timeout'], ['UND_ERR_CONNECT_TIMEOUT', 'request_timeout'],
  ['UND_ERR_HEADERS_TIMEOUT', 'request_timeout'], ['UND_ERR_BODY_TIMEOUT', 'request_timeout'],
  ['UND_ERR_SOCKET', 'connection_reset']
]);

export async function checkVisibility(candidate, fetcher = fetch, timeoutMs = 30000) {
  parseVersion(candidate.version);
  if (candidate.name !== PACKAGE_NAME || !/^sha512-[A-Za-z0-9+/]{86}==$/.test(candidate.integrity)) {
    throw new Error('Visibility check requires the exact tested package identity and integrity');
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000) {
    throw new Error('Visibility request timeout must be between 1 and 60000 milliseconds');
  }
  const result = (status, reason) => ({ schema: visibilitySchema, name: candidate.name,
    version: candidate.version, integrity: candidate.integrity, status, reason });
  const url = `https://registry.npmjs.org/${PACKAGE_NAME.replace('/', '%2F')}/${candidate.version}`;
  try {
    // Exact version metadata cannot authorize another publish. Bypass stale
    // caches, reject redirects, and never attach publishing credentials.
    const response = await fetcher(url, { method: 'GET', redirect: 'error',
      headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' }, signal: AbortSignal.timeout(timeoutMs) });
    if (response.status === 404 || transientStatuses.has(response.status)) {
      if (response.body) await response.body.cancel();
      return result(response.status === 404 ? 'absent' : 'transient', `HTTP ${response.status}`);
    }
    if (response.status !== 200) throw new Error(`npm visibility request rejected: HTTP ${response.status}`);
    if (!response.body) throw new Error('npm visibility response has no metadata');
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > 1024 * 1024) throw new Error('npm visibility metadata exceeds the read limit');
      chunks.push(chunk);
    }
    const metadata = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
    if (metadata?.name !== candidate.name || metadata?.version !== candidate.version) {
      throw new Error('npm visibility metadata differs from the exact tested package');
    }
    if (metadata?.dist?.integrity !== candidate.integrity) {
      throw new Error('Published version has different or missing integrity; inspect the existing publication');
    }
    return result('visible', 'verified_integrity');
  } catch (error) {
    const reason = transportFailures.get(error?.cause?.code ?? error?.code) ??
      (error?.name === 'TimeoutError' ? 'request_timeout' : undefined);
    if (reason) return result('transient', reason);
    throw error;
  }
}

function localCandidate(path) {
  const pkg = readJson('package.json');
  validatePackage(pkg, readJson('package-lock.json'));
  const candidate = readJson(path);
  if (candidate.name !== pkg.name || candidate.version !== pkg.version) {
    throw new Error('Packed candidate must match checked-out SDK name and version');
  }
  const integrity = `sha512-${createHash('sha512').update(readFileSync(candidate.tarball)).digest('base64')}`;
  if (integrity !== candidate.integrity) throw new Error('Packed candidate bytes changed after validation');
  return candidate;
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
    writeOutput('should_publish', checkRegistry(localCandidate(args[0])));
  } else if (mode === 'visibility' && args.length === 2) {
    console.log(JSON.stringify(await checkVisibility(localCandidate(args[0]), fetch, Number(args[1]))));
  } else {
    throw new Error('Usage: release-policy.mjs changes BASE HEAD | registry CANDIDATE_JSON | visibility CANDIDATE_JSON TIMEOUT_MS');
  }
}
