import { test as base, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { baseURL, packageDir, siteDir, workDir } from './paths.mjs';

const readJSON = async (path) => JSON.parse(await readFile(path, 'utf8'));
// Neutral raster tile preserves the real basemap layer without external traffic.
const basemapPNG = await readFile(resolve(packageDir, 'fixtures/basemap.png'));
const libraries = new Map([
  ['https://unpkg.com/maplibre-gl@5.9.0/dist/maplibre-gl.js', ['maplibre-gl/dist/maplibre-gl.js', 'text/javascript']],
  ['https://unpkg.com/maplibre-gl@5.9.0/dist/maplibre-gl.css', ['maplibre-gl/dist/maplibre-gl.css', 'text/css']],
  ['https://unpkg.com/pmtiles@4.3.0/dist/pmtiles.js', ['pmtiles/dist/pmtiles.js', 'text/javascript']],
]);

const test = base.extend({
  transport: [async ({ browser, context, page }, use, testInfo) => {
    await testInfo.attach('runtime.json', { body: JSON.stringify({ chromium: browser.version(), playwright: '1.62.1', maplibre: '5.9.0', pmtiles: '4.3.0' }), contentType: 'application/json' });
    const catalog = await readJSON(resolve(siteDir, 'catalog.json'));
    const indexes = new Map(await Promise.all(catalog.assets.map(async (asset) => [asset.slug, await readJSON(resolve(workDir, 'inputs/indexes', `${asset.slug}.json`))])));
    const requests = [], errors = [], forbidden = [], expectedErrors = new Set();
    let releaseHeld;
    const state = { deny: 0, holdMetadata: false, held: false, release: () => releaseHeld?.(), requests };
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (['error', 'warning'].includes(message.type())) errors.push({ type: message.type(), text: message.text(), url: message.location().url });
    });
    await context.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      requests.push({ url: url.href, method: request.method(), range: request.headers().range || null });
      const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
      if (libraries.has(url.href)) {
        const [file, contentType] = libraries.get(url.href);
        return route.fulfill({ path: resolve(packageDir, 'node_modules', file), contentType });
      }
      if (/^https:\/\/tile\.openstreetmap\.org\/\d+\/\d+\/\d+\.png$/.test(url.href)) return route.fulfill({ body: basemapPNG, contentType: 'image/png' });
      const asset = catalog.assets.find((entry) => new URL(entry.release_index_url, baseURL).href === `${url.origin}${url.pathname}`);
      if (asset) return json(indexes.get(asset.slug));
      if (url.origin === baseURL && ['/api/pmtiles/signed-url', '/api/download-url'].includes(url.pathname)) {
        const slug = url.searchParams.get('slug'), date = url.searchParams.get('version');
        const format = url.pathname.includes('/pmtiles/') ? 'pmtiles' : url.searchParams.get('format');
        const file = indexes.get(slug)?.releases.find((entry) => entry.date === date)?.files.find((entry) => entry.format === format);
        if (!file || String(file.generation) !== url.searchParams.get('generation')) {
          forbidden.push(`Signer did not receive exact release identity: ${url.href}`);
          return json({ error: 'Invalid fixture identity' }, 400);
        }
        if (format === 'pmtiles' && state.deny) {
          expectedErrors.add(url.href);
          return json({ error: state.deny === 403 ? 'Synthetic authorization denied' : 'Synthetic generation changed' }, state.deny);
        }
        return json({ gs_uri: file.path, resolved_release: date, generation: String(file.generation),
          expires_at: '2300-01-01T00:00:00Z',
          [format === 'pmtiles' ? 'pmtiles_url' : 'download_url']: `https://tiles.skytruth.org/artifacts/${file.path.split('example-bucket/')[1]}?generation=${file.generation}` });
      }
      if (url.hostname === 'tiles.skytruth.org' && url.pathname.startsWith('/artifacts/')) {
        const object = decodeURIComponent(url.pathname.slice('/artifacts/'.length));
        const file = [...indexes.values()].flatMap((index) => index.releases.flatMap((entry) => entry.files)).find((entry) => entry.path === `gs://example-bucket/${object}` && String(entry.generation) === url.searchParams.get('generation'));
        if (!file) {
          forbidden.push(`Unpinned or unexpected artifact: ${url.href}`);
          return json({ error: 'Not retained' }, 404);
        }
        if (state.holdMetadata && file.format === 'metadata' && object.includes('/2026-01-01/')) {
          state.held = true;
          await new Promise((resolveHeld) => { releaseHeld = resolveHeld; });
        }
        let data = await readFile(resolve(workDir, 'objects', object));
        // Acceptance-only negative control: real wrong tiles cannot satisfy the inspector assertion.
        if (process.env.CATALOG_BROWSER_NEGATIVE_CONTROL === 'wrong-old-tiles' && file.format === 'pmtiles' && object.includes('/2026-01-01/')) data = await readFile(resolve(packageDir, 'fixtures/new.pmtiles'));
        const range = request.headers().range;
        const headers = { 'accept-ranges': 'bytes', 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Content-Range,Content-Length' };
        if (range) {
          const match = /^bytes=(\d+)-(\d*)$/.exec(range);
          if (!match) throw new Error(`Unexpected Range: ${range}`);
          const start = Number(match[1]), end = Math.min(Number(match[2] || data.length - 1), data.length - 1);
          headers['content-range'] = `bytes ${start}-${end}/${data.length}`;
          return route.fulfill({ status: 206, headers, contentType: 'application/octet-stream', body: data.subarray(start, end + 1) });
        }
        return route.fulfill({ headers, contentType: 'application/octet-stream', body: data });
      }
      if (url.origin === baseURL && !url.pathname.startsWith('/api/') && !url.pathname.startsWith('/v1/')) return route.continue();
      forbidden.push(`Unexpected network request: ${url.href}`);
      return route.abort('blockedbyclient');
    });
    try {
      await use(state);
    } finally {
      state.release();
      await testInfo.attach('requests.json', { body: JSON.stringify(requests, null, 2), contentType: 'application/json' });
      await testInfo.attach('browser-errors.json', { body: JSON.stringify(errors, null, 2), contentType: 'application/json' });
      expect(forbidden, 'All data/auth/CDN boundaries must be explicitly served').toEqual([]);
      // Chromium SwiftShader emits a documented performance warning on screenshot readback.
      const expectedDriverWarning = (entry) => entry.type === 'warning' && /^\[\.WebGL-0x[0-9a-f]+\]GL Driver Message \(OpenGL, Performance, GL_CLOSE_PATH_NV, High\): GPU stall due to ReadPixels(?: \(this message will no longer repeat\))?$/.test(entry.text);
      expect(errors.filter((entry) => !expectedDriverWarning(entry) && !(typeof entry === 'object' && expectedErrors.has(entry.url) && /^Failed to load resource: the server responded with a status of (403|409)/.test(entry.text))), 'Unexpected browser errors').toEqual([]);
    }
  }, { auto: true }],
});

async function select(page, tier) {
  await page.goto(baseURL);
  await page.locator(`#asset-list [data-slug="smoke-${tier}"]`).click();
}

async function clickPoint(page, old, testInfo) {
  const canvas = page.locator('#map-preview canvas.maplibregl-canvas');
  await expect(canvas).toBeVisible();
  await expect(page.locator('#map-status')).toBeHidden();
  const box = await canvas.boundingBox();
  expect(box.width).toBeGreaterThan(100);
  expect(box.height).toBeGreaterThan(100);
  // MapLibre fits the fixture's 1x1 degree bounds with padding34, maxZoom8.
  // Click the upper-right point: independent geometry, no map instance/test hook.
  const mercatorY = (lat) => (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2;
  const dx = 1 / 360, dy = mercatorY(0) - mercatorY(1);
  const scale = Math.min((box.width - 68) / dx, (box.height - 68) / dy, 512 * 2 ** 8);
  await canvas.click({ position: { x: box.width / 2 + dx * scale / 2, y: box.height / 2 - dy * scale / 2 } });
  await expect(page.locator('#feature-inspector')).toContainText(old ? 'a2' : 'b2');
  await testInfo.attach(`${old ? 'historical' : 'latest'}-canvas.png`, { body: await canvas.screenshot(), contentType: 'image/png' });
}

async function expectMetadata(page, old) {
  const inspector = page.locator('#feature-inspector');
  await expect(inspector).toContainText(old ? 'Old footprint 2' : 'New footprint 2');
  await expect(inspector).toContainText(old ? '2026-01-01' : '2026-09-22');
  await expect(inspector).not.toContainText(old ? 'New footprint' : 'Old footprint');
}

test('public latest and historical maps keep tile and inspector identity together', async ({ page, transport }, testInfo) => {
  await select(page, 'public');
  await clickPoint(page, false, testInfo);
  await expectMetadata(page, false);
  expect(transport.requests.some((entry) => entry.range && /2026-09-22\/smoke-public\.pmtiles\?generation=201/.test(entry.url))).toBeTruthy();
  expect(transport.requests.some((entry) => /2026-09-22\/smoke-public\.metadata\.ndjson\.gz\?generation=202/.test(entry.url))).toBeTruthy();
  await page.locator('#version-select').selectOption('2026-01-01');
  await clickPoint(page, true, testInfo);
  await expectMetadata(page, true);
  await expect(page.locator('#download-fgb')).toHaveAttribute('href', /2026-01-01\/smoke-public\.fgb\?generation=100/);
  expect(transport.requests.some((entry) => entry.range && /2026-01-01\/smoke-public\.pmtiles\?generation=101/.test(entry.url))).toBeTruthy();
  expect(transport.requests.some((entry) => /2026-01-01\/smoke-public\.metadata\.ndjson\.gz\?generation=102/.test(entry.url))).toBeTruthy();
  await page.locator('#version-select').selectOption('latest');
  await clickPoint(page, false, testInfo);
  await expectMetadata(page, false);
});

for (const [tier, status] of [['private', 403], ['internal', 409]]) {
  test(`${tier} ${status} refusal clears the old map and inspector, then recovers`, async ({ page, transport }, testInfo) => {
    await select(page, tier);
    await clickPoint(page, false, testInfo);
    await expectMetadata(page, false);
    transport.deny = status;
    const priorRequests = transport.requests.length;
    await page.locator('#version-select').selectOption('2026-01-01');
    await expect(page.locator('#map-status')).toContainText('Map unavailable');
    await expect(page.locator('#map-preview canvas')).toHaveCount(0);
    await expect(page.locator('#feature-inspector')).toBeHidden();
    await expect(page.locator('#feature-inspector')).toBeEmpty();
    expect(transport.requests.slice(priorRequests).filter((entry) => entry.url.includes('/artifacts/'))).toEqual([]);
    transport.deny = 0;
    await page.locator('#version-select').selectOption('latest');
    await clickPoint(page, false, testInfo);
    await expectMetadata(page, false);
  });
}

test('late historical metadata cannot replace the current inspector', async ({ page, transport }, testInfo) => {
  transport.holdMetadata = true;
  await select(page, 'public');
  await page.locator('#version-select').selectOption('2026-01-01');
  await clickPoint(page, true, testInfo);
  await expect.poll(() => transport.held).toBe(true);
  await page.locator('#version-select').selectOption('latest');
  await clickPoint(page, false, testInfo);
  await expectMetadata(page, false);
  const completed = page.waitForResponse((response) => response.url().includes('/2026-01-01/') && response.url().includes('.metadata.ndjson.gz'));
  transport.release();
  await (await completed).finished();
  // Wait for response processing and a painted frame, not merely response headers.
  await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
  await expectMetadata(page, false);
});
