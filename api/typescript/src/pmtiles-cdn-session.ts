export type PmtilesTier = 'public' | 'private' | 'internal';

export type PmtilesRestrictedTier = Exclude<PmtilesTier, 'public'>;

export const PMTILES_TIERS: readonly PmtilesTier[] = [
  'public',
  'private',
  'internal'
];

export const RESTRICTED_PMTILES_TIERS: readonly PmtilesRestrictedTier[] = [
  'private',
  'internal'
];

export const getPmtilesTier = (
  tierParam: string | string[] | undefined
): PmtilesTier | null => {
  const tier = Array.isArray(tierParam) ? tierParam[0] : tierParam;
  return tier === 'public' || tier === 'private' || tier === 'internal'
    ? tier
    : null;
};
