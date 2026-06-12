import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_PMTILES_ACCESS_POLICY,
  ensurePmtilesCdnSession,
  getPmtilesCdnGrants,
  getPmtilesFetchCredentials,
  getPmtilesTier,
  getViewerTierAuthorization,
  isRestrictedPmtilesUrl,
  isViewerAuthorizedForTier,
  RESTRICTED_PMTILES_TIERS
} from '@skytruth/shared-datasets';
import {
  createNextPmtilesSessionHandler,
  createPmtilesSessionHandler,
  DEFAULT_PMTILES_CDN_SESSION_TTL_SECONDS,
  getPmtilesSessionCookiesForTiers
} from '@skytruth/shared-datasets/server';

const NOW_MS = 1_700_000_000_000;
const now = () => NOW_MS;

const skytruthViewer = {
  email: 'analyst@skytruth.org',
  emailVerified: true
};

const guestViewer = (expiresAt = new Date(NOW_MS + 3_600_000)) => ({
  email: 'funder@example.org',
  emailVerified: true,
  tierGrants: [{ tier: 'internal', expiresAt }]
});

test('authorizes viewers per tier with one shared policy', () => {
  assert.deepEqual(DEFAULT_PMTILES_ACCESS_POLICY, {
    internalAllowedDomains: ['skytruth.org']
  });
  assert.deepEqual(RESTRICTED_PMTILES_TIERS, ['private', 'internal']);

  assert.equal(isViewerAuthorizedForTier('public', null), true);
  assert.equal(isViewerAuthorizedForTier('private', null), false);
  assert.equal(isViewerAuthorizedForTier('private', { email: null }), true);
  assert.equal(isViewerAuthorizedForTier(null, skytruthViewer), false);
  assert.equal(isViewerAuthorizedForTier('secret', skytruthViewer), false);

  assert.equal(isViewerAuthorizedForTier('internal', skytruthViewer), true);
  assert.equal(
    isViewerAuthorizedForTier('internal', {
      email: 'Analyst@SkyTruth.ORG',
      emailVerified: true
    }),
    true
  );
  assert.equal(
    isViewerAuthorizedForTier('internal', {
      email: 'analyst@skytruth.org',
      emailVerified: false
    }),
    false
  );
  assert.equal(
    isViewerAuthorizedForTier('internal', {
      email: 'someone@example.org',
      emailVerified: true
    }),
    false
  );
  assert.equal(
    isViewerAuthorizedForTier(
      'internal',
      { email: 'partner@partner.org', emailVerified: true },
      { internalAllowedDomains: ['partner.org'] }
    ),
    true
  );

  assert.deepEqual(
    getViewerTierAuthorization('internal', guestViewer(), undefined, now),
    { authorized: true, expiresAt: new Date(NOW_MS + 3_600_000) }
  );
  assert.deepEqual(
    getViewerTierAuthorization(
      'internal',
      guestViewer(new Date(NOW_MS - 1_000)),
      undefined,
      now
    ),
    { authorized: false }
  );
  assert.deepEqual(
    getViewerTierAuthorization('internal', guestViewer(null), undefined, now),
    { authorized: true, expiresAt: null }
  );
  assert.equal(
    isViewerAuthorizedForTier(
      'internal',
      { ...guestViewer(), emailVerified: false },
      undefined,
      now
    ),
    false
  );
  // Domain-authorized viewers carry no grant expiry to clamp to.
  assert.deepEqual(getViewerTierAuthorization('internal', skytruthViewer), {
    authorized: true
  });
});

test('clamps tier cookie lifetimes to grant expiry', () => {
  const signingKey = Buffer.alloc(16, 7);
  const config = { now };

  const cookies = getPmtilesSessionCookiesForTiers(
    signingKey,
    [
      { tier: 'private' },
      { tier: 'internal', expiresAt: new Date(NOW_MS + 3_600_000) }
    ],
    config
  );
  assert.equal(cookies.length, 2);
  assert.match(cookies[0], /Path=\/pmtiles\/private/);
  assert.match(
    cookies[0],
    new RegExp(`Max-Age=${DEFAULT_PMTILES_CDN_SESSION_TTL_SECONDS}`)
  );
  assert.match(cookies[1], /Path=\/pmtiles\/internal/);
  assert.match(cookies[1], /Max-Age=3600/);
  assert.match(cookies[1], /URLPrefix=/);
  assert.match(cookies[1], /Expires=1700003600:/);

  // A grant expiring after the default TTL must not extend the cookie.
  const longGrantCookie = getPmtilesSessionCookiesForTiers(
    signingKey,
    [
      {
        tier: 'internal',
        expiresAt: new Date(
          NOW_MS + (DEFAULT_PMTILES_CDN_SESSION_TTL_SECONDS + 100) * 1000
        )
      }
    ],
    config
  )[0];
  assert.match(
    longGrantCookie,
    new RegExp(`Max-Age=${DEFAULT_PMTILES_CDN_SESSION_TTL_SECONDS}`)
  );

  // An already-expired grant must not produce a future-dated cookie.
  const expiredGrantCookie = getPmtilesSessionCookiesForTiers(
    signingKey,
    [{ tier: 'internal', expiresAt: new Date(NOW_MS - 1_000) }],
    config
  )[0];
  assert.match(expiredGrantCookie, /Max-Age=0/);
});

const createHandler = ({ viewer = null, signingKey, ...options } = {}) =>
  createPmtilesSessionHandler({
    config: { now },
    getSigningKey: () => signingKey ?? Buffer.alloc(16, 7),
    getViewer: () => viewer,
    ...options
  });

test('session handler implements the CDN session contract', async () => {
  const anonymous = createHandler();

  const deleted = await anonymous({ method: 'DELETE', req: null });
  assert.equal(deleted.status, 204);
  assert.equal(deleted.headers['Cache-Control'], 'no-store');
  assert.equal(deleted.cookies.length, 2);
  assert.match(deleted.cookies[0], /Path=\/pmtiles\/private.*Max-Age=0/);
  assert.match(deleted.cookies[1], /Path=\/pmtiles\/internal.*Max-Age=0/);

  const methodNotAllowed = await anonymous({ method: 'PUT', req: null });
  assert.equal(methodNotAllowed.status, 405);
  assert.equal(methodNotAllowed.headers.Allow, 'GET, DELETE');

  const publicTier = await anonymous({
    method: 'GET',
    req: null,
    tierParam: 'public'
  });
  assert.equal(publicTier.status, 204);
  assert.deepEqual(publicTier.cookies, []);

  const invalidTier = await anonymous({
    method: 'GET',
    req: null,
    tierParam: 'secret'
  });
  assert.equal(invalidTier.status, 400);

  const missingTier = await anonymous({ method: 'GET', req: null });
  assert.equal(missingTier.status, 400);

  for (const tierParam of ['private', 'internal']) {
    const unauthenticated = await anonymous({
      method: 'GET',
      req: null,
      tierParam
    });
    assert.equal(unauthenticated.status, 401);
    assert.deepEqual(unauthenticated.cookies, []);
  }
});

test('session handler issues cookies per authorized tier', async () => {
  const outsiderViewer = { email: 'someone@example.org', emailVerified: true };

  const outsiderInternal = await createHandler({ viewer: outsiderViewer })({
    method: 'GET',
    req: null,
    tierParam: 'internal'
  });
  assert.equal(outsiderInternal.status, 403);
  assert.deepEqual(outsiderInternal.cookies, []);

  const outsiderPrivate = await createHandler({ viewer: outsiderViewer })({
    method: 'GET',
    req: null,
    tierParam: 'private'
  });
  assert.equal(outsiderPrivate.status, 204);
  assert.equal(outsiderPrivate.cookies.length, 1);
  assert.match(outsiderPrivate.cookies[0], /Path=\/pmtiles\/private/);

  const skytruthInternal = await createHandler({ viewer: skytruthViewer })({
    method: 'GET',
    req: null,
    tierParam: 'internal'
  });
  assert.equal(skytruthInternal.status, 204);
  assert.equal(skytruthInternal.cookies.length, 2);
  assert.match(skytruthInternal.cookies[0], /Path=\/pmtiles\/private/);
  assert.match(skytruthInternal.cookies[1], /Path=\/pmtiles\/internal/);
  assert.match(
    skytruthInternal.cookies[1],
    new RegExp(`Max-Age=${DEFAULT_PMTILES_CDN_SESSION_TTL_SECONDS}`)
  );

  const guestInternal = await createHandler({ viewer: guestViewer() })({
    method: 'GET',
    req: null,
    tierParam: 'internal'
  });
  assert.equal(guestInternal.status, 204);
  assert.equal(guestInternal.cookies.length, 2);
  assert.match(
    guestInternal.cookies[0],
    new RegExp(
      `Path=/pmtiles/private.*Max-Age=${DEFAULT_PMTILES_CDN_SESSION_TTL_SECONDS}`
    )
  );
  assert.match(guestInternal.cookies[1], /Path=\/pmtiles\/internal/);
  assert.match(guestInternal.cookies[1], /Max-Age=3600/);

  const expiredGuestInternal = await createHandler({
    viewer: guestViewer(new Date(NOW_MS - 1_000))
  })({ method: 'GET', req: null, tierParam: 'internal' });
  assert.equal(expiredGuestInternal.status, 403);
});

test('session handler reports grants without arming cookies', async () => {
  const anonymousGrants = await createHandler()({
    method: 'GET',
    req: null,
    tierParam: 'grants'
  });
  assert.equal(anonymousGrants.status, 200);
  assert.deepEqual(anonymousGrants.body, { tiers: ['public'] });
  assert.deepEqual(anonymousGrants.cookies, []);

  const skytruthGrants = await createHandler({ viewer: skytruthViewer })({
    method: 'GET',
    req: null,
    tierParam: 'grants'
  });
  assert.deepEqual(skytruthGrants.body, {
    tiers: ['public', 'private', 'internal']
  });
  assert.deepEqual(skytruthGrants.cookies, []);

  const outsiderGrants = await createHandler({
    viewer: { email: 'someone@example.org', emailVerified: true }
  })({ method: 'GET', req: null, tierParam: 'grants' });
  assert.deepEqual(outsiderGrants.body, { tiers: ['public', 'private'] });
});

test('session handler fails closed on signing errors without leaking', async () => {
  const errors = [];
  const failing = createPmtilesSessionHandler({
    config: { now },
    getSigningKey: () => {
      throw new Error('secret backend down');
    },
    getViewer: () => skytruthViewer,
    onError: (message, error) => errors.push({ error, message })
  });

  const result = await failing({
    method: 'GET',
    req: null,
    tierParam: 'private'
  });
  assert.equal(result.status, 500);
  assert.deepEqual(result.body, { error: 'Unable to issue PMTiles session' });
  assert.deepEqual(result.cookies, []);
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /secret backend down/);
});

test('session handler supports custom authorize overrides', async () => {
  const customized = createPmtilesSessionHandler({
    authorize: tier => tier === 'private',
    config: { now },
    getSigningKey: () => Buffer.alloc(16, 7),
    getViewer: () => skytruthViewer
  });

  const internal = await customized({
    method: 'GET',
    req: null,
    tierParam: 'internal'
  });
  assert.equal(internal.status, 403);

  const privateTier = await customized({
    method: 'GET',
    req: null,
    tierParam: 'private'
  });
  assert.equal(privateTier.status, 204);
  assert.equal(privateTier.cookies.length, 1);
});

const createFakeRes = () => {
  const headers = {};
  const result = { body: undefined, ended: false, statusCode: null };
  return {
    headers,
    result,
    setHeader: (name, value) => {
      headers[name] = value;
    },
    status: statusCode => {
      result.statusCode = statusCode;
      return {
        end: () => {
          result.ended = true;
        },
        json: body => {
          result.body = body;
        }
      };
    }
  };
};

test('Next.js adapter applies handler results to the response', async () => {
  const seenRequests = [];
  const handler = createNextPmtilesSessionHandler({
    config: { now },
    getSigningKey: () => Buffer.alloc(16, 7),
    getViewer: req => {
      seenRequests.push(req);
      return skytruthViewer;
    }
  });

  const okRes = createFakeRes();
  const okReq = { method: 'GET', query: { tier: 'internal' } };
  await handler(okReq, okRes);
  assert.equal(okRes.result.statusCode, 204);
  assert.equal(okRes.result.ended, true);
  assert.equal(okRes.headers['Cache-Control'], 'no-store');
  assert.equal(okRes.headers['Set-Cookie'].length, 2);
  assert.deepEqual(seenRequests, [okReq]);

  const badRes = createFakeRes();
  await handler({ method: 'GET', query: { tier: 'secret' } }, badRes);
  assert.equal(badRes.result.statusCode, 400);
  assert.deepEqual(badRes.result.body, { error: 'Invalid PMTiles tier' });
  assert.equal(badRes.headers['Set-Cookie'], undefined);
});

test('browser helpers handle internal tiers, denials, and grants probes', async () => {
  const calls = [];
  const fetchWithStatus = (status, body = null) => async (input, init) => {
    calls.push({ init, input: String(input) });
    return new Response(body, {
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      status
    });
  };

  assert.deepEqual(
    await ensurePmtilesCdnSession({
      accessTier: 'internal',
      endpoint: '/api/pmtiles/session',
      fetchImpl: fetchWithStatus(204)
    }),
    { ok: true, status: 204 }
  );
  assert.equal(calls.at(-1).input, '/api/pmtiles/session?tier=internal');
  assert.equal(calls.at(-1).init.credentials, 'include');

  assert.deepEqual(
    await ensurePmtilesCdnSession({
      accessTier: 'internal',
      endpoint: '/api/pmtiles/session',
      fetchImpl: fetchWithStatus(403)
    }),
    { denied: true, ok: false, status: 403 }
  );
  assert.deepEqual(
    await ensurePmtilesCdnSession({
      accessTier: 'private',
      endpoint: '/api/pmtiles/session',
      fetchImpl: fetchWithStatus(401)
    }),
    { ok: false, status: 401 }
  );

  assert.deepEqual(
    await getPmtilesCdnGrants({
      endpoint: '/api/pmtiles/session',
      fetchImpl: fetchWithStatus(
        200,
        JSON.stringify({ tiers: ['public', 'private'] })
      )
    }),
    { ok: true, status: 200, tiers: ['public', 'private'] }
  );
  assert.equal(calls.at(-1).input, '/api/pmtiles/session?tier=grants');

  const malformed = await getPmtilesCdnGrants({
    endpoint: '/api/pmtiles/session',
    fetchImpl: fetchWithStatus(200, JSON.stringify({}))
  });
  assert.equal(malformed.ok, false);
});

test('treats internal PMTiles URLs as restricted for fetch credentials', () => {
  assert.equal(
    isRestrictedPmtilesUrl(
      'https://tiles.skytruth.org/pmtiles/internal/example.pmtiles'
    ),
    true
  );
  assert.equal(
    isRestrictedPmtilesUrl(
      'https://tiles.skytruth.org/pmtiles/public/example.pmtiles'
    ),
    false
  );
  assert.equal(
    getPmtilesFetchCredentials(
      'https://tiles.skytruth.org/pmtiles/internal/example.pmtiles'
    ),
    'include'
  );
  assert.equal(
    getPmtilesFetchCredentials(
      'https://tiles.skytruth.org/pmtiles/private/example.pmtiles'
    ),
    'include'
  );
  assert.equal(
    getPmtilesFetchCredentials(
      'https://tiles.skytruth.org/pmtiles/public/example.pmtiles'
    ),
    'same-origin'
  );
  assert.equal(getPmtilesTier('internal'), 'internal');
});
