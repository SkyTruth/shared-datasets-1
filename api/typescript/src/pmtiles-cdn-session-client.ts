import type { PmtilesTier } from './pmtiles-cdn-session.js';

type PmtilesCdnSessionFetch = (
  input: RequestInfo | URL,
  init?: RequestInit
) => Promise<Response>;

export type PmtilesCdnSessionResult =
  | { ok: true; status?: number }
  | {
      ok: false;
      status?: number;
      error?: unknown;
      /**
       * True when the backend definitively refused this viewer (HTTP 403).
       * Hide the layer instead of retrying.
       */
      denied?: boolean;
    };

export type PmtilesCdnGrantsResult =
  | { ok: true; status?: number; tiers: PmtilesTier[] }
  | { ok: false; status?: number; error?: unknown };

export type EnsurePmtilesCdnSessionOptions = {
  accessTier: PmtilesTier | null | undefined;
  endpoint: string;
  fetchImpl?: PmtilesCdnSessionFetch;
};

export type ClearPmtilesCdnSessionOptions = {
  endpoint: string;
  fetchImpl?: PmtilesCdnSessionFetch;
};

export type GetPmtilesCdnGrantsOptions = {
  endpoint: string;
  fetchImpl?: PmtilesCdnSessionFetch;
};

const getFetchImpl = (fetchImpl?: PmtilesCdnSessionFetch) => {
  return fetchImpl ?? globalThis.fetch?.bind(globalThis) ?? null;
};

const withQueryParam = (endpoint: string, key: string, value: string) => {
  const hashIndex = endpoint.indexOf('#');
  const base = hashIndex >= 0 ? endpoint.slice(0, hashIndex) : endpoint;
  const hash = hashIndex >= 0 ? endpoint.slice(hashIndex) : '';
  const queryIndex = base.indexOf('?');
  const path = queryIndex >= 0 ? base.slice(0, queryIndex) : base;
  const query = queryIndex >= 0 ? base.slice(queryIndex + 1) : '';
  const params = new URLSearchParams(query);
  params.set(key, value);
  return `${path}?${params.toString()}${hash}`;
};

export const ensurePmtilesCdnSession = async ({
  accessTier,
  endpoint,
  fetchImpl
}: EnsurePmtilesCdnSessionOptions): Promise<PmtilesCdnSessionResult> => {
  if (accessTier === 'public') return { ok: true };
  if (accessTier !== 'private' && accessTier !== 'internal') {
    return {
      error: new Error('Invalid PMTiles access tier'),
      ok: false
    };
  }

  const resolvedFetch = getFetchImpl(fetchImpl);
  if (!resolvedFetch) {
    return {
      error: new Error('No fetch implementation is available'),
      ok: false
    };
  }

  try {
    const response = await resolvedFetch(
      withQueryParam(endpoint, 'tier', accessTier),
      {
        credentials: 'include',
        method: 'GET'
      }
    );

    if (response.ok) return { ok: true, status: response.status };
    return response.status === 403
      ? { denied: true, ok: false, status: response.status }
      : { ok: false, status: response.status };
  } catch (error) {
    return { error, ok: false };
  }
};

/**
 * Asks the session endpoint which tiers the current viewer qualifies for
 * (`?tier=grants`) without arming any cookies, so UIs can decide which
 * datasets and layers to offer before mounting anything.
 */
export const getPmtilesCdnGrants = async ({
  endpoint,
  fetchImpl
}: GetPmtilesCdnGrantsOptions): Promise<PmtilesCdnGrantsResult> => {
  const resolvedFetch = getFetchImpl(fetchImpl);
  if (!resolvedFetch) {
    return {
      error: new Error('No fetch implementation is available'),
      ok: false
    };
  }

  try {
    const response = await resolvedFetch(
      withQueryParam(endpoint, 'tier', 'grants'),
      {
        credentials: 'include',
        method: 'GET'
      }
    );
    if (!response.ok) return { ok: false, status: response.status };

    const body = (await response.json()) as { tiers?: unknown };
    const tiers = Array.isArray(body?.tiers)
      ? body.tiers.filter(
          (tier): tier is PmtilesTier =>
            tier === 'public' || tier === 'private' || tier === 'internal'
        )
      : null;
    if (!tiers) {
      return {
        error: new Error('Malformed PMTiles grants response'),
        ok: false,
        status: response.status
      };
    }
    return { ok: true, status: response.status, tiers };
  } catch (error) {
    return { error, ok: false };
  }
};

export const clearPmtilesCdnSession = async ({
  endpoint,
  fetchImpl
}: ClearPmtilesCdnSessionOptions): Promise<PmtilesCdnSessionResult> => {
  const resolvedFetch = getFetchImpl(fetchImpl);
  if (!resolvedFetch) {
    return {
      error: new Error('No fetch implementation is available'),
      ok: false
    };
  }

  try {
    const response = await resolvedFetch(endpoint, {
      credentials: 'include',
      method: 'DELETE'
    });
    return response.ok
      ? { ok: true, status: response.status }
      : { ok: false, status: response.status };
  } catch (error) {
    return { error, ok: false };
  }
};
