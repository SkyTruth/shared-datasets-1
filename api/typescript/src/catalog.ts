import type { PmtilesTier } from './pmtiles-cdn-session.js';

export const DEFAULT_SHARED_DATASETS_CATALOG_JSON_URL =
  'https://tiles.skytruth.org/_catalog/web/catalog.json';

export type SharedDatasetAccessTier = PmtilesTier;

export type SharedDatasetPmtilesRef = {
  accessTier: SharedDatasetAccessTier;
  url: string;
};

export type SharedDatasetLocalizedNameReviewState =
  | 'source_provided'
  | 'machine_translated'
  | 'human_reviewed'
  | 'mixed';

export type SharedDatasetLocalizedNameTranslation = {
  locale_code: string;
  field: string;
  review_state_field?: string | null;
  label?: string | null;
  review_state: SharedDatasetLocalizedNameReviewState;
};

export type SharedDatasetLocalizedNames = {
  storage?: string | null;
  join_key?: string | null;
  localization_file?: string | null;
  property_template?: string | null;
  locale_code_format?: string | null;
  fallback_locale?: string | null;
  fallback_field?: string | null;
  available_locales?: string[];
  translations?: SharedDatasetLocalizedNameTranslation[];
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
  localizedNames: SharedDatasetLocalizedNames | null;
};

export type SharedDatasetCatalogRef = SharedDatasetPmtilesRef &
  SharedDatasetCatalogMetadata;

export type SharedDatasetsCatalogAsset = {
  access_tier?: string | null;
  available_formats: string[];
  citation?: string | null;
  colorizer_metadata?: {
    schema_version?: number;
    source?: 'metadata_sidecar_schema' | 'pmtiles_vector_layers' | 'none' | string;
    field_source?: string;
    schema_file?: string;
    feature_id_property?: string;
  } | null;
  consumer_guidance?: string | null;
  description?: string | null;
  docs_url?: string | null;
  has_csv: boolean;
  has_geojson: boolean;
  has_pmtiles: boolean;
  last_updated?: string | null;
  latest_release?: Record<string, unknown> | null;
  localized_names?: SharedDatasetLocalizedNames | null;
  license?: string | null;
  pmtiles_url?: string | null;
  release_index_url?: string | null;
  slug: string;
  source?: string | null;
  source_url?: string | null;
  status?: string | null;
  title?: string | null;
};

export type SharedDatasetsCatalogJson = {
  assets: SharedDatasetsCatalogAsset[];
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

const cleanCatalogLocalizedNames = (value: unknown) =>
  cleanCatalogObject(value) as SharedDatasetLocalizedNames | null;

export const normalizeSharedDatasetAssetSlug = (
  value: string | null | undefined
) => value?.trim().toLowerCase() ?? null;

export const getSharedDatasetAccessTier = (
  value: string | null | undefined
): SharedDatasetAccessTier | null => {
  const tier = value?.trim().toLowerCase();
  return tier === 'public' || tier === 'private' || tier === 'internal'
    ? tier
    : null;
};

const requireCatalogString = (
  value: unknown,
  fieldName: string,
  index: number
) => {
  if (typeof value !== 'string' || !value.trim()) {
    throw new SharedDatasetCatalogResolutionError(
      `Unable to parse shared datasets catalog: assets[${index}].${fieldName} must be a non-empty string`
    );
  }
};

const requireCatalogBoolean = (
  value: unknown,
  fieldName: string,
  index: number
) => {
  if (typeof value !== 'boolean') {
    throw new SharedDatasetCatalogResolutionError(
      `Unable to parse shared datasets catalog: assets[${index}].${fieldName} must be a boolean`
    );
  }
};

const requireCatalogFormats = (
  value: unknown,
  fieldName: string,
  index: number
) => {
  if (!Array.isArray(value) || value.some(format => typeof format !== 'string')) {
    throw new SharedDatasetCatalogResolutionError(
      `Unable to parse shared datasets catalog: assets[${index}].${fieldName} must be an array of strings`
    );
  }
};

const validateCatalogAsset = (asset: unknown, index: number) => {
  const record = cleanCatalogObject(asset);
  if (!record) {
    throw new SharedDatasetCatalogResolutionError(
      `Unable to parse shared datasets catalog: assets[${index}] must be an object`
    );
  }
  requireCatalogString(record.slug, 'slug', index);
  requireCatalogFormats(record.available_formats, 'available_formats', index);
  requireCatalogBoolean(record.has_pmtiles, 'has_pmtiles', index);
  requireCatalogBoolean(record.has_geojson, 'has_geojson', index);
  requireCatalogBoolean(record.has_csv, 'has_csv', index);
};

const getCatalogAssetSlug = (asset: SharedDatasetsCatalogAsset) =>
  normalizeSharedDatasetAssetSlug(asset.slug);

const getCatalogFormats = (asset: SharedDatasetsCatalogAsset) => {
  return asset.available_formats
    .map(format => format.trim().toLowerCase())
    .filter(Boolean);
};

const catalogAssetHasPmtiles = (asset: SharedDatasetsCatalogAsset) =>
  asset.has_pmtiles && getCatalogFormats(asset).includes('pmtiles');

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
      lastUpdated: cleanCatalogString(asset.last_updated),
      localizedNames: cleanCatalogLocalizedNames(asset.localized_names)
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

  const parsedCatalogJson = catalogJson as SharedDatasetsCatalogJson;
  parsedCatalogJson.assets.forEach(validateCatalogAsset);
  return parsedCatalogJson;
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
  parseSharedDatasetsCatalogJson(catalogJson).assets.forEach(asset => {
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
      'Unable to resolve shared dataset PMTiles ref: missing slug'
    );
  }

  const refs = await resolveSharedDatasetPmtilesRefs([normalizedSlug], options);
  return refs[normalizedSlug];
};
