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

export type SharedDatasetMetadataRecord = {
  schema_version?: number;
  asset_slug?: string;
  release?: string;
  feature_id: string;
  geometry_hash?: string;
  properties_hash?: string;
  properties?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
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

export type FetchSharedDatasetMetadataBytes = (url: string) => Promise<ArrayBuffer>;

export type SharedDatasetMetadataRecordsOptions = {
  fetchBytes?: FetchSharedDatasetMetadataBytes;
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

const defaultFetchBytes: FetchSharedDatasetMetadataBytes = async url => {
  if (!globalThis.fetch) {
    throw new Error('No fetch implementation is available');
  }

  const response = await globalThis.fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.arrayBuffer();
};

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

const isGzipBytes = (bytes: Uint8Array) =>
  bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;

const gunzipToText = async (bytes: Uint8Array) => {
  if (typeof DecompressionStream === 'undefined') {
    throw new SharedDatasetCatalogResolutionError(
      'Metadata sidecar is gzip-compressed and DecompressionStream is unavailable in this runtime'
    );
  }
  const stream = new Blob([bytes as BlobPart])
    .stream()
    .pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).text();
};

export const parseSharedDatasetMetadataRecords = (
  ndjsonText: string
): Map<string, SharedDatasetMetadataRecord> => {
  const records = new Map<string, SharedDatasetMetadataRecord>();
  ndjsonText.split('\n').forEach((line, index) => {
    const trimmedLine = line.trim();
    if (!trimmedLine) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmedLine);
    } catch (error) {
      throw new SharedDatasetCatalogResolutionError(
        `Unable to parse metadata sidecar line ${index + 1}: ${getErrorMessage(error)}`
      );
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new SharedDatasetCatalogResolutionError(
        `Unable to parse metadata sidecar line ${index + 1}: record must be a JSON object`
      );
    }
    const record = parsed as SharedDatasetMetadataRecord;
    const featureId =
      typeof record.feature_id === 'string' ? record.feature_id.trim() : '';
    if (!featureId) {
      throw new SharedDatasetCatalogResolutionError(
        `Unable to parse metadata sidecar line ${index + 1}: missing feature_id`
      );
    }
    if (records.has(featureId)) {
      throw new SharedDatasetCatalogResolutionError(
        `Unable to parse metadata sidecar: duplicate feature_id ${featureId}`
      );
    }
    records.set(featureId, record);
  });
  return records;
};

export const fetchSharedDatasetMetadataRecords = async (
  url: string,
  { fetchBytes = defaultFetchBytes }: SharedDatasetMetadataRecordsOptions = {}
): Promise<Map<string, SharedDatasetMetadataRecord>> => {
  let buffer: ArrayBuffer;
  try {
    buffer = await fetchBytes(url);
  } catch (error) {
    throw new SharedDatasetCatalogResolutionError(
      `Unable to load shared dataset metadata sidecar: ${getErrorMessage(error)}`
    );
  }
  const bytes = new Uint8Array(buffer);
  const ndjsonText = isGzipBytes(bytes)
    ? await gunzipToText(bytes)
    : new TextDecoder('utf-8').decode(bytes);
  return parseSharedDatasetMetadataRecords(ndjsonText);
};
