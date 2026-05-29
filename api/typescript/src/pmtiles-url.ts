export const DEFAULT_PMTILES_PRIVATE_PATH_PREFIX = '/pmtiles/private/';
export const DEFAULT_PMTILES_URL_BASE = 'https://tiles.skytruth.org';

export type PmtilesUrlOptions = {
  baseUrl?: string;
  privatePathPrefix?: string;
};

export const isPrivatePmtilesUrl = (
  url: string,
  {
    baseUrl = DEFAULT_PMTILES_URL_BASE,
    privatePathPrefix = DEFAULT_PMTILES_PRIVATE_PATH_PREFIX
  }: PmtilesUrlOptions = {}
) => {
  try {
    const parsedUrl = new URL(url, baseUrl);
    return (
      parsedUrl.origin === new URL(baseUrl).origin &&
      parsedUrl.pathname.startsWith(privatePathPrefix)
    );
  } catch {
    return false;
  }
};

export const getPmtilesFetchCredentials = (
  url: string,
  options?: PmtilesUrlOptions
): RequestCredentials =>
  isPrivatePmtilesUrl(url, options) ? 'include' : 'same-origin';
