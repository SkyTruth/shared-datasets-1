import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import {
  DEFAULT_ACCESS_TIER_CACHE_TTL_MS,
  DEFAULT_PMTILES_PRIVATE_PATH_PREFIX,
  DEFAULT_PMTILES_URL_BASE,
  DEFAULT_SHARED_DATASETS_CATALOG_JSON_URL,
  SharedDatasetCatalogResolutionError,
  clearPmtilesCdnSession,
  createSharedDatasetAccessTierLookup,
  fetchSharedDatasetCatalogJson,
  getAccessTiersFromSharedDatasetPmtilesRefs,
  getPmtilesFetchCredentials,
  getPmtilesTier,
  getSharedDatasetAccessTier,
  isPrivatePmtilesUrl,
  normalizeSharedDatasetAssetSlug,
  parseSharedDatasetsCatalogJson,
  resolveAllSharedDatasetPmtilesRefs,
  resolveSharedDatasetPmtilesRef,
  resolveSharedDatasetPmtilesRefs,
  resolveSharedDatasetPmtilesRefsFromCatalogJson,
  ensurePmtilesCdnSession
} from '@skytruth/shared-datasets';
import {
  DEFAULT_PMTILES_CDN_SESSION_CONFIG,
  decodePmtilesCdnSigningKey,
  getExpiredPmtilesCookies,
  getPrivatePmtilesSessionCookies
} from '@skytruth/shared-datasets/server';
import * as mainEntrypoint from '@skytruth/shared-datasets';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = path.resolve(__dirname, '..');

const toUrlSafeBase64 = value =>
  Buffer.from(value).toString('base64').replace(/\+/g, '-').replace(/\//g, '_');

const catalogFixture = {
  assets: [
    {
      access_tier: ' public ',
      citation: ' Example citation ',
      consumer_guidance: ' Keep this layer visible by default. ',
      description: ' Example description ',
      docs_url: ' docs/assets/example-public-layer.md ',
      has_pmtiles: true,
      last_updated: ' 2026-01-15 ',
      latest_release: { date: '2026-01-15' },
      localized_names: {
        storage: 'localization_csv_v1',
        join_key: 'ext_id',
        localization_file: 'latest/example-public-layer-localizations.csv',
        property_template: 'name_{locale_code}',
        locale_code_format: 'bcp47_field_safe',
        fallback_field: 'name',
        available_locales: ['en', 'es'],
        translations: [
          {
            locale_code: 'en',
            field: 'name_en',
            review_state_field: 'name_en_review_state',
            label: 'English',
            review_state: 'source_provided'
          },
          {
            locale_code: 'es',
            field: 'name_es',
            review_state_field: 'name_es_review_state',
            label: 'Spanish',
            review_state: 'mixed'
          }
        ]
      },
      license: ' Example license ',
      pmtiles_url: ' https://tiles.skytruth.org/pmtiles/public/example-public-layer.pmtiles ',
      release_index_url: ' ../releases/example-public-layer.json ',
      slug: ' Example-Public-Layer ',
      source: ' Example source ',
      source_url: ' https://example.test/source ',
      status: ' active ',
      title: ' Example public layer '
    },
    {
      access_tier: 'private',
      asset_slug: 'example-private-layer',
      available_formats: 'fgb; pmtiles',
      pmtiles_url:
        'https://tiles.skytruth.org/pmtiles/private/example-private-layer.pmtiles',
      title: ''
    },
    {
      access_tier: 'public',
      asset_slug: 'example-table',
      available_formats: 'csv',
      pmtiles_url: null
    },
    {
      access_tier: 'internal',
      asset_slug: 'invalid-tier-layer',
      has_pmtiles: 'true',
      pmtiles_url:
        'https://tiles.skytruth.org/pmtiles/private/invalid-tier-layer.pmtiles'
    }
  ]
};

const getPortableFiles = async dir => {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(entry => {
      const entryPath = path.join(dir, entry.name);
      return entry.isDirectory()
        ? getPortableFiles(entryPath)
        : /\.(ts|md|json|mjs)$/.test(entry.name)
          ? [entryPath]
          : [];
    })
  );
  return files.flat();
};

test('keeps SkyTruth PMTiles CDN and catalog defaults', () => {
  assert.equal(
    DEFAULT_SHARED_DATASETS_CATALOG_JSON_URL,
    'https://tiles.skytruth.org/_catalog/web/catalog.json'
  );
  assert.equal(DEFAULT_PMTILES_URL_BASE, 'https://tiles.skytruth.org');
  assert.equal(DEFAULT_PMTILES_PRIVATE_PATH_PREFIX, '/pmtiles/private/');
  assert.equal(DEFAULT_ACCESS_TIER_CACHE_TTL_MS, 5 * 60 * 1000);
  assert.deepEqual(DEFAULT_PMTILES_CDN_SESSION_CONFIG, {
    cookieDomain: '.skytruth.org',
    cookieName: 'Cloud-CDN-Cookie',
    legacyPath: '/pmtiles',
    now: DEFAULT_PMTILES_CDN_SESSION_CONFIG.now,
    privatePath: '/pmtiles/private',
    privateUrlPrefix: 'https://tiles.skytruth.org/pmtiles/private/',
    signingKeyName: 'shared-datasets-pmtiles-v1',
    ttlSeconds: 24 * 60 * 60
  });
  assert.deepEqual(getExpiredPmtilesCookies(), [
    'Cloud-CDN-Cookie=; Domain=.skytruth.org; Path=/pmtiles; Secure; HttpOnly; SameSite=None; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT',
    'Cloud-CDN-Cookie=; Domain=.skytruth.org; Path=/pmtiles/private; Secure; HttpOnly; SameSite=None; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT'
  ]);

  const privateCookie = getPrivatePmtilesSessionCookies(Buffer.alloc(16, 1), {
    now: () => 1_700_000_000_000
  })[1];
  assert.match(privateCookie, /^Cloud-CDN-Cookie=/);
  assert.match(privateCookie, /Path=\/pmtiles\/private/);
  assert.match(privateCookie, /Max-Age=86400/);
  assert.match(privateCookie, /KeyName=shared-datasets-pmtiles-v1/);
});

test('creates signed private PMTiles CDN cookies and clears legacy cookies', () => {
  const signingKey = Buffer.alloc(16, 7);
  const config = {
    cookieName: 'Test-CDN-Cookie',
    cookieDomain: '.example.org',
    legacyPath: '/pmtiles',
    privatePath: '/pmtiles/private',
    ttlSeconds: 60,
    privateUrlPrefix: 'https://tiles.example.org/pmtiles/private/',
    signingKeyName: 'test-key',
    now: () => 1_700_000_000_000
  };

  assert.deepEqual(getExpiredPmtilesCookies(config), [
    'Test-CDN-Cookie=; Domain=.example.org; Path=/pmtiles; Secure; HttpOnly; SameSite=None; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT',
    'Test-CDN-Cookie=; Domain=.example.org; Path=/pmtiles/private; Secure; HttpOnly; SameSite=None; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT'
  ]);

  const cookies = getPrivatePmtilesSessionCookies(signingKey, config);
  assert.equal(cookies.length, 2);
  assert.equal(
    cookies[0],
    'Test-CDN-Cookie=; Domain=.example.org; Path=/pmtiles; Secure; HttpOnly; SameSite=None; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT'
  );

  const privateCookie = cookies[1];
  const cookieValue = privateCookie.match(/^Test-CDN-Cookie=([^;]+);/)?.[1];
  assert.ok(cookieValue);

  const encodedPrefix = toUrlSafeBase64(config.privateUrlPrefix);
  const expectedPolicy = `URLPrefix=${encodedPrefix}:Expires=1700000060:KeyName=test-key`;
  const expectedSignature = toUrlSafeBase64(
    crypto.createHmac('sha1', signingKey).update(expectedPolicy).digest()
  );
  assert.equal(
    cookieValue,
    `${expectedPolicy}:Signature=${expectedSignature}`
  );
  assert.match(privateCookie, /Path=\/pmtiles\/private/);
  assert.match(privateCookie, /Max-Age=60/);
  assert.match(
    privateCookie,
    new RegExp(`Expires=${new Date(1_700_000_060_000).toUTCString()}`)
  );
});

test('decodes and validates PMTiles CDN signing keys', () => {
  const expectedKey = Buffer.alloc(16, 3);
  const encodedKey = toUrlSafeBase64(expectedKey);

  assert.equal(Buffer.compare(decodePmtilesCdnSigningKey(encodedKey), expectedKey), 0);
  assert.throws(() => decodePmtilesCdnSigningKey(''), /secret version is empty/);
  assert.throws(
    () => decodePmtilesCdnSigningKey(toUrlSafeBase64(Buffer.alloc(15, 3))),
    /16 bytes/
  );
  assert.equal(
    Buffer.compare(
      decodePmtilesCdnSigningKey(toUrlSafeBase64(Buffer.alloc(8, 5)), {
        expectedBytes: 8
      }),
      Buffer.alloc(8, 5)
    ),
    0
  );
});

test('detects private PMTiles URLs and fetch credential mode', () => {
  assert.equal(isPrivatePmtilesUrl('/pmtiles/private/a.pmtiles'), true);
  assert.equal(
    isPrivatePmtilesUrl(
      'https://tiles.skytruth.org/pmtiles/private/a.pmtiles'
    ),
    true
  );
  assert.equal(
    isPrivatePmtilesUrl('https://example.org/pmtiles/private/a.pmtiles'),
    false
  );
  assert.equal(isPrivatePmtilesUrl('/pmtiles/public/a.pmtiles'), false);
  assert.equal(isPrivatePmtilesUrl('https://tiles.skytruth.org/other'), false);
  assert.equal(isPrivatePmtilesUrl('http://['), false);
  assert.equal(getPmtilesFetchCredentials('/pmtiles/private/a.pmtiles'), 'include');
  assert.equal(getPmtilesFetchCredentials('/pmtiles/public/a.pmtiles'), 'same-origin');
  assert.equal(
    getPmtilesFetchCredentials('https://example.org/secure/a.pmtiles', {
      baseUrl: 'https://example.org',
      privatePathPrefix: '/secure/'
    }),
    'include'
  );
});

test('parses PMTiles access tiers', () => {
  assert.equal(getPmtilesTier('public'), 'public');
  assert.equal(getPmtilesTier(['private', 'public']), 'private');
  assert.equal(getPmtilesTier('PRIVATE'), null);
  assert.equal(getPmtilesTier(undefined), null);
  assert.equal(getSharedDatasetAccessTier(' Public '), 'public');
  assert.equal(getSharedDatasetAccessTier('internal'), null);
});

test('performs browser PMTiles CDN session handshakes', async () => {
  const calls = [];
  const okFetch = async (input, init) => {
    calls.push({ input: String(input), init });
    return new Response(null, { status: 204 });
  };

  assert.deepEqual(
    await ensurePmtilesCdnSession({
      accessTier: 'public',
      endpoint: '/api/pmtiles/session',
      fetchImpl: okFetch
    }),
    { ok: true }
  );
  assert.equal(calls.length, 0);

  assert.deepEqual(
    await ensurePmtilesCdnSession({
      accessTier: 'private',
      endpoint: '/api/pmtiles/session',
      fetchImpl: okFetch
    }),
    { ok: true, status: 204 }
  );
  assert.deepEqual(calls[0], {
    input: '/api/pmtiles/session?tier=private',
    init: {
      credentials: 'include',
      method: 'GET'
    }
  });

  calls.length = 0;
  assert.deepEqual(
    await ensurePmtilesCdnSession({
      accessTier: 'private',
      endpoint: '/api/pmtiles/session?source=layer',
      fetchImpl: okFetch
    }),
    { ok: true, status: 204 }
  );
  assert.equal(calls[0].input, '/api/pmtiles/session?source=layer&tier=private');

  calls.length = 0;
  assert.deepEqual(
    await ensurePmtilesCdnSession({
      accessTier: 'private',
      endpoint: '/api/pmtiles/session?tier=public&source=layer',
      fetchImpl: okFetch
    }),
    { ok: true, status: 204 }
  );
  assert.equal(calls[0].input, '/api/pmtiles/session?tier=private&source=layer');

  calls.length = 0;
  assert.deepEqual(
    await ensurePmtilesCdnSession({
      accessTier: 'private',
      endpoint: '/api/pmtiles/session?source=layer#frag',
      fetchImpl: okFetch
    }),
    { ok: true, status: 204 }
  );
  assert.equal(calls[0].input, '/api/pmtiles/session?source=layer&tier=private#frag');

  assert.deepEqual(
    await ensurePmtilesCdnSession({
      accessTier: 'private',
      endpoint: '/api/pmtiles/session',
      fetchImpl: async () => new Response(null, { status: 401 })
    }),
    { ok: false, status: 401 }
  );

  const invalidTier = await ensurePmtilesCdnSession({
    accessTier: null,
    endpoint: '/api/pmtiles/session',
    fetchImpl: okFetch
  });
  assert.equal(invalidTier.ok, false);
  assert.match(invalidTier.error.message, /Invalid PMTiles access tier/);

  const networkFailure = await ensurePmtilesCdnSession({
    accessTier: 'private',
    endpoint: '/api/pmtiles/session',
    fetchImpl: async () => {
      throw new Error('network failed');
    }
  });
  assert.equal(networkFailure.ok, false);
  assert.match(networkFailure.error.message, /network failed/);

  const originalFetch = globalThis.fetch;
  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: undefined,
    writable: true
  });
  try {
    const missingFetch = await ensurePmtilesCdnSession({
      accessTier: 'private',
      endpoint: '/api/pmtiles/session'
    });
    assert.equal(missingFetch.ok, false);
    assert.match(missingFetch.error.message, /No fetch implementation/);
  } finally {
    Object.defineProperty(globalThis, 'fetch', {
      configurable: true,
      value: originalFetch,
      writable: true
    });
  }

  calls.length = 0;
  assert.deepEqual(
    await clearPmtilesCdnSession({
      endpoint: '/api/pmtiles/session',
      fetchImpl: okFetch
    }),
    { ok: true, status: 204 }
  );
  assert.deepEqual(calls[0], {
    input: '/api/pmtiles/session',
    init: {
      credentials: 'include',
      method: 'DELETE'
    }
  });
  const clearFailure = await clearPmtilesCdnSession({
    endpoint: '/api/pmtiles/session',
    fetchImpl: async () => {
      throw new Error('network failed');
    }
  });
  assert.equal(clearFailure.ok, false);
  assert.match(clearFailure.error.message, /network failed/);
});

test('resolves PMTiles refs from shared-datasets catalog JSON', async () => {
  assert.deepEqual(parseSharedDatasetsCatalogJson(catalogFixture), catalogFixture);
  assert.throws(
    () => parseSharedDatasetsCatalogJson({ rows: [] }),
    SharedDatasetCatalogResolutionError
  );
  assert.equal(
    normalizeSharedDatasetAssetSlug(' Example-Public-Layer '),
    'example-public-layer'
  );

  const refs = resolveSharedDatasetPmtilesRefsFromCatalogJson(catalogFixture);
  assert.deepEqual(Object.keys(refs).sort(), [
    'example-private-layer',
    'example-public-layer'
  ]);
  assert.deepEqual(refs['example-public-layer'], {
    accessTier: 'public',
    citation: 'Example citation',
    consumerGuidance: 'Keep this layer visible by default.',
    description: 'Example description',
    docsUrl: 'docs/assets/example-public-layer.md',
    lastUpdated: '2026-01-15',
    latestRelease: { date: '2026-01-15' },
    license: 'Example license',
    localizedNames: {
      storage: 'localization_csv_v1',
      join_key: 'ext_id',
      localization_file: 'latest/example-public-layer-localizations.csv',
      property_template: 'name_{locale_code}',
      locale_code_format: 'bcp47_field_safe',
      fallback_field: 'name',
      available_locales: ['en', 'es'],
      translations: [
        {
          locale_code: 'en',
          field: 'name_en',
          review_state_field: 'name_en_review_state',
          label: 'English',
          review_state: 'source_provided'
        },
        {
          locale_code: 'es',
          field: 'name_es',
          review_state_field: 'name_es_review_state',
          label: 'Spanish',
          review_state: 'mixed'
        }
      ]
    },
    releaseIndexUrl: '../releases/example-public-layer.json',
    source: 'Example source',
    sourceUrl: 'https://example.test/source',
    status: 'active',
    title: 'Example public layer',
    url: 'https://tiles.skytruth.org/pmtiles/public/example-public-layer.pmtiles'
  });
  assert.equal(refs['example-private-layer'].title, null);

  assert.deepEqual(
    resolveSharedDatasetPmtilesRefsFromCatalogJson(catalogFixture, [
      ' example-private-layer '
    ]),
    {
      'example-private-layer': refs['example-private-layer']
    }
  );
  assert.deepEqual(
    resolveSharedDatasetPmtilesRefsFromCatalogJson(catalogFixture, [null, ' ']),
    {}
  );
  assert.throws(
    () =>
      resolveSharedDatasetPmtilesRefsFromCatalogJson(catalogFixture, [
        'missing-layer'
      ]),
    /missing-layer/
  );

  const fetched = await fetchSharedDatasetCatalogJson({
    catalogUrl: 'https://example.test/catalog.json',
    fetchJson: async url => {
      assert.equal(url, 'https://example.test/catalog.json');
      return catalogFixture;
    }
  });
  assert.equal(fetched, catalogFixture);
  await assert.rejects(
    () =>
      fetchSharedDatasetCatalogJson({
        fetchJson: async () => {
          throw new Error('offline');
        }
      }),
    /Unable to load shared datasets catalog: offline/
  );

  assert.deepEqual(
    await resolveSharedDatasetPmtilesRefs(['example-public-layer'], {
      fetchJson: async () => catalogFixture
    }),
    {
      'example-public-layer': refs['example-public-layer']
    }
  );
  assert.deepEqual(
    await resolveAllSharedDatasetPmtilesRefs({
      fetchJson: async () => catalogFixture
    }),
    refs
  );
  assert.deepEqual(
    await resolveSharedDatasetPmtilesRef('example-private-layer', {
      fetchJson: async () => catalogFixture
    }),
    refs['example-private-layer']
  );
  await assert.rejects(
    () => resolveSharedDatasetPmtilesRef('', { fetchJson: async () => catalogFixture }),
    /missing asset_slug/
  );
});

test('caches shared dataset access-tier lookups', async () => {
  const refs = resolveSharedDatasetPmtilesRefsFromCatalogJson(catalogFixture);
  assert.deepEqual(getAccessTiersFromSharedDatasetPmtilesRefs(refs), {
    'example-private-layer': 'private',
    'example-public-layer': 'public'
  });

  let currentTime = 1_000;
  let loadCount = 0;
  const lookup = createSharedDatasetAccessTierLookup({
    loadAccessTiers: async () => {
      loadCount += 1;
      return loadCount === 1
        ? { 'example-public-layer': 'public' }
        : { 'example-public-layer': 'private' };
    },
    now: () => currentTime,
    ttlMs: 100
  });

  assert.equal(await lookup('example-public-layer'), 'public');
  assert.equal(await lookup('example-public-layer'), 'public');
  assert.equal(loadCount, 1);
  currentTime = 1_101;
  assert.equal(await lookup('example-public-layer'), 'private');
  assert.equal(loadCount, 2);
  await assert.rejects(() => lookup('missing-layer'), /missing-layer/);
});

test('main package entrypoint stays browser safe', async () => {
  assert.equal('decodePmtilesCdnSigningKey' in mainEntrypoint, false);
  assert.equal('getPrivatePmtilesSessionCookies' in mainEntrypoint, false);

  const distIndex = await readFile(path.join(packageRoot, 'dist/index.js'), 'utf8');
  assert.doesNotMatch(distIndex, /node:crypto/);
  assert.doesNotMatch(distIndex, /pmtiles-cdn-session-server/);
});

test('package source and docs do not include consumer-specific or dataset-specific coupling', async () => {
  const files = [
    ...(await getPortableFiles(path.join(packageRoot, 'src'))),
    path.join(packageRoot, 'README.md')
  ];
  const matches = [];
  const disallowed =
    /\b(Cerulean|AOI|wdpa|gfw|iucn|eamlis|petrodata|gogi|natural-earth|marine-regions|global-coral)\b/i;

  await Promise.all(
    files.map(async sourceFile => {
      const source = await readFile(sourceFile, 'utf8');
      if (disallowed.test(source)) {
        matches.push(path.relative(packageRoot, sourceFile));
      }
    })
  );

  assert.deepEqual(matches.sort(), []);
});

test('package source and docs avoid internal deployment details', async () => {
  const files = [
    ...(await getPortableFiles(path.join(packageRoot, 'src'))),
    path.join(packageRoot, 'README.md')
  ];
  const matches = [];
  const disallowed =
    /projects\/shared-datasets-1\/secrets\/|gs:\/\/skytruth-shared-datasets-1|pmtiles-cdn-signed-request-key|iam\.gserviceaccount\.com|serviceAccount:|notificationChannels\/|pmtiles_cdn_allowed_origin|shared-datasets-breakglass|jona@skytruth\.org|\b734798842681\b|\b12695949518\b|terraform\/envs|\.github\/workflows|\.claude\/skills|AGENTS\.md/i;

  await Promise.all(
    files.map(async sourceFile => {
      const source = await readFile(sourceFile, 'utf8');
      if (disallowed.test(source)) {
        matches.push(path.relative(packageRoot, sourceFile));
      }
    })
  );

  assert.deepEqual(matches.sort(), []);
});
