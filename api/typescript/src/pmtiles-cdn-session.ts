export type PmtilesTier = 'public' | 'private';

export const getPmtilesTier = (
  tierParam: string | string[] | undefined
): PmtilesTier | null => {
  const tier = Array.isArray(tierParam) ? tierParam[0] : tierParam;
  return tier === 'public' || tier === 'private' ? tier : null;
};
