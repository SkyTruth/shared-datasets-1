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
  ref: SharedDatasetCatalogRef;
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
    throw new Error(`HTTP ${response.status}`);
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

const releaseDate = (release: unknown) => {
  const date =
    release && typeof release === 'object' && !Array.isArray(release)
      ? (release as Record<string, unknown>).date
      : null;
  return typeof date === 'string' && date.trim() ? date.trim() : null;
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
  if (indexSlug && indexSlug !== assetSlug) {
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
  if (!releaseIndexUrl) {
    return {
      ref,
      releaseIndexUrl: null,
      releaseIndex: null,
      resolvedRelease: releaseDate(ref.latestRelease),
      sidecar: null
    };
  }

  const fetchJson =
    options.fetchReleaseIndexJson ?? options.fetchJson ?? defaultFetchJson;
  let releaseIndexJson: unknown;
  try {
    releaseIndexJson = await fetchJson(releaseIndexUrl);
  } catch (error) {
    throw new SharedDatasetCatalogResolutionError(
      `Unable to load shared dataset release index: ${getErrorMessage(error)}`
    );
  }
  const releaseIndex = parseReleaseIndexJson(releaseIndexJson, normalizedSlug ?? '');

  const sidecar = resolveSharedDatasetMetadataSidecar({
    releaseIndex,
    version: options.version,
    locale: options.locale
  });
  return {
    ref,
    releaseIndexUrl,
    releaseIndex,
    resolvedRelease:
      sidecar?.resolvedVersion ??
      releaseDate(releaseIndex.latest_release) ??
      releaseDate(ref.latestRelease),
    sidecar: sidecar
      ? {
          ...sidecar,
          url:
            ref.accessTier === 'public'
              ? sharedDatasetArtifactUrlFromGsUri(sidecar.gsUri, options)
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
