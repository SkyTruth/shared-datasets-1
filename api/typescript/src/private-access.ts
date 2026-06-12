import {
  normalizeSharedDatasetAssetSlug,
  resolveAllSharedDatasetPmtilesRefs,
  type SharedDatasetAccessTier,
  type SharedDatasetCatalogFetchOptions,
  type SharedDatasetCatalogRef,
  SharedDatasetCatalogResolutionError
} from './catalog.js';

export type SharedDatasetAccessTierCache = {
  expiresAt: number;
  tiers: Record<string, SharedDatasetAccessTier>;
};

type TtlMs = number | (() => number);

export type SharedDatasetAccessTierLookupOptions = {
  loadAccessTiers: () => Promise<Record<string, SharedDatasetAccessTier>>;
  ttlMs?: TtlMs;
  now?: () => number;
};

export const DEFAULT_ACCESS_TIER_CACHE_TTL_MS = 5 * 60 * 1000;

const resolveTtlMs = (ttlMs: TtlMs | undefined) => {
  const value =
    typeof ttlMs === 'function'
      ? ttlMs()
      : (ttlMs ?? DEFAULT_ACCESS_TIER_CACHE_TTL_MS);
  return Number.isFinite(value) && value > 0
    ? Math.floor(value)
    : DEFAULT_ACCESS_TIER_CACHE_TTL_MS;
};

export const getAccessTiersFromSharedDatasetPmtilesRefs = (
  refs: Record<string, SharedDatasetCatalogRef>
) =>
  Object.fromEntries(
    Object.entries(refs).map(([slug, ref]) => [slug, ref.accessTier])
  );

export const createSharedDatasetAccessTierLoader = ({
  loadAccessTiers,
  now = Date.now,
  ttlMs
}: SharedDatasetAccessTierLookupOptions) => {
  let accessTierCache: SharedDatasetAccessTierCache | null = null;

  return async () => {
    const currentTime = now();
    if (accessTierCache && accessTierCache.expiresAt > currentTime) {
      return accessTierCache.tiers;
    }

    const tiers = await loadAccessTiers();
    accessTierCache = {
      expiresAt: currentTime + resolveTtlMs(ttlMs),
      tiers
    };
    return tiers;
  };
};

export const createSharedDatasetAccessTierLookup = (
  options: SharedDatasetAccessTierLookupOptions
) => {
  const loadAccessTiers = createSharedDatasetAccessTierLoader(options);

  return async (slug: string) => {
    const tiers = await loadAccessTiers();
    const accessTier = tiers[slug];
    if (!accessTier) {
      throw new SharedDatasetCatalogResolutionError(
        `Unable to resolve shared dataset access tier: ${slug}`
      );
    }
    return accessTier;
  };
};

export type CatalogSharedDatasetAccessTierLookupOptions =
  SharedDatasetCatalogFetchOptions & {
    ttlMs?: TtlMs;
    now?: () => number;
  };

/**
 * Creates a cached slug-to-tier lookup backed by the shared datasets catalog.
 * Convenience wiring of `createSharedDatasetAccessTierLookup` over
 * `resolveAllSharedDatasetPmtilesRefs` for the common server case.
 */
export const createCatalogSharedDatasetAccessTierLookup = ({
  catalogUrl,
  fetchJson,
  now,
  ttlMs
}: CatalogSharedDatasetAccessTierLookupOptions = {}) =>
  createSharedDatasetAccessTierLookup({
    loadAccessTiers: async () =>
      getAccessTiersFromSharedDatasetPmtilesRefs(
        await resolveAllSharedDatasetPmtilesRefs({ catalogUrl, fetchJson })
      ),
    now,
    ttlMs
  });

export type SharedDatasetRowFilterOptions<T> = {
  getAccessTier: (slug: string) => Promise<SharedDatasetAccessTier>;
  getAssetSlug?: (row: T) => string | null | undefined;
};

export type SharedDatasetRowFilterResult<T> = {
  rows: T[];
  tierLookupFailed: boolean;
};

const defaultGetAssetSlug = <T>(row: T) =>
  (row as { assetSlug?: string | null }).assetSlug;

/**
 * Drops rows that belong to non-public shared datasets, for payloads served
 * to unauthenticated or otherwise untrusted audiences. Fails closed: a row
 * whose access tier cannot be resolved is dropped rather than exposed, and
 * `tierLookupFailed` reports the degradation so callers can avoid
 * long-caching the over-filtered result. Rows without an asset slug are not
 * shared-dataset rows and pass through unchanged.
 */
export const filterPrivateSharedDatasetRows = async <T>(
  rows: T[],
  {
    getAccessTier,
    getAssetSlug = defaultGetAssetSlug
  }: SharedDatasetRowFilterOptions<T>
): Promise<SharedDatasetRowFilterResult<T>> => {
  let tierLookupFailed = false;
  const filtered = await Promise.all(
    rows.map(async (row): Promise<T | null> => {
      const assetSlug = normalizeSharedDatasetAssetSlug(getAssetSlug(row));
      if (!assetSlug) return row;
      try {
        const accessTier = await getAccessTier(assetSlug);
        return accessTier === 'public' ? row : null;
      } catch {
        tierLookupFailed = true;
        return null;
      }
    })
  );
  return {
    rows: filtered.filter((row): row is Awaited<T> => row !== null),
    tierLookupFailed
  };
};
