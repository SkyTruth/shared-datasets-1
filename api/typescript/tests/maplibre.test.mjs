import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {resolveDatasetMap} from '../dist/maplibre.js';
import {authorizeSnapshotArtifact} from '../dist/server.js';
const corpus = JSON.parse(await readFile(new URL('../../../tests/fixtures/workspace-snapshot-v1.json', import.meta.url), 'utf8'));
const dataset = corpus.snapshot.datasets[0];
const tiles = dataset.artifacts.find(a => a.format === 'pmtiles');
const metadata = dataset.artifacts.find(a => a.format === 'metadata');
const reference = {tiles:`${tiles.gs_uri}#${tiles.generation}`,metadata:`${metadata.gs_uri}#${metadata.generation}`};

test('compact map references load exactly matching metadata without catalog lookups', async t => {
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push([url, options.method]);
    if (options.method === 'HEAD') return new Response(null, {headers:{'x-goog-generation':url.includes('pmtiles') ? tiles.generation : metadata.generation}});
    return new Response(Buffer.from(corpus.metadata_base64,'base64'));
  });
  const result = await resolveDatasetMap(reference, {bucket:'example-bucket'});
  assert.equal(result.records.get('1').properties.name,'Quoted "name"\n雪');
  assert.equal(result.layer.tileUrl.endsWith('?generation=101'),true);
  assert.equal(result.layer.metadata.url.endsWith('?generation=102'),true);
  assert.equal(requests.length,3);
  assert(requests.every(([url]) => url.includes('/2026-01-01/') && url.includes('generation=')));
});

test('compact map rejects wrong roots, mismatched releases, secrets and missing generations before network access', async t => {
  const fetch = t.mock.method(globalThis,'fetch', async () => {throw new Error('Unexpected network access');});
  for (const invalid of [
    {...reference,metadata:reference.metadata.replace('/2026-01-01/','/2026-01-02/')},
    {...reference,metadata:reference.metadata.replace('/category/','/other-category/')},
    {...reference,tiles:reference.tiles.replace('#101','')},
    {...reference,tiles:reference.tiles.replace('#101','?token=secret#101')},
    {...reference,tiles:reference.tiles.replace('example-bucket','evil-bucket')},
  ]) await assert.rejects(resolveDatasetMap(invalid,{bucket:'example-bucket'}));
  assert.equal(fetch.mock.callCount(),0);
});

test('compact map fails closed for unavailable or incorrectly authorized generations', async t => {
  t.mock.method(globalThis,'fetch',async () => new Response(null,{status:404}));
  await assert.rejects(resolveDatasetMap(reference,{bucket:'example-bucket'}),/captured bytes unavailable/);
  await assert.rejects(resolveDatasetMap(reference,{bucket:'example-bucket',access:'internal'}),/app-owned/);
  await assert.rejects(resolveDatasetMap(reference,{bucket:'example-bucket',access:'internal',authorizeArtifact:async () => ({gs_uri:tiles.gs_uri,generation:'999',resolved_release:dataset.release,url:'https://app.test/data'})}),/captured identity/);
});

test('compact restricted route rechecks current entitlement and exact indexed files', async t => {
  let authorized=0;
  const files=dataset.artifacts.map(a=>({format:a.format,path:a.gs_uri,generation:a.generation}));
  const authorization={bucket:'example-bucket',viewer:{email:'viewer@skytruth.org',emailVerified:true},
    getAsset:async()=>({access_tier:'internal',canonical_path:dataset.artifacts[0].gs_uri.replace('/releases/2026-01-01/','/latest/')}),
    getReleaseIndex:async()=>({schema_version:1,asset_slug:dataset.asset_slug,latest_release:{date:dataset.release},releases:[{date:dataset.release,files}]}),
    getSigningKey:async()=>Buffer.alloc(16,1)};
  t.mock.method(globalThis,'fetch',async (url,options)=>{
    if (url === '/api/snapshot-artifact') {
      const body=JSON.parse(options.body);
      assert.equal(options.credentials,'include');
      const granted=await authorizeSnapshotArtifact(body.snapshot,body.asset_slug,body.role,body.locale,authorization);
      authorized++;
      return Response.json(granted);
    }
    if (options.method==='HEAD') return new Response(null);
    return new Response(Buffer.from(corpus.metadata_base64,'base64'));
  });
  const result=await resolveDatasetMap(reference,{bucket:'example-bucket',access:'internal',authorizationUrl:'/api/snapshot-artifact'});
  assert.equal(authorized,2);assert.equal(result.records.size,1);
  files.find(f=>f.format==='pmtiles').generation='999';
  await assert.rejects(resolveDatasetMap(reference,{bucket:'example-bucket',access:'internal',authorizationUrl:'/api/snapshot-artifact'}),/older generation/);
  assert.equal(authorized,2);
});
