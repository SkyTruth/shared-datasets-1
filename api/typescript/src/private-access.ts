import {
  type SharedDatasetAccessTier,
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
