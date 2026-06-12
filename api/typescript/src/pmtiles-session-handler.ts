import {
  getPmtilesTier,
  type PmtilesTier,
  RESTRICTED_PMTILES_TIERS
} from './pmtiles-cdn-session.js';
import {
  getExpiredPmtilesCookies,
  getPmtilesSessionCookiesForTiers,
  type PmtilesCdnSessionConfigInput,
  type PmtilesSessionCookieGrant
} from './pmtiles-cdn-session-server.js';
import {
  getViewerTierAuthorization,
  type PmtilesAccessPolicy,
  type PmtilesTierAuthorization,
  type PmtilesViewer
} from './private-access.js';

export const PMTILES_SESSION_GRANTS_PARAM = 'grants';

export type PmtilesSessionAuthorize = (
  tier: PmtilesTier,
  viewer: PmtilesViewer | null
) =>
  | boolean
  | PmtilesTierAuthorization
  | Promise<boolean | PmtilesTierAuthorization>;

export type PmtilesSessionHandlerOptions<TReq> = {
  /** Resolve the signed-in viewer from the app's own auth/session store. */
  getViewer: (req: TReq) => Promise<PmtilesViewer | null> | PmtilesViewer | null;
  /** Read the CDN signing key from the app's secret store. Never logged. */
  getSigningKey: () => Promise<Buffer> | Buffer;
  policy?: Partial<PmtilesAccessPolicy>;
  config?: PmtilesCdnSessionConfigInput;
  /**
   * Override the default `getViewerTierAuthorization` policy. Return a
   * `PmtilesTierAuthorization` (rather than a bare boolean) to keep cookie
   * TTLs clamped to grant expiry.
   */
  authorize?: PmtilesSessionAuthorize;
  onError?: (message: string, error: unknown) => void;
};

export type PmtilesSessionHandlerResult = {
  status: number;
  headers: Record<string, string>;
  cookies: string[];
  body: { error: string } | { tiers: PmtilesTier[] } | null;
};

export type PmtilesSessionHandlerInput<TReq> = {
  method: string | undefined;
  tierParam: string | string[] | undefined;
  req: TReq;
};

const NO_STORE_HEADERS = { 'Cache-Control': 'no-store' };

const asTierAuthorization = (
  value: boolean | PmtilesTierAuthorization
): PmtilesTierAuthorization =>
  typeof value === 'boolean' ? { authorized: value } : value;

/**
 * Implements the full PMTiles CDN session contract from
 * `docs/pmtiles-cdn.md` once, framework-neutrally:
 *
 * - `Cache-Control: no-store` on every response.
 * - `DELETE` expires the cookie for every restricted tier (204).
 * - `GET ?tier=public` is a no-op success (204, no cookie).
 * - `GET ?tier=grants` reports which tiers the viewer qualifies for (200)
 *   without setting cookies.
 * - `GET ?tier=private|internal` returns 401 with no viewer, 403 for an
 *   unauthorized viewer, and otherwise signs one cookie per tier the viewer
 *   qualifies for (204), clamping each cookie's lifetime to the grant that
 *   authorized it.
 * - Unknown methods 405; unknown tiers 400; signing failures 500 without
 *   exposing or logging the key or cookie value.
 */
export const createPmtilesSessionHandler = <TReq = unknown>({
  authorize,
  config,
  getSigningKey,
  getViewer,
  onError = (message: string) => console.error(message),
  policy
}: PmtilesSessionHandlerOptions<TReq>) => {
  const resolveAuthorization = async (
    tier: PmtilesTier,
    viewer: PmtilesViewer | null
  ) =>
    asTierAuthorization(
      authorize
        ? await authorize(tier, viewer)
        : // Evaluate grant expiry on the same clock used to sign cookies.
          getViewerTierAuthorization(tier, viewer, policy, config?.now)
    );

  return async ({
    method,
    req,
    tierParam
  }: PmtilesSessionHandlerInput<TReq>): Promise<PmtilesSessionHandlerResult> => {
    if (method === 'DELETE') {
      return {
        body: null,
        cookies: getExpiredPmtilesCookies(config),
        headers: NO_STORE_HEADERS,
        status: 204
      };
    }

    if (method !== 'GET') {
      return {
        body: { error: 'Method not allowed' },
        cookies: [],
        headers: { ...NO_STORE_HEADERS, Allow: 'GET, DELETE' },
        status: 405
      };
    }

    const rawTier = Array.isArray(tierParam) ? tierParam[0] : tierParam;
    const isGrantsProbe = rawTier === PMTILES_SESSION_GRANTS_PARAM;
    const tier = isGrantsProbe ? null : getPmtilesTier(tierParam);
    if (!isGrantsProbe && !tier) {
      return {
        body: { error: 'Invalid PMTiles tier' },
        cookies: [],
        headers: NO_STORE_HEADERS,
        status: 400
      };
    }

    if (tier === 'public') {
      return { body: null, cookies: [], headers: NO_STORE_HEADERS, status: 204 };
    }

    const viewer = await getViewer(req);

    if (isGrantsProbe) {
      const tiers: PmtilesTier[] = ['public'];
      for (const restrictedTier of RESTRICTED_PMTILES_TIERS) {
        const { authorized } = await resolveAuthorization(
          restrictedTier,
          viewer
        );
        if (authorized) tiers.push(restrictedTier);
      }
      return {
        body: { tiers },
        cookies: [],
        headers: NO_STORE_HEADERS,
        status: 200
      };
    }

    if (!viewer) {
      return {
        body: { error: 'Authentication required' },
        cookies: [],
        headers: NO_STORE_HEADERS,
        status: 401
      };
    }

    const grants: PmtilesSessionCookieGrant[] = [];
    let requestedTierAuthorized = false;
    for (const restrictedTier of RESTRICTED_PMTILES_TIERS) {
      const authorization = await resolveAuthorization(restrictedTier, viewer);
      if (!authorization.authorized) continue;
      if (restrictedTier === tier) requestedTierAuthorized = true;
      grants.push({
        expiresAt: authorization.expiresAt ?? null,
        tier: restrictedTier
      });
    }

    if (!requestedTierAuthorized) {
      return {
        body: { error: 'Not authorized for this PMTiles tier' },
        cookies: [],
        headers: NO_STORE_HEADERS,
        status: 403
      };
    }

    try {
      const signingKey = await getSigningKey();
      return {
        body: null,
        cookies: getPmtilesSessionCookiesForTiers(signingKey, grants, config),
        headers: NO_STORE_HEADERS,
        status: 204
      };
    } catch (error) {
      onError(
        `Unable to issue PMTiles CDN session: ${
          error instanceof Error ? error.message : String(error)
        }`,
        error
      );
      return {
        body: { error: 'Unable to issue PMTiles session' },
        cookies: [],
        headers: NO_STORE_HEADERS,
        status: 500
      };
    }
  };
};

export type NextApiRequestLike = {
  method?: string;
  query: Partial<Record<string, string | string[]>>;
};

export type NextApiResponseLike = {
  setHeader(name: string, value: string | string[]): unknown;
  status(statusCode: number): {
    json(body: unknown): unknown;
    end(): unknown;
  };
};

/**
 * Next.js pages-router adapter. A consumer's whole session route becomes:
 *
 * ```ts
 * export default createNextPmtilesSessionHandler({
 *   getViewer: async req => (await getAPISession(req)).data?.user ?? null,
 *   getSigningKey: getPMTilesCDNSigningKey
 * });
 * ```
 */
export const createNextPmtilesSessionHandler = <
  TReq extends NextApiRequestLike
>(
  options: PmtilesSessionHandlerOptions<TReq>
) => {
  const handler = createPmtilesSessionHandler(options);

  return async (req: TReq, res: NextApiResponseLike) => {
    const result = await handler({
      method: req.method,
      req,
      tierParam: req.query.tier
    });

    Object.entries(result.headers).forEach(([name, value]) =>
      res.setHeader(name, value)
    );
    if (result.cookies.length) res.setHeader('Set-Cookie', result.cookies);

    const response = res.status(result.status);
    return result.body === null ? response.end() : response.json(result.body);
  };
};
