import type { PmtilesTier } from './pmtiles-cdn-session.js';

type PmtilesCdnSessionFetch = (
  input: RequestInfo | URL,
  init?: RequestInit
) => Promise<Response>;

export type PmtilesCdnSessionResult =
  | { ok: true; status?: number }
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
  if (accessTier !== 'private') {
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
      withQueryParam(endpoint, 'tier', 'private'),
      {
        credentials: 'include',
        method: 'GET'
      }
    );

    return response.ok
      ? { ok: true, status: response.status }
      : { ok: false, status: response.status };
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
