export const DEFAULT_PMTILES_PRIVATE_PATH_PREFIX = '/pmtiles/private/';
export const DEFAULT_PMTILES_RESTRICTED_PATH_PREFIXES = [
  '/pmtiles/private/',
  '/pmtiles/internal/'
];
export const DEFAULT_PMTILES_URL_BASE = 'https://tiles.skytruth.org';

export type PmtilesUrlOptions = {
  baseUrl?: string;
  privatePathPrefix?: string;
};

export type RestrictedPmtilesUrlOptions = {
  baseUrl?: string;
  restrictedPathPrefixes?: string[];
};

/**
 * True for any PMTiles URL on a restricted (cookie-gated) tier prefix —
 * currently `private` and `internal`.
 */
export const isRestrictedPmtilesUrl = (
  url: string,
  {
    baseUrl = DEFAULT_PMTILES_URL_BASE,
    restrictedPathPrefixes = DEFAULT_PMTILES_RESTRICTED_PATH_PREFIXES
  }: RestrictedPmtilesUrlOptions = {}
) => {
  try {
    const parsedUrl = new URL(url, baseUrl);
    return (
      parsedUrl.origin === new URL(baseUrl).origin &&
      restrictedPathPrefixes.some(prefix =>
        parsedUrl.pathname.startsWith(prefix)
      )
    );
  } catch {
    return false;
  }
};

export const isPrivatePmtilesUrl = (
  url: string,
  {
    baseUrl = DEFAULT_PMTILES_URL_BASE,
    privatePathPrefix = DEFAULT_PMTILES_PRIVATE_PATH_PREFIX
  }: PmtilesUrlOptions = {}
) =>
  isRestrictedPmtilesUrl(url, {
    baseUrl,
    restrictedPathPrefixes: [privatePathPrefix]
  });

export const getPmtilesFetchCredentials = (
  url: string,
  options?: RestrictedPmtilesUrlOptions & PmtilesUrlOptions
): RequestCredentials => {
  const restrictedPathPrefixes =
    options?.restrictedPathPrefixes ??
    (options?.privatePathPrefix
      ? [options.privatePathPrefix]
      : DEFAULT_PMTILES_RESTRICTED_PATH_PREFIXES);
  return isRestrictedPmtilesUrl(url, {
    baseUrl: options?.baseUrl,
    restrictedPathPrefixes
  })
    ? 'include'
    : 'same-origin';
};
