import crypto from 'node:crypto';

export type PmtilesCdnSessionConfig = {
  cookieName: string;
  cookieDomain: string;
  legacyPath: string;
  privatePath: string;
  ttlSeconds: number;
  privateUrlPrefix: string;
  signingKeyName: string;
  now: () => number;
};

export type PmtilesCdnSigningKeyDecodeOptions = {
  expectedBytes?: number;
};

export const DEFAULT_PMTILES_CDN_SIGNING_KEY_BYTES = 16;

export const DEFAULT_PMTILES_CDN_SESSION_CONFIG: PmtilesCdnSessionConfig = {
  cookieName: 'Cloud-CDN-Cookie',
  cookieDomain: '.skytruth.org',
  legacyPath: '/pmtiles',
  privatePath: '/pmtiles/private',
  ttlSeconds: 24 * 60 * 60,
  privateUrlPrefix: 'https://tiles.skytruth.org/pmtiles/private/',
  signingKeyName: 'shared-datasets-pmtiles-v1',
  now: Date.now
};

const getConfig = (
  config: Partial<PmtilesCdnSessionConfig> = {}
): PmtilesCdnSessionConfig => {
  const ttlSeconds = Number(config.ttlSeconds);
  return {
    ...DEFAULT_PMTILES_CDN_SESSION_CONFIG,
    ...config,
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
  config?: Partial<PmtilesCdnSessionConfig>
) => {
  const resolvedConfig = getConfig(config);
  return [
    getExpiredPmtilesCookie(resolvedConfig.legacyPath, resolvedConfig),
    getExpiredPmtilesCookie(resolvedConfig.privatePath, resolvedConfig)
  ];
};

const signedCookieValue = ({
  config,
  expires,
  key
}: {
  config: PmtilesCdnSessionConfig;
  expires: number;
  key: Buffer;
}) => {
  const encodedUrlPrefix = toUrlSafeBase64(config.privateUrlPrefix);
  const policy = `URLPrefix=${encodedUrlPrefix}:Expires=${expires}:KeyName=${config.signingKeyName}`;
  const signature = toUrlSafeBase64(
    crypto.createHmac('sha1', key).update(policy).digest()
  );
  return `${policy}:Signature=${signature}`;
};

export const getPrivatePmtilesSessionCookies = (
  signingKey: Buffer,
  config?: Partial<PmtilesCdnSessionConfig>
) => {
  const resolvedConfig = getConfig(config);
  const expires =
    Math.floor(resolvedConfig.now() / 1000) + resolvedConfig.ttlSeconds;
  const value = signedCookieValue({
    config: resolvedConfig,
    expires,
    key: signingKey
  });
  const expiresDate = new Date(expires * 1000).toUTCString();

  return [
    getExpiredPmtilesCookie(resolvedConfig.legacyPath, resolvedConfig),
    `${resolvedConfig.cookieName}=${value}; ${getPmtilesCookieAttributes(resolvedConfig.privatePath, resolvedConfig)}; Max-Age=${resolvedConfig.ttlSeconds}; Expires=${expiresDate}`
  ];
};
