export const DEFAULT_SHARED_DATASETS_CATALOG_JSON_URL =
  'https://tiles.skytruth.org/_catalog/web/catalog.json';

export type SharedDatasetAccessTier = 'public' | 'private';

export type SharedDatasetPmtilesRef = {
  accessTier: SharedDatasetAccessTier;
  url: string;
};

type SharedDatasetCatalogMetadata = {
  title: string | null;
  description: string | null;
  status: string | null;
  consumerGuidance: string | null;
  citation: string | null;
  license: string | null;
  source: string | null;
  sourceUrl: string | null;
  docsUrl: string | null;
  releaseIndexUrl: string | null;
  latestRelease: Record<string, unknown> | null;
  lastUpdated: string | null;
};

export type SharedDatasetCatalogRef = SharedDatasetPmtilesRef &
  SharedDatasetCatalogMetadata;

export type SharedDatasetsCatalogAsset = {
  access_tier?: string | null;
  asset_slug?: string | null;
  available_formats?: string[] | string | null;
  citation?: string | null;
  consumer_guidance?: string | null;
  description?: string | null;
  docs_url?: string | null;
  has_pmtiles?: boolean | string | null;
  last_updated?: string | null;
  latest_release?: Record<string, unknown> | null;
  license?: string | null;
  pmtiles_url?: string | null;
  release_index_url?: string | null;
  slug?: string | null;
  source?: string | null;
  source_url?: string | null;
  status?: string | null;
  title?: string | null;
};

export type SharedDatasetsCatalogJson = {
  assets?: SharedDatasetsCatalogAsset[];
};

export type FetchSharedDatasetCatalogJson = (url: string) => Promise<unknown>;

export type SharedDatasetCatalogFetchOptions = {
  catalogUrl?: string;
  fetchJson?: FetchSharedDatasetCatalogJson;
};

export class SharedDatasetCatalogResolutionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SharedDatasetCatalogResolutionError';
  }
}

export const getErrorMessage = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

const cleanCatalogString = (value: string | null | undefined) => {
  const cleanedValue = value?.trim();
  return cleanedValue ? cleanedValue : null;
};

const cleanCatalogObject = (value: unknown) =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

export const normalizeSharedDatasetAssetSlug = (
  value: string | null | undefined
) => value?.trim().toLowerCase() ?? null;

export const getSharedDatasetAccessTier = (
  value: string | null | undefined
): SharedDatasetAccessTier | null => {
  const tier = value?.trim().toLowerCase();
  return tier === 'public' || tier === 'private' ? tier : null;
};

const getCatalogAssetSlug = (asset: SharedDatasetsCatalogAsset) =>
  normalizeSharedDatasetAssetSlug(asset.slug ?? asset.asset_slug);

const getCatalogFormats = (asset: SharedDatasetsCatalogAsset) => {
  const formats = asset.available_formats;
  if (Array.isArray(formats)) {
    return formats.map(format => format.trim().toLowerCase()).filter(Boolean);
  }

  return (formats ?? '')
    .split(/[;,]/)
    .map(format => format.trim().toLowerCase())
    .filter(Boolean);
};

const catalogAssetHasPmtiles = (asset: SharedDatasetsCatalogAsset) =>
  asset.has_pmtiles === true ||
  String(asset.has_pmtiles ?? '')
    .trim()
    .toLowerCase() === 'true' ||
  getCatalogFormats(asset).includes('pmtiles') ||
  !!asset.pmtiles_url;

const getCatalogSharedDatasetRef = (
  asset: SharedDatasetsCatalogAsset
): [string, SharedDatasetCatalogRef] | null => {
  const assetSlug = getCatalogAssetSlug(asset);
  if (!assetSlug || !catalogAssetHasPmtiles(asset)) return null;

  const accessTier = getSharedDatasetAccessTier(asset.access_tier);
  const url = asset.pmtiles_url?.trim();
  if (!accessTier || !url) return null;

  return [
    assetSlug,
    {
      accessTier,
      url,
      title: cleanCatalogString(asset.title),
      description: cleanCatalogString(asset.description),
      status: cleanCatalogString(asset.status),
      consumerGuidance: cleanCatalogString(asset.consumer_guidance),
      citation: cleanCatalogString(asset.citation),
      license: cleanCatalogString(asset.license),
      source: cleanCatalogString(asset.source),
      sourceUrl: cleanCatalogString(asset.source_url),
      docsUrl: cleanCatalogString(asset.docs_url),
      releaseIndexUrl: cleanCatalogString(asset.release_index_url),
      latestRelease: cleanCatalogObject(asset.latest_release),
      lastUpdated: cleanCatalogString(asset.last_updated)
    }
  ];
};

export const parseSharedDatasetsCatalogJson = (
  catalogJson: unknown
): SharedDatasetsCatalogJson => {
  if (
    !catalogJson ||
    typeof catalogJson !== 'object' ||
    !Array.isArray((catalogJson as SharedDatasetsCatalogJson).assets)
  ) {
    throw new SharedDatasetCatalogResolutionError(
      'Unable to parse shared datasets catalog: missing assets array'
    );
  }

  return catalogJson as SharedDatasetsCatalogJson;
};

const getRequestedSlugs = (assetSlugs: Array<string | null | undefined>) =>
  new Set(
    assetSlugs.flatMap(assetSlug => {
      const normalizedSlug = normalizeSharedDatasetAssetSlug(assetSlug);
      return normalizedSlug ? [normalizedSlug] : [];
    })
  );

export const resolveSharedDatasetPmtilesRefsFromCatalogJson = (
  catalogJson: unknown,
  assetSlugs?: Array<string | null | undefined>
) => {
  const requestedSlugs = assetSlugs ? getRequestedSlugs(assetSlugs) : null;
  if (requestedSlugs && !requestedSlugs.size) return {};

  const refs: Record<string, SharedDatasetCatalogRef> = {};
  parseSharedDatasetsCatalogJson(catalogJson).assets?.forEach(asset => {
    const sharedDatasetRef = getCatalogSharedDatasetRef(asset);
    if (!sharedDatasetRef) return;

    const [assetSlug, ref] = sharedDatasetRef;
    if (!requestedSlugs || requestedSlugs.has(assetSlug)) {
      refs[assetSlug] = ref;
    }
  });

  if (requestedSlugs) {
    const unresolvedSlugs = [...requestedSlugs].filter(slug => !refs[slug]);
    if (unresolvedSlugs.length) {
      throw new SharedDatasetCatalogResolutionError(
        `Unable to resolve shared dataset PMTiles refs: ${unresolvedSlugs.join(', ')}`
      );
    }
  }

  return refs;
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

export const fetchSharedDatasetCatalogJson = async ({
  catalogUrl = DEFAULT_SHARED_DATASETS_CATALOG_JSON_URL,
  fetchJson = defaultFetchJson
}: SharedDatasetCatalogFetchOptions = {}) => {
  try {
    return await fetchJson(catalogUrl);
  } catch (error) {
    throw new SharedDatasetCatalogResolutionError(
      `Unable to load shared datasets catalog: ${getErrorMessage(error)}`
    );
  }
};

export const resolveSharedDatasetPmtilesRefs = async (
  assetSlugs: Array<string | null | undefined>,
  options?: SharedDatasetCatalogFetchOptions
) =>
  resolveSharedDatasetPmtilesRefsFromCatalogJson(
    await fetchSharedDatasetCatalogJson(options),
    assetSlugs
  );

export const resolveAllSharedDatasetPmtilesRefs = async (
  options?: SharedDatasetCatalogFetchOptions
) =>
  resolveSharedDatasetPmtilesRefsFromCatalogJson(
    await fetchSharedDatasetCatalogJson(options)
  );

export const resolveSharedDatasetPmtilesRef = async (
  assetSlug: string | null | undefined,
  options?: SharedDatasetCatalogFetchOptions
) => {
  const normalizedSlug = normalizeSharedDatasetAssetSlug(assetSlug);
  if (!normalizedSlug) {
    throw new SharedDatasetCatalogResolutionError(
      'Unable to resolve shared dataset PMTiles ref: missing asset_slug'
    );
  }

  const refs = await resolveSharedDatasetPmtilesRefs([normalizedSlug], options);
  return refs[normalizedSlug];
};
