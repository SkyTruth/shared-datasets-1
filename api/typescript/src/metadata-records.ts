import {
  DEFAULT_SHARED_DATASETS_CATALOG_JSON_URL,
  FetchSharedDatasetCatalogJson,
  SharedDatasetCatalogFetchOptions,
  SharedDatasetCatalogRef,
  SharedDatasetCatalogResolutionError,
  getErrorMessage,
  normalizeSharedDatasetAssetSlug,
  resolveSharedDatasetPmtilesRef
} from './catalog.js';
import {
  SharedDatasetArtifactUrlOptions,
  SharedDatasetMetadataSidecarResolution,
  SharedDatasetReleaseIndex,
  resolveSharedDatasetMetadataSidecar,
  normalizeArtifactGeneration,
  resolveSharedDatasetRelease,
  selectReleaseFile,
  validateReleaseArtifact,
  SharedDatasetReleaseFile,
  sharedDatasetArtifactUrlFromGsUri
} from './artifact-url.js';
import {METADATA_DEFAULT_MAX_COMPRESSED_BYTES} from './snapshot-contract.js';

export type SharedDatasetMetadataRecord = {
  schema_version?: number;
  asset_slug?: string;
  release?: string;
  feature_id: string;
  geometry_hash?: string;
  properties_hash?: string;
  properties?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
  translation?: {
    locale: string;
    state: 'complete' | 'partial' | 'fallback';
    translated_fields: string[];
    fallback_fields: string[];
    machine_fields: string[];
    human_reviewed_fields: string[];
  };
  [key: string]: unknown;
};

export type SharedDatasetLayerSidecar = SharedDatasetMetadataSidecarResolution & {
  url: string | null;
};

export type SharedDatasetLayer = {
  ref: Omit<SharedDatasetCatalogRef, 'url'> & {url: string | null};
  pmtiles: {file: SharedDatasetReleaseFile; gsUri: string; generation: string} | null;
  releaseIndexUrl: string | null;
  releaseIndex: SharedDatasetReleaseIndex | null;
  resolvedRelease: string | null;
  sidecar: SharedDatasetLayerSidecar | null;
};

export type SharedDatasetLayerOptions = SharedDatasetCatalogFetchOptions &
  SharedDatasetArtifactUrlOptions & {
    version?: string | null;
    locale?: string | null;
    fetchReleaseIndexJson?: FetchSharedDatasetCatalogJson;
  };

export type FetchSharedDatasetMetadataBytes = (url: string, options?: {signal?: AbortSignal; maxBytes?: number}) => Promise<ArrayBuffer>;
export const DEFAULT_METADATA_LIMITS = {maxCompressedBytes: METADATA_DEFAULT_MAX_COMPRESSED_BYTES, maxExpandedBytes: 128 * 1024 * 1024, maxRecords: 1_000_000};

function metadataBudgetError(option: keyof typeof DEFAULT_METADATA_LIMITS) {
  return new SharedDatasetCatalogResolutionError(`Metadata budget exceeded (${option}); increase ${option} or use the feature metadata lookup API`);
}

export type SharedDatasetMetadataRecordsOptions = {
  fetchBytes?: FetchSharedDatasetMetadataBytes;
  signal?: AbortSignal;
  maxCompressedBytes?: number;
  maxExpandedBytes?: number;
  maxRecords?: number;
  expectedSize?: number | null;
  expectedSha256?: string | null;
  requireGzip?: boolean;
};

const defaultFetchJson: FetchSharedDatasetCatalogJson = async url => {
  if (!globalThis.fetch) {
    throw new Error('No fetch implementation is available');
  }

  const response = await globalThis.fetch(url);
  if (!response.ok) {
    throw Object.assign(new Error(`HTTP ${response.status}`), {status: response.status});
  }
  return response.json();
};

const defaultFetchBytes: FetchSharedDatasetMetadataBytes = async (url, options) => {
  const response = await globalThis.fetch(url, {signal: options?.signal, credentials: 'same-origin', cache: 'no-store'});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const limit = options?.maxBytes ?? DEFAULT_METADATA_LIMITS.maxCompressedBytes;
  if (Number(response.headers.get('content-length')) > limit) {
    await response.body?.cancel();
    throw metadataBudgetError('maxCompressedBytes');
  }
  if (!response.body) return new ArrayBuffer(0);
  const chunks: Uint8Array[] = [];
  let length = 0;
  await readChunks(response.body, options?.signal, chunk => {
    length += chunk.byteLength;
    if (length > limit) throw metadataBudgetError('maxCompressedBytes');
    chunks.push(chunk);
  });
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
  return bytes.buffer;
};

async function readChunks(stream: ReadableStream<Uint8Array>, signal: AbortSignal | undefined, accept: (chunk: Uint8Array) => void) {
  signal?.throwIfAborted();
  const reader = stream.getReader();
  const cancel = () => {void reader.cancel(signal?.reason).catch(() => {});};
  signal?.addEventListener('abort', cancel, {once: true});
  let done = false;
  try {
    while (true) {
      const item = await reader.read();
      signal?.throwIfAborted();
      if (item.done) {done = true; break;}
      accept(item.value);
    }
  } finally {
    signal?.removeEventListener('abort', cancel);
    if (!done) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

const resolveReleaseIndexUrl = (
  ref: SharedDatasetCatalogRef,
  catalogUrl: string
) => {
  const rawUrl = ref.releaseIndexUrl?.trim();
  if (!rawUrl) return null;
  try {
    return new URL(rawUrl, catalogUrl).toString();
  } catch (error) {
    throw new SharedDatasetCatalogResolutionError(
      `Unable to resolve shared dataset release index URL: ${getErrorMessage(error)}`
    );
  }
};

const parseReleaseIndexJson = (
  releaseIndexJson: unknown,
  assetSlug: string
): SharedDatasetReleaseIndex => {
  if (
    !releaseIndexJson ||
    typeof releaseIndexJson !== 'object' ||
    Array.isArray(releaseIndexJson)
  ) {
    throw new SharedDatasetCatalogResolutionError(
      'Unable to parse shared dataset release index: payload must be a JSON object'
    );
  }
  const releaseIndex = releaseIndexJson as SharedDatasetReleaseIndex;
  const indexSlug = normalizeSharedDatasetAssetSlug(releaseIndex.asset_slug);
  if (indexSlug !== assetSlug) {
    throw new SharedDatasetCatalogResolutionError(
      `Unable to parse shared dataset release index: asset_slug ${indexSlug} does not match ${assetSlug}`
    );
  }
  return releaseIndex;
};

export const resolveSharedDatasetLayer = async (
  assetSlug: string | null | undefined,
  options: SharedDatasetLayerOptions = {}
): Promise<SharedDatasetLayer> => {
  const normalizedSlug = normalizeSharedDatasetAssetSlug(assetSlug);
  const ref = await resolveSharedDatasetPmtilesRef(normalizedSlug, options);
  const catalogUrl = options.catalogUrl ?? DEFAULT_SHARED_DATASETS_CATALOG_JSON_URL;
  const releaseIndexUrl = resolveReleaseIndexUrl(ref, catalogUrl);
  const legacyLayer = (): SharedDatasetLayer => {
    if (options.version && options.version !== 'latest') throw new SharedDatasetCatalogResolutionError('Requested release was not found: no release index');
    return {ref, pmtiles: null, releaseIndexUrl: null, releaseIndex: null, resolvedRelease: null, sidecar: null};
  };
  if (!releaseIndexUrl) return legacyLayer();

  const fetchJson =
    options.fetchReleaseIndexJson ?? options.fetchJson ?? defaultFetchJson;
  let releaseIndexJson: unknown;
  try {
    releaseIndexJson = await fetchJson(releaseIndexUrl);
  } catch (error) {
    if (error && typeof error === 'object' && 'status' in error && error.status === 404) return legacyLayer();
    throw new SharedDatasetCatalogResolutionError(
      `Unable to load shared dataset release index: ${getErrorMessage(error)}`
    );
  }
  const releaseIndex = parseReleaseIndexJson(releaseIndexJson, normalizedSlug ?? '');

  const release = resolveSharedDatasetRelease(releaseIndex, options.version || 'latest');
  const file = selectReleaseFile(release.files!, 'pmtiles', ref.pmtilesPath || '');
  if (!file) throw new SharedDatasetCatalogResolutionError('Selected release does not include PMTiles');
  validateReleaseArtifact(file, normalizedSlug!, String(release.date), ref.pmtilesPath || '');
  const generation = normalizeArtifactGeneration(file.generation);
  const gsUri = String(file.path);
  const tileUrl = sharedDatasetArtifactUrlFromGsUri(gsUri, {...options, generation});
  const sidecar = resolveSharedDatasetMetadataSidecar({
    releaseIndex,
    version: options.version,
    locale: options.locale
  });
  if (sidecar) {
    validateReleaseArtifact(sidecar.file, normalizedSlug!, String(release.date), ref.pmtilesPath || '');
    sharedDatasetArtifactUrlFromGsUri(sidecar.gsUri, {...options, generation: sidecar.generation});
  }
  return {
    ref: {...ref, url: ref.accessTier === 'public' ? tileUrl : null},
    pmtiles: {file, gsUri, generation},
    releaseIndexUrl,
    releaseIndex,
    resolvedRelease: String(release.date),
    sidecar: sidecar
      ? {
          ...sidecar,
          url:
            ref.accessTier === 'public'
              ? sharedDatasetArtifactUrlFromGsUri(sidecar.gsUri, {...options, generation: sidecar.generation})
              : null
        }
      : null
  };
};

const isGzipBytes = (bytes: Uint8Array) => bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;

function addMetadataRecord(line: string, index: number, records: Map<string, SharedDatasetMetadataRecord>, maxRecords = Number.MAX_SAFE_INTEGER) {
  const trimmedLine = line.trim();
  if (!trimmedLine) return;
  let parsed: unknown;
  try {parsed = JSON.parse(trimmedLine);} catch (error) {
    throw new SharedDatasetCatalogResolutionError(`Unable to parse metadata sidecar line ${index}: ${getErrorMessage(error)}`);
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new SharedDatasetCatalogResolutionError(`Unable to parse metadata sidecar line ${index}: record must be a JSON object`);
  const record = parsed as SharedDatasetMetadataRecord;
  const featureId = typeof record.feature_id === 'string' ? record.feature_id.trim() : '';
  if (!featureId) throw new SharedDatasetCatalogResolutionError(`Unable to parse metadata sidecar line ${index}: missing feature_id`);
  if (records.has(featureId)) throw new SharedDatasetCatalogResolutionError(`Unable to parse metadata sidecar: duplicate feature_id ${featureId}`);
  if (records.size >= maxRecords) throw metadataBudgetError('maxRecords');
  records.set(featureId, record);
}

export const parseSharedDatasetMetadataRecords = (ndjsonText: string): Map<string, SharedDatasetMetadataRecord> => {
  const records = new Map<string, SharedDatasetMetadataRecord>();
  let start = 0, line = 1;
  for (let end = ndjsonText.indexOf('\n'); end >= 0; end = ndjsonText.indexOf('\n', start)) {
    addMetadataRecord(ndjsonText.slice(start, end), line++, records);
    start = end + 1;
  }
  addMetadataRecord(ndjsonText.slice(start), line, records);
  return records;
};

/** Bounded download and incremental NDJSON decoding. Custom loaders must honor
 * their supplied signal/budget; their returned bytes are checked again here. */
export const fetchSharedDatasetMetadataRecords = async (url: string, options: SharedDatasetMetadataRecordsOptions = {}): Promise<Map<string, SharedDatasetMetadataRecord>> => {
  const limits = {...DEFAULT_METADATA_LIMITS};
  for (const key of Object.keys(limits) as (keyof typeof limits)[]) {
    const value = options[key] ?? limits[key];
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`${key} must be a positive safe integer`);
    limits[key] = value;
  }
  options.signal?.throwIfAborted();
  if (options.expectedSize != null && options.expectedSize > limits.maxCompressedBytes) throw metadataBudgetError('maxCompressedBytes');
  let buffer: ArrayBuffer;
  try {
    buffer = await (options.fetchBytes ?? defaultFetchBytes)(url, {signal: options.signal, maxBytes: limits.maxCompressedBytes});
  } catch (error) {
    options.signal?.throwIfAborted();
    throw new SharedDatasetCatalogResolutionError(`Unable to load shared dataset metadata sidecar: ${getErrorMessage(error)}`);
  }
  options.signal?.throwIfAborted();
  if (buffer.byteLength > limits.maxCompressedBytes) throw metadataBudgetError('maxCompressedBytes');
  if (options.expectedSize != null && buffer.byteLength !== options.expectedSize) throw new SharedDatasetCatalogResolutionError('Metadata size mismatch');
  if (options.expectedSha256 != null) {
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map(x => x.toString(16).padStart(2, '0')).join('');
    if (hash !== options.expectedSha256) throw new SharedDatasetCatalogResolutionError('Metadata SHA-256 mismatch');
  }
  const bytes = new Uint8Array(buffer);
  const gzip = isGzipBytes(bytes);
  if (options.requireGzip && !gzip) throw new SharedDatasetCatalogResolutionError('Captured metadata must be gzip NDJSON');
  let stream = new Blob([bytes]).stream();
  if (gzip) stream = stream.pipeThrough(new DecompressionStream('gzip'));
  const records = new Map<string, SharedDatasetMetadataRecord>();
  const decoder = new TextDecoder('utf-8', {fatal: true});
  let expanded = 0, pending = '', line = 1;
  await readChunks(stream, options.signal, chunk => {
    expanded += chunk.byteLength;
    if (expanded > limits.maxExpandedBytes) throw metadataBudgetError('maxExpandedBytes');
    pending += decoder.decode(chunk, {stream: true});
    let start = 0;
    for (let end = pending.indexOf('\n'); end >= 0; end = pending.indexOf('\n', start)) {
      addMetadataRecord(pending.slice(start, end), line++, records, limits.maxRecords);
      start = end + 1;
    }
    pending = pending.slice(start);
  });
  addMetadataRecord(pending + decoder.decode(), line, records, limits.maxRecords);
  return records;
};
