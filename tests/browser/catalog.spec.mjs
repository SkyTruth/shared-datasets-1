import { PNG } from 'pngjs';
import ts from '../../api/typescript/node_modules/typescript/lib/typescript.js';
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
    const storedFiles = [...indexes.values()].flatMap(index => index.releases.flatMap(release => release.files)).map(file => structuredClone(file));
    let releaseHeld;
    const state = { deny: 0, comparisonUnavailable: false, holdMetadata: false, held: false, holdComparison: false, comparisonHeld: false, release: () => releaseHeld?.(), requests, indexes, unavailable: new Set() };
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
      if (/^https:\/\/services\.arcgisonline\.com\/ArcGIS\/rest\/services\/World_Imagery\/MapServer\/tile\/\d+\/\d+\/\d+$/.test(url.href)) return route.fulfill({body: basemapPNG, contentType: 'image/png'});
      const asset = catalog.assets.find((entry) => new URL(entry.release_index_url, baseURL).href === `${url.origin}${url.pathname}`);
      if (asset) return json(indexes.get(asset.slug));
      if (url.origin === baseURL && /^\/sdk\/[a-z-]+\.js$/.test(url.pathname)) {
        return route.fulfill({path: resolve(siteDir, url.pathname.slice(1)), contentType: 'text/javascript'});
      }
      if (url.origin === baseURL && url.pathname.startsWith('/api/comparisons')) {
        if (state.comparisonUnavailable) {
          expectedErrors.add(url.href);
          return route.fulfill({status:404,contentType:'text/plain',body:'Not found'});
        }
        const response = await route.fetch({ headers: {...request.headers(), 'X-Goog-Authenticated-User-Email': 'accounts.google.com:browser@skytruth.org'} });
        if (state.holdComparison && request.method() === 'POST' && url.pathname === '/api/comparisons') {
          state.holdComparison = false; state.comparisonHeld = true;
          await new Promise(resolveHeld => { releaseHeld = resolveHeld; });
        }
        return route.fulfill({response});
      }
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
        const file = storedFiles.find((entry) => entry.path === `gs://example-bucket/${object}` && String(entry.generation) === url.searchParams.get('generation'));
        if (file && state.unavailable.has(`${file.path}#${file.generation}`)) {
          expectedErrors.add(url.href);
          return json({error: 'Synthetic captured generation unavailable'}, 404);
        }
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
        const headers = { 'accept-ranges': 'bytes', 'access-control-allow-origin': '*', 'x-goog-generation': String(file.generation), 'access-control-expose-headers': 'Content-Range,Content-Length,x-goog-generation' };
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
      expect(errors.filter((entry) => !expectedDriverWarning(entry) && !(typeof entry === 'object' && expectedErrors.has(entry.url) && /^Failed to load resource: the server responded with a status of (403|404|409)/.test(entry.text))), 'Unexpected browser errors').toEqual([]);
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


test('comparison automatically takes over the primary map with compact tables and keyboard inspection', async ({page, transport}, testInfo) => {
  await select(page, 'comparison');
  await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#compare-open').focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#compare-before')).toBeFocused();
  await expect(page.locator('#compare-before')).toHaveValue('2026-01-01');
  await expect(page.locator('#compare-after')).toHaveValue('2026-09-22');
  await expect(page.locator('#version-select')).toBeHidden();
  await expect(page.locator('#compare-open')).toHaveText('Close comparison');
  await expect(page.locator('#compare-run, #compare-close, #compare-mode, #compare-maps')).toHaveCount(0);
  await expect(page.locator('#compare-status')).toHaveText('Comparison complete');
  await expect(page.locator('#compare-page')).toHaveText('1–50 of 106');
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  const canvas = page.locator('#map-preview canvas');
  await expect(page.locator('canvas')).toHaveCount(1); await expect(canvas).toBeVisible();
  await expect.poll(() => transport.requests.filter(r => r.url.endsWith('/map') && r.method === 'POST').length).toBeGreaterThanOrEqual(2);
  expect(transport.requests.some(r => r.range && r.url.includes('/2026-01-01/') && r.url.includes('.pmtiles'))).toBe(true);
  expect(transport.requests.some(r => r.range && r.url.includes('/2026-09-22/') && r.url.includes('.pmtiles'))).toBe(true);
  // The primary map preserves the previous viewport, fitted to the after archive.
  const box = await canvas.boundingBox();
  const mercatorY = lat => (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2;
  const scale = Math.min((box.width - 68) / (3 / 360), (box.height - 68) / (mercatorY(-1) - mercatorY(1)), 512 * 2 ** 8);
  await canvas.click({position:{x:box.width / 2 - 1.5 / 360 * scale, y:box.height / 2 - (0.5 - mercatorY(1)) * scale}});
  await expect(page.locator('#compare-inspector')).toContainText('Shared before');
  await expect(page.locator('#compare-inspector')).toContainText('Shared after');
  await page.locator('#compare-search').fill('Shared');
  await expect(page.locator('#compare-page')).toHaveText('1–1 of 1');
  await page.locator('#compare-rows button').focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#compare-inspector')).toContainText('Absent');
  await expect(page.locator('#compare-inspector')).toContainText('null');
  await page.locator('#compare-search').fill(''); await page.locator('#compare-next').click();
  await expect(page.locator('#compare-page')).toHaveText('51–100 of 106');
  const report = await downloadedJson(page, page.locator('#compare-report'));
  expect(report.features).toHaveLength(106);
  expect(report.summary.counts).toEqual({added:1,removed:1,geometry_only:1,properties_only:1,both:0,unchanged:102});
  await page.locator('#compare-open').scrollIntoViewIfNeeded();
  await testInfo.attach('comparison-desktop.png', {body:await page.screenshot(), contentType:'image/png'});
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('#compare-before')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator('#compare-open').scrollIntoViewIfNeeded();
  await testInfo.attach('comparison-narrow.png', {body:await page.screenshot(), contentType:'image/png'});
  await page.locator('#compare-before').focus(); await page.keyboard.press('Escape');
  await expect(page.locator('#compare-panel')).toBeHidden();
  await expect(page.locator('#compare-open')).toHaveText('Compare releases');
  await expect(page.locator('#version-select')).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(1);
});

test('static catalog explains comparison availability and restores ordinary browsing', async ({page, transport}, testInfo) => {
  transport.comparisonUnavailable = true;
  await select(page, 'public'); await page.locator('#compare-open').click();
  await expect(page.locator('#compare-status')).toContainText('authenticated catalog viewer');
  await expect(page.locator('#map-preview canvas')).toHaveCount(1);
  await expect(page.locator('#compare-table')).toBeHidden();
  await expect(page.locator('#compare-report')).toBeDisabled();
  await page.locator('.compare-local-guide summary').click();
  await expect(page.locator('.compare-local-guide')).toContainText('scripts/compare_releases.py');
  await page.locator('#compare-open').focus(); await page.keyboard.press('Escape');
  await expect(page.locator('#compare-panel')).toBeHidden();
  await expect(page.locator('#compare-open')).toBeFocused();
  await page.locator('#version-select').selectOption('2026-01-01');
  await clickPoint(page, true, testInfo); await expectMetadata(page, true);
});

test('comparison table updates preserve a denied historical map error and selection changes retry', async ({page, transport}) => {
  await select(page, 'private'); transport.deny = 403;
  await page.locator('#compare-open').click();
  await expect(page.locator('#compare-status')).toHaveText('Comparison complete');
  await expect(page.locator('#compare-page')).toHaveText('1–50 of 105');
  await expect(page.locator('#compare-map-note')).toContainText('Map inspection unavailable');
  await expect(page.locator('#map-preview canvas')).toHaveCount(0);
  await page.locator('#compare-search').fill('Old');
  await expect(page.locator('#compare-page')).toHaveText('1–2 of 2');
  await page.locator('#compare-rows button').first().click();
  await expect(page.locator('#compare-inspector')).toContainText('Old footprint');
  await expect(page.locator('#compare-map-note')).toContainText('Map inspection unavailable');
  transport.deny = 0;
  await page.locator('#compare-before').selectOption('2026-09-22');
  await page.locator('#compare-after').selectOption('2026-01-01');
  await expect(page.locator('#compare-status')).toHaveText('Comparison complete');
  await expect(page.locator('#map-preview canvas')).toHaveCount(1);
  await expect(page.locator('#compare-map-note')).not.toContainText('Map inspection unavailable');
});

test('automatic comparison rejects delayed starts and closing cancels pending work', async ({page, transport}) => {
  await select(page, 'public'); transport.holdComparison = true;
  await page.locator('#compare-open').click();
  await expect.poll(() => transport.comparisonHeld).toBe(true);
  await page.locator('#compare-before').selectOption('2026-09-22');
  await page.locator('#compare-after').selectOption('2026-01-01');
  await expect(page.locator('#compare-status')).toHaveText('Comparison complete');
  const releaseRow = page.locator('#compare-summary tbody tr').first();
  await expect(releaseRow.locator('td').first()).toHaveText('2026-09-22');
  transport.release();
  await expect.poll(() => transport.requests.some(r => r.url.endsWith('/cancel'))).toBe(true);
  await expect(releaseRow.locator('td').last()).toHaveText('2026-01-01');
  transport.comparisonHeld = false; transport.holdComparison = true;
  await page.locator('#compare-after').selectOption('2026-09-22');
  await expect.poll(() => transport.comparisonHeld).toBe(true);
  await page.locator('#compare-open').click(); transport.release();
  await expect(page.locator('#compare-panel')).toBeHidden();
  await expect(page.locator('#compare-summary')).toBeEmpty();
  await expect(page.locator('#version-select')).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(1);
});

test('polygons render red green yellow and faint gray across a generated ID reset', async ({page, transport}, testInfo) => {
  await select(page, 'polygons'); await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#compare-open').click();
  await expect(page.locator('#compare-status')).toHaveText('Comparison complete');
  await expect(page.locator('#compare-summary')).toContainText('Feature IDs cannot be matched');
  await expect(page.locator('#compare-summary')).toContainText('Unique geometries');
  await expect(page.locator('#compare-table')).toBeHidden();
  await expect(page.locator('canvas')).toHaveCount(1);
  const canvas = page.locator('#map-preview canvas');
  await expect.poll(() => transport.requests.filter(r => r.url.endsWith('/map')).length).toBeGreaterThanOrEqual(2);
  const box = await canvas.boundingBox();
  const mercatorY = lat => (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2;
  const low = mercatorY(-1.5), high = mercatorY(3), scale = Math.min((box.width - 68) / (6 / 360), (box.height - 68) / (low - high), 512 * 2 ** 8);
  const pixel = (image, lon, lat) => {
    const x = Math.round(box.width / 2 + lon / 360 * scale), y = Math.round(box.height / 2 + (mercatorY(lat) - (low + high) / 2) * scale);
    return [...image.data.subarray((y * image.width + x) * 4, (y * image.width + x) * 4 + 3)];
  };
  const sample = async () => {
    const image = PNG.sync.read(await canvas.screenshot());
    return {red:pixel(image,-1.5,-1),green:pixel(image,2.5,-1),yellow:pixel(image,.5,.5),gray:pixel(image,-2.5,2.5),background:pixel(image,-1.5,2.5)};
  };
  // Inspect actual polygon interior pixels. This fails if ID compatibility or
  // pagination gates map colors, or if fill paint remains ordinary gray.
  await expect.poll(async () => {
    const c = await sample();
    return c.red[0] - c.red[1] > 30 && c.green[1] - c.green[0] > 30 && c.yellow[0] - c.yellow[2] > 50;
  }).toBe(true);
  const c = await sample(), distance = (a,b) => Math.hypot(...a.map((v,i) => v-b[i]));
  expect(distance(c.gray,c.background)).toBeLessThan(distance(c.red,c.background) / 2);
  await testInfo.attach('comparison-polygons.png', {body:await page.locator('#map-section').screenshot(), contentType:'image/png'});
  await testInfo.attach('comparison-polygon-summary.png', {body:await page.locator('#compare-panel').screenshot(), contentType:'image/png'});
  const report = await downloadedJson(page, page.locator('#compare-report'));
  expect(report.summary.counts).toBeNull();
  expect(report.summary.geometry_counts).toEqual({novel:2,removed:2,metadata_changed:1,unchanged:1});
});

async function downloadedJson(page, button) {
  const waiting = page.waitForEvent('download');
  await button.click();
  const download = await waiting;
  return JSON.parse(await readFile(await download.path(), 'utf8'));
}
async function openSnapshot(page, snapshot) {
  await page.locator('#open-workspace-file').setInputFiles({name: 'fixture.workspace.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(snapshot))});
}

test('Use this dataset opens, copies valid code, exports exact identity and executes the TypeScript integration', async ({page, context}, testInfo) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await select(page, 'public');
  await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#use-dataset').click();
  await expect(page.locator('#use-dataset-dialog')).toBeVisible();
  await expect(page.locator('#use-summary')).toContainText('release 2026-09-22');
  await expect(page.locator('#use-install')).toContainText(/Unreleased local build|skytruth-shared-datasets\[gcs\] @/);
  await page.locator('#use-copy-python').click();
  const python = await page.evaluate(() => navigator.clipboard.readText());
  expect(python).toContain('fetch_snapshot_artifact');
  await page.locator('#use-copy-typescript').click();
  const code = await page.evaluate(() => navigator.clipboard.readText());
  expect(code).toContain('resolveSnapshotLayer');
  const lock = await downloadedJson(page, page.locator('#use-export'));
  expect(lock.presentation).toBeNull();
  expect(lock.datasets[0].artifacts.find(a => a.role === 'tiles').generation).toBe('201');
  expect(JSON.stringify(lock)).not.toMatch(/Signature=|cookies|cache_path|download_url/);
  await testInfo.attach('use-dataset-dialog.png', {body: await page.locator('#use-dataset-dialog').screenshot(), contentType: 'image/png'});
  await testInfo.attach('generated-python.py', {body: python, contentType: 'text/x-python'});
  await testInfo.attach('generated-typescript.ts', {body: code, contentType: 'text/plain'});
  await page.locator('#use-close').click();
  // A bundler resolves these imports in consumers. Here the real browser SDK
  // modules and pinned MapLibre/PMTiles libraries are passed without mocks.
  const compiledCode = ts.transpileModule(code, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText;
  const generated = await page.evaluate(async source => {
    const sdk = await import('/sdk/index.js');
    const container = document.createElement('div'); container.id = 'map';
    container.style.cssText = 'position:fixed;inset:100px;width:600px;height:500px;z-index:500'; document.body.append(container);
    const body = source.replace(/^import .*;\n/gm, '');
    const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
    const result = await new AsyncFunction('maplibregl', 'Protocol', 'PMTiles', 'validateWorkspaceSnapshot', 'resolveSnapshotLayer', 'fetchSnapshotMetadata',
      body + '\nawait new Promise((resolve,reject)=>{map.once("load",resolve);map.once("error",event=>reject(event.error))}); return {map,records,layer};'
    )(window.maplibregl, window.pmtiles.Protocol, window.pmtiles.PMTiles, sdk.validateWorkspaceSnapshot, sdk.resolveSnapshotLayer, sdk.fetchSnapshotMetadata);
    window.generatedIntegration = result;
    return {ids: [...result.records.keys()], tileUrl: result.layer.tileUrl, metadataUrl: result.layer.metadata.url};
  }, compiledCode);
  expect(generated.ids).toEqual(['b1', 'b2']);
  expect(generated.tileUrl).toContain('generation=201');
  expect(generated.metadataUrl).toContain('generation=202');
  await expect.poll(() => page.evaluate(() => window.generatedIntegration.map.queryRenderedFeatures().map(f => f.properties.feature_id))).toContain('b2');
  await testInfo.attach('generated-map.png', {body: await page.locator('#map').screenshot(), contentType: 'image/png'});
  await page.evaluate(() => {window.generatedIntegration.map.remove();document.querySelector('#map').remove();});
});

test('single workspace round-trip restores release, viewport, basemap and supported layer/color controls', async ({page, transport}, testInfo) => {
  await select(page, 'public');
  await page.locator('#version-select').selectOption('2026-01-01');
  await expect(page.locator('#map-status')).toBeHidden();
  const layers = await page.locator('#layer-select option').evaluateAll(options => options.map(o => o.value).filter(Boolean));
  if (layers.length) {
    await page.locator('#layer-select').selectOption(layers[0]);
    await expect(page.locator('#map-status')).toBeHidden();
  }
  await expect(page.locator('#colorize-select')).toContainText('name');
  await page.locator('#colorize-select').selectOption('name');
  await page.locator('#basemap-select').selectOption('satellite');
  await expect(page.locator('#map-status')).toBeHidden();
  const before = await downloadedJson(page, page.locator('#save-workspace'));
  expect(before.presentation.basemap).toBe('satellite');
  expect(before.presentation.layers[0].source_layer).toBe(layers[0] || null);
  expect(before.presentation.layers[0].color_field).toBe('name');
  expect(before.presentation.viewport.zoom).toBeGreaterThan(1);
  await page.locator('#version-select').selectOption('latest');
  await expect(page.locator('#map-status')).toBeHidden();
  const index = transport.indexes.get('smoke-public'); index.latest_release = index.releases[1];
  await openSnapshot(page, before);
  await expect(page.locator('#workspace-status')).toContainText('Workspace restored');
  const after = await downloadedJson(page, page.locator('#save-workspace'));
  expect(after.presentation).toEqual(before.presentation);
  expect(after.datasets[0].artifacts).toEqual(before.datasets[0].artifacts);
  await clickPoint(page, true, testInfo);
  await expectMetadata(page, true);
});

test('multiple dataset workspace preserves exact per-asset releases and display order', async ({page, transport}, testInfo) => {
  await select(page, 'public');
  await page.locator('#version-select').selectOption('2026-01-01');
  await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#asset-list [data-slug="smoke-private"]').click({modifiers: ['Meta']});
  await expect(page.locator('#map-status')).toBeHidden();
  const before = await downloadedJson(page, page.locator('#save-workspace'));
  expect(before.datasets.map(d => [d.asset_slug, d.release])).toEqual([['smoke-public', '2026-01-01'], ['smoke-private', '2026-09-22']]);
  const requests = transport.requests.length;
  await page.locator('#asset-list [data-slug="smoke-internal"]').click();
  await expect(page.locator('#map-status')).toBeHidden();
  await openSnapshot(page, before);
  await expect(page.locator('#workspace-status')).toContainText('Workspace restored');
  const after = await downloadedJson(page, page.locator('#save-workspace'));
  expect(after.datasets.map(d => d.artifacts)).toEqual(before.datasets.map(d => d.artifacts));
  expect(after.presentation).toEqual(before.presentation);
  expect(transport.requests.slice(requests).some(r => r.url.includes('/api/pmtiles/signed-url') && r.url.includes('slug=smoke-private'))).toBeTruthy();
  await testInfo.attach('restored-workspace.png', {body: await page.screenshot(), contentType: 'image/png'});
});

test('unavailable or malformed workspace fails atomically without upgrading or requesting arbitrary paths', async ({page, transport}) => {
  await select(page, 'public');
  await page.locator('#version-select').selectOption('2026-01-01');
  await expect(page.locator('#map-status')).toBeHidden();
  const snapshot = await downloadedJson(page, page.locator('#save-workspace'));
  await page.locator('#version-select').selectOption('latest');
  await expect(page.locator('#map-status')).toBeHidden();
  const old = structuredClone(snapshot.datasets[0].artifacts.find(a => a.role === 'canonical'));
  transport.unavailable.add(`${old.gs_uri}#${old.generation}`);
  await openSnapshot(page, snapshot);
  await expect(page.locator('#workspace-status')).toContainText('Workspace not restored');
  await expect(page.locator('#workspace-status')).toContainText('#100');
  await expect(page.locator('#version-select')).toHaveValue('latest');
  await expect(page.locator('#workspace-status')).toContainText('no release was substituted');
  snapshot.datasets[0].artifacts[0].gs_uri = 'gs://example-bucket/secrets/key.json';
  const count = transport.requests.length;
  await openSnapshot(page, snapshot);
  await expect(page.locator('#workspace-status')).toContainText('Invalid workspace');
  expect(transport.requests.slice(count).filter(r => r.url.includes('/api/'))).toEqual([]);
  snapshot.datasets[0].artifacts[0].gs_uri = old.gs_uri;
  transport.unavailable.clear();
  snapshot.presentation.layers[0].source_layer = 'unsupported-source-layer';
  await openSnapshot(page, snapshot);
  await expect(page.locator('#workspace-status')).toContainText('Captured source layer is unavailable');
  await expect(page.locator('#version-select')).toHaveValue('latest');
  await expect(page.locator('#map-status')).toBeHidden();
});
