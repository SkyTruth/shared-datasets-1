import crypto from 'node:crypto';

import {
  type PmtilesRestrictedTier,
  RESTRICTED_PMTILES_TIERS
} from './pmtiles-cdn-session.js';

export type PmtilesTierPathConfig = {
  path: string;
  urlPrefix: string;
};

export type PmtilesCdnSessionConfig = {
  cookieName: string;
  cookieDomain: string;
  /** @deprecated Alias for `tierPaths.private.path`. */
  privatePath: string;
  ttlSeconds: number;
  /** @deprecated Alias for `tierPaths.private.urlPrefix`. */
  privateUrlPrefix: string;
  signingKeyName: string;
  tierPaths: Record<PmtilesRestrictedTier, PmtilesTierPathConfig>;
  now: () => number;
};

export type PmtilesCdnSessionConfigInput = Partial<
  Omit<PmtilesCdnSessionConfig, 'tierPaths'>
> & {
  tierPaths?: Partial<Record<PmtilesRestrictedTier, PmtilesTierPathConfig>>;
};

export type PmtilesCdnSigningKeyDecodeOptions = {
  expectedBytes?: number;
};

export const DEFAULT_PMTILES_CDN_SIGNING_KEY_BYTES = 16;

export const DEFAULT_PMTILES_CDN_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60;

export const DEFAULT_PMTILES_CDN_TIER_PATHS: Record<
  PmtilesRestrictedTier,
  PmtilesTierPathConfig
> = {
  private: {
    path: '/pmtiles/private',
    urlPrefix: 'https://tiles.skytruth.org/pmtiles/private/'
  },
  internal: {
    path: '/pmtiles/internal',
    urlPrefix: 'https://tiles.skytruth.org/pmtiles/internal/'
  }
};

export const DEFAULT_PMTILES_CDN_SESSION_CONFIG: PmtilesCdnSessionConfig = {
  cookieName: 'Cloud-CDN-Cookie',
  cookieDomain: '.skytruth.org',
  privatePath: DEFAULT_PMTILES_CDN_TIER_PATHS.private.path,
  ttlSeconds: DEFAULT_PMTILES_CDN_SESSION_TTL_SECONDS,
  privateUrlPrefix: DEFAULT_PMTILES_CDN_TIER_PATHS.private.urlPrefix,
  signingKeyName: 'shared-datasets-pmtiles-v1',
  tierPaths: DEFAULT_PMTILES_CDN_TIER_PATHS,
  now: Date.now
};

const getConfig = (
  config: PmtilesCdnSessionConfigInput = {}
): PmtilesCdnSessionConfig => {
  const ttlSeconds = Number(config.ttlSeconds);
  const privateTierPath: PmtilesTierPathConfig = {
    path:
      config.tierPaths?.private?.path ??
      config.privatePath ??
      DEFAULT_PMTILES_CDN_TIER_PATHS.private.path,
    urlPrefix:
      config.tierPaths?.private?.urlPrefix ??
      config.privateUrlPrefix ??
      DEFAULT_PMTILES_CDN_TIER_PATHS.private.urlPrefix
  };

  return {
    ...DEFAULT_PMTILES_CDN_SESSION_CONFIG,
    ...config,
    privatePath: privateTierPath.path,
    privateUrlPrefix: privateTierPath.urlPrefix,
    tierPaths: {
      private: privateTierPath,
      internal:
        config.tierPaths?.internal ?? DEFAULT_PMTILES_CDN_TIER_PATHS.internal
    },
    ttlSeconds:
      Number.isFinite(ttlSeconds) && ttlSeconds > 0
        ? Math.floor(ttlSeconds)
        : DEFAULT_PMTILES_CDN_SESSION_CONFIG.ttlSeconds,
    now: config.now ?? DEFAULT_PMTILES_CDN_SESSION_CONFIG.now
  };
};

const toUrlSafeBase64 = (value: string | Buffer) =>
  Buffer.from(value).toString('base64').replace(/\+/g, '-').replace(/\//g, '_');

const getPmtilesCookieAttributes = (
  path: string,
  config: PmtilesCdnSessionConfig
) =>
  `Domain=${config.cookieDomain}; Path=${path}; Secure; HttpOnly; SameSite=None`;

const getExpiredPmtilesCookie = (
  path: string,
  config: PmtilesCdnSessionConfig
) =>
  `${config.cookieName}=; ${getPmtilesCookieAttributes(path, config)}; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT`;

export const decodePmtilesCdnSigningKey = (
  encodedKey: string | null | undefined,
  {
    expectedBytes = DEFAULT_PMTILES_CDN_SIGNING_KEY_BYTES
  }: PmtilesCdnSigningKeyDecodeOptions = {}
) => {
  const trimmedKey = encodedKey?.trim();
  if (!trimmedKey) {
    throw new Error('PMTiles signing key secret version is empty');
  }

  const key = Buffer.from(
    trimmedKey.replace(/-/g, '+').replace(/_/g, '/'),
    'base64'
  );
  if (key.length !== expectedBytes) {
    throw new Error(
      `PMTiles signing key must decode to ${expectedBytes} bytes`
    );
  }
  return key;
};

export const getExpiredPmtilesCookies = (
  config?: PmtilesCdnSessionConfigInput
) => {
  const resolvedConfig = getConfig(config);
  return RESTRICTED_PMTILES_TIERS.map(tier =>
    getExpiredPmtilesCookie(resolvedConfig.tierPaths[tier].path, resolvedConfig)
  );
};

const signedCookieValue = ({
  config,
  expires,
  key,
  urlPrefix
}: {
  config: PmtilesCdnSessionConfig;
  expires: number;
  key: Buffer;
  urlPrefix: string;
}) => {
  const encodedUrlPrefix = toUrlSafeBase64(urlPrefix);
  const policy = `URLPrefix=${encodedUrlPrefix}:Expires=${expires}:KeyName=${config.signingKeyName}`;
  const signature = toUrlSafeBase64(
    crypto.createHmac('sha1', key).update(policy).digest()
  );
  return `${policy}:Signature=${signature}`;
};

export type PmtilesSessionCookieGrant = {
  tier: PmtilesRestrictedTier;
  /**
   * When set, the cookie lifetime is clamped so CDN access cannot outlive
   * the grant that authorized it.
   */
  expiresAt?: Date | null;
};

/**
 * Signs one CDN session cookie per granted restricted tier. Each cookie is
 * scoped to its tier's cookie path and URL prefix, so browsers send the
 * right cookie per request and a cookie for one tier never validates
 * against another tier's URLs.
 */
export const getPmtilesSessionCookiesForTiers = (
  signingKey: Buffer,
  grants: PmtilesSessionCookieGrant[],
  config?: PmtilesCdnSessionConfigInput
) => {
  const resolvedConfig = getConfig(config);
  const nowSeconds = Math.floor(resolvedConfig.now() / 1000);

  return grants.map(grant => {
    const tierPath = resolvedConfig.tierPaths[grant.tier];
    const defaultExpires = nowSeconds + resolvedConfig.ttlSeconds;
    const grantExpires = grant.expiresAt
      ? Math.floor(grant.expiresAt.getTime() / 1000)
      : null;
    const expires =
      grantExpires !== null && grantExpires < defaultExpires
        ? grantExpires
        : defaultExpires;
    const maxAge = Math.max(expires - nowSeconds, 0);
    const value = signedCookieValue({
      config: resolvedConfig,
      expires,
      key: signingKey,
      urlPrefix: tierPath.urlPrefix
    });
    const expiresDate = new Date(expires * 1000).toUTCString();

    return `${resolvedConfig.cookieName}=${value}; ${getPmtilesCookieAttributes(tierPath.path, resolvedConfig)}; Max-Age=${maxAge}; Expires=${expiresDate}`;
  });
};

/** @deprecated Use `getPmtilesSessionCookiesForTiers` with explicit grants. */
export const getPrivatePmtilesSessionCookies = (
  signingKey: Buffer,
  config?: PmtilesCdnSessionConfigInput
) => getPmtilesSessionCookiesForTiers(signingKey, [{ tier: 'private' }], config);
