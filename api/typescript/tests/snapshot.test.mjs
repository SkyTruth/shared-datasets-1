import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import ts from 'typescript';
import {validateWorkspaceSnapshot, resolveSnapshotLayer, fetchSnapshotMetadata} from '../dist/index.js';
import {authorizeSnapshotArtifact} from '../dist/server.js';
import {captureWorkspace, pythonSnippet, typescriptSnippet} from '../../../web/catalog/workspace.js';
import {selectReleaseReference} from '../../../web/catalog/release-reference.js';
const corpus = JSON.parse(await readFile(new URL('../../../tests/fixtures/workspace-snapshot-v1.json', import.meta.url), 'utf8'));
const clone = value => JSON.parse(JSON.stringify(value));
function assetFor(snapshot) {
  const dataset = snapshot.datasets[0];
  const root = dataset.artifacts[0].gs_uri.split('/releases/')[0];
  const files = dataset.artifacts.map(a => ({path: a.gs_uri, generation: a.generation, format: a.format, role: a.format, size: a.size, sha256: a.sha256, ...(a.resolved_locale ? {locale: a.resolved_locale} : {})}));
  return {...dataset.provenance, slug: dataset.asset_slug, canonical_format: dataset.canonical_format, canonical_path: `${root}/latest/${dataset.asset_slug}.csv`, access_tier: dataset.access_tier,
    has_pmtiles: true, pmtiles_path: `${root}/latest/${dataset.asset_slug}.pmtiles`, latest_release: {date: dataset.release}, versions: [{date: dataset.release, files}]};
}
for (const entry of corpus.invalid_mutations) test(`shared snapshot rejection: ${entry.name}`, () => {
  const input = clone(corpus.snapshot); let parent = input;
  for (const key of entry.path.slice(0, -1)) parent = parent[key];
  parent[entry.path.at(-1)] = entry.value;
  assert.throws(() => validateWorkspaceSnapshot(JSON.stringify(input), {bucket: 'example-bucket'}));
});
test('portable validation rejects duplicate keys and preserves large generation strings', () => {
  assert.deepEqual(validateWorkspaceSnapshot(corpus.snapshot, {bucket: 'example-bucket'}), corpus.snapshot);
  assert.throws(() => validateWorkspaceSnapshot(JSON.stringify(corpus.snapshot).replace('"schema_version":1', '"schema_version":1,"schema_version":1'), {bucket: 'example-bucket'}), /duplicate/);
  assert.throws(() => validateWorkspaceSnapshot(' '.repeat(1024 * 1024 + 1), {bucket: 'example-bucket'}));
});
test('capture resolves latest once; changed latest and same-date replacement never alter a public restore', async () => {
  const asset = assetFor(corpus.snapshot);
  const reference = selectReleaseReference(asset, 'latest', {bucket: 'example-bucket'});
  const snapshot = captureWorkspace([reference], {bucket: 'example-bucket', presentation: corpus.snapshot.presentation});
  const requests = [];
  asset.latest_release = {date: '2026-10-01'}; asset.versions = []; asset.citation = 'Different current citation';
  const layer = await resolveSnapshotLayer(snapshot, asset.slug, {bucket: 'example-bucket', probeArtifact: async url => {requests.push(url);}});
  assert.equal(layer.dataset.release, '2026-01-01');
  assert.deepEqual(layer.dataset.provenance, snapshot.datasets[0].provenance);
  assert.equal(layer.dataset.artifacts.find(a => a.role === 'canonical').generation, '9007199254740993');
  assert(requests.every(url => url.includes('/2026-01-01/') && url.includes('generation=')));
  assert.deepEqual(validateWorkspaceSnapshot(snapshot, {bucket: 'example-bucket'}).presentation, corpus.snapshot.presentation);
  assert(!JSON.stringify(snapshot).includes('Signature='));
});
test('exact TypeScript tiles and metadata are coherent and verify byte integrity', async () => {
  const probes = [];
  const layer = await resolveSnapshotLayer(corpus.snapshot, 'example-layer', {bucket: 'example-bucket', probeArtifact: async (url, artifact) => probes.push([url, artifact.generation])});
  assert.equal(probes.length, 3);
  assert(layer.tileUrl.endsWith('?generation=101'));
  assert(layer.metadata.url.endsWith('?generation=102'));
  const bytes = Buffer.from(corpus.metadata_base64, 'base64');
  const records = await fetchSnapshotMetadata(layer, {fetchBytes: async () => Uint8Array.from(bytes).buffer});
  assert.equal(records.get('1').properties.name, 'Quoted "name"\n雪');
  await assert.rejects(fetchSnapshotMetadata(layer, {fetchBytes: async () => new Uint8Array(2).buffer}), /size/);
  const corrupt = Uint8Array.from(bytes); corrupt[corrupt.length - 1] ^= 1;
  await assert.rejects(fetchSnapshotMetadata(layer, {fetchBytes: async () => corrupt.buffer}), /SHA-256/);
});
test('localized and canonical metadata preserve requested and resolved locale on round-trip', async () => {
  const snapshot = clone(corpus.snapshot);
  snapshot.presentation.locale = 'es';
  const local = clone(snapshot.datasets[0].artifacts[2]); local.gs_uri = local.gs_uri.replace('.metadata.', '.metadata.es.'); local.generation = '103'; local.requested_locale = local.resolved_locale = 'es';
  snapshot.datasets[0].artifacts.push(local);
  const asset = assetFor(snapshot), reference = selectReleaseReference(asset, 'latest', {bucket: 'example-bucket'});
  const captured = captureWorkspace([reference], {bucket: 'example-bucket', locale: 'es', presentation: snapshot.presentation});
  const layer = await resolveSnapshotLayer(captured, 'example-layer', {bucket: 'example-bucket', probeArtifact: async () => {}});
  assert.equal(layer.metadata.artifact.resolved_locale, 'es');
  const lock = {...captured, presentation: null};
  assert.equal((await resolveSnapshotLayer(lock, 'example-layer', {bucket: 'example-bucket', probeArtifact: async () => {}})).metadata.artifact.resolved_locale, 'es');
  const fallback = captureWorkspace([selectReleaseReference(assetFor(corpus.snapshot), 'latest', {bucket: 'example-bucket'})], {bucket: 'example-bucket', locale: 'fr'});
  assert.equal(fallback.datasets[0].artifacts.find(a => a.role === 'metadata').requested_locale, 'fr');
  assert.equal(fallback.datasets[0].artifacts.find(a => a.role === 'metadata').resolved_locale, null);
});
test('unavailable generations and malformed signing paths fail before mounting without substitution', async t => {
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async () => {requests++; return new Response('', {status: 404});});
  await assert.rejects(resolveSnapshotLayer({...corpus.snapshot, unexpected: true}, 'example-layer', {bucket: 'example-bucket'}));
  assert.equal(requests, 0);
  await assert.rejects(resolveSnapshotLayer(corpus.snapshot, 'example-layer', {bucket: 'example-bucket'}), /9007199254740993.*captured bytes unavailable/);
  assert.equal(requests, 1);
  assert.throws(() => captureWorkspace([{...assetFor(corpus.snapshot), date: null, files: [], release_snapshot_key: null}], {bucket: 'example-bucket'}), /not reproducibly pinned/);
});
test('restricted restore reacquires app authorization and never exports secrets', async () => {
  const snapshot = clone(corpus.snapshot); snapshot.datasets[0].access_tier = 'internal';
  let authorized = 0;
  await assert.rejects(resolveSnapshotLayer(snapshot, 'example-layer', {bucket: 'example-bucket', probeArtifact: async () => {}}), /app-owned/);
  const layer = await resolveSnapshotLayer(snapshot, 'example-layer', {bucket: 'example-bucket', probeArtifact: async () => {}, authorizeArtifact: async (d, a) => {
    authorized++;
    return {gs_uri: a.gs_uri, generation: a.generation, resolved_release: d.release, url: 'https://app.test/authorized?Signature=ephemeral'};
  }});
  assert.equal(authorized, 3); assert(layer.tileUrl.includes('ephemeral'));
  assert(!JSON.stringify(snapshot).includes('ephemeral'));
  await assert.rejects(resolveSnapshotLayer(snapshot, 'example-layer', {bucket: 'example-bucket', authorizeArtifact: async () => ({gs_uri: 'wrong', url: 'https://app.test'})}), /captured identity/);
});
test('server signing checks current entitlement and indexed generation, not imported tier', async () => {
  const snapshot = clone(corpus.snapshot), asset = assetFor(snapshot);
  const files = asset.versions[0].files;
  let signKeyCalls = 0;
  const options = {bucket: 'example-bucket', viewer: null,
    getAsset: async () => ({canonical_path: asset.canonical_path, access_tier: 'internal'}),
    getReleaseIndex: async () => ({schema_version: 1, asset_slug: 'example-layer', latest_release: {date: '2026-01-01'}, releases: [{date: '2026-01-01', files}]}),
    getSigningKey: async () => {signKeyCalls++; return Buffer.alloc(16, 1);}};
  await assert.rejects(authorizeSnapshotArtifact(snapshot, 'example-layer', 'canonical', null, options), /not authorized/);
  options.viewer = {email: 'viewer@skytruth.org', emailVerified: true};
  const authorized = await authorizeSnapshotArtifact(snapshot, 'example-layer', 'canonical', null, options);
  assert(authorized.url.includes('generation=9007199254740993'));
  assert.equal(signKeyCalls, 1);
  files[0].generation = '9007199254740994';
  await assert.rejects(authorizeSnapshotArtifact(snapshot, 'example-layer', 'canonical', null, options), /older generation/);
  assert.equal(signKeyCalls, 1);
});
test('generated TypeScript has valid syntax with quotes, newlines, unicode, and restricted code', () => {
  for (const tier of ['public', 'internal']) {
    const snapshot = clone(corpus.snapshot); snapshot.datasets[0].access_tier = tier;
    const code = typescriptSnippet(snapshot);
    const compiled = ts.transpileModule(code, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}, reportDiagnostics: true});
    assert.deepEqual(compiled.diagnostics, []);
    assert(code.includes('showDataset'));
    assert.equal(code.split('\n').length, 5);
    assert(code.includes('#101'));
    assert(code.includes('#102'));
    assert(!code.includes('provenance'));
    assert.equal(pythonSnippet(snapshot).split('\n').length, 4);
    assert(!code.includes('@skytruth/shared-datasets/server";'));
  }
});
