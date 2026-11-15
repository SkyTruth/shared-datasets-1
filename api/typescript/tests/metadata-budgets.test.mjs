import assert from 'node:assert/strict';
import {gzipSync} from 'node:zlib';
import test from 'node:test';
import {fetchSharedDatasetMetadataRecords, SharedDatasetCatalogResolutionError} from '../dist/index.js';

const rows = [{feature_id: '1', properties: {name: '雪'.repeat(12000)}}, {feature_id: '2'}];
const data = gzipSync(rows.map(row => JSON.stringify(row)).join('\n'));
const bytes = value => value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);

test('bounded decoder preserves UTF-8 across decompression chunks and rejects each resource limit', async () => {
  const fetchBytes = async () => bytes(data);
  const records = await fetchSharedDatasetMetadataRecords('https://fixture.test/data', {fetchBytes});
  assert.equal(records.get('1').properties.name, rows[0].properties.name);
  for (const options of [{maxCompressedBytes: 4}, {maxExpandedBytes: 20}, {maxRecords: 1}]) {
    await assert.rejects(fetchSharedDatasetMetadataRecords('https://fixture.test/data', {fetchBytes, ...options}), error => {
      assert(error instanceof SharedDatasetCatalogResolutionError);
      assert(error.message.includes(Object.keys(options)[0]));
      return /budget exceeded/.test(error.message);
    });
  }
  await assert.rejects(fetchSharedDatasetMetadataRecords('https://fixture.test/data', {fetchBytes, maxRecords: 0}), /positive safe integer/);
});

test('oversized advertised metadata is refused before a transfer', async () => {
  let requests = 0;
  await assert.rejects(fetchSharedDatasetMetadataRecords('https://fixture.test/data', {
    fetchBytes: async () => {requests++; return bytes(data);}, expectedSize: 100, maxCompressedBytes: 50
  }), /budget exceeded/);
  assert.equal(requests, 0);
});

test('default streaming fetch cancels a response that exceeds the compressed budget', async t => {
  let cancelled = false;
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
    start(controller) {controller.enqueue(new Uint8Array(100));}, cancel() {cancelled = true;}
  })));
  await assert.rejects(fetchSharedDatasetMetadataRecords('https://fixture.test/data', {maxCompressedBytes: 10}), /budget exceeded/);
  assert.equal(cancelled, true);
});

test('cancellation stops a pending reader and preserves AbortError', async t => {
  const controller = new AbortController();
  let cancelled = false;
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
    pull() {queueMicrotask(() => controller.abort());}, cancel() {cancelled = true;}
  })));
  await assert.rejects(fetchSharedDatasetMetadataRecords('https://fixture.test/data', {signal: controller.signal}), {name: 'AbortError'});
  assert.equal(cancelled, true);
});

test('streamed duplicate IDs and corrupt captured bytes still fail', async () => {
  const duplicate = gzipSync('{"feature_id":"1"}\n{"feature_id":"1"}\n');
  await assert.rejects(fetchSharedDatasetMetadataRecords('https://fixture.test/data', {fetchBytes: async () => bytes(duplicate)}), /duplicate feature_id/);
  await assert.rejects(fetchSharedDatasetMetadataRecords('https://fixture.test/data', {fetchBytes: async () => bytes(data), expectedSha256: '0'.repeat(64)}), /SHA-256 mismatch/);
});
