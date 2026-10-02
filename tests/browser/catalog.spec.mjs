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


test('comparison uses complete inputs, union maps, real controls and keyboard inspection', async ({page, transport}, testInfo) => {
  await select(page, 'comparison');
  await page.locator('#compare-open').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#compare-baseline')).toBeFocused();
  await expect(page.locator('#compare-baseline')).toHaveValue('2026-01-01');
  await expect(page.locator('#compare-target')).toHaveValue('2026-09-22');
  await page.locator('#compare-run').click();
  await expect(page.locator('#compare-status')).toContainText('Comparison complete');
  await expect(page.locator('#compare-page')).toHaveText('1–50 of 106');
  await expect(page.locator('#compare-summary')).toContainText('Properties only');
  const canvas = page.locator('#compare-maps canvas');
  await expect(canvas).toHaveCount(1);
  await expect(canvas).toBeVisible();
  expect(transport.requests.some(r => r.range && r.url.includes('/2026-01-01/') && r.url.includes('.pmtiles'))).toBe(true);
  expect(transport.requests.some(r => r.range && r.url.includes('/2026-09-22/') && r.url.includes('.pmtiles'))).toBe(true);
  await page.locator('#compare-maps').scrollIntoViewIfNeeded();
  await testInfo.attach('comparison-union.png', {body:await page.locator('#compare-maps').screenshot(), contentType:'image/png'});
  // Click the real shared [1, 1] point within combined [-2, -1, 4, 1] bounds.
  // The pixel is derived from independent fixture geometry, without a map test hook.
  const box = await canvas.boundingBox();
  const mercatorY = lat => (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2;
  const scale = Math.min((box.width - 68) / (6 / 360), (box.height - 68) / (mercatorY(-1) - mercatorY(1)), 512 * 2 ** 8);
  await canvas.click({position:{x:box.width / 2, y:box.height / 2 - (0.5 - mercatorY(1)) * scale}});
  await expect(page.locator('#compare-inspector')).toContainText('Shared before');
  await expect(page.locator('#compare-inspector')).toContainText('Shared after');
  await page.locator('#compare-search').fill('Shared');
  await expect(page.locator('#compare-page')).toHaveText('1–1 of 1');
  await page.locator('#compare-rows button').focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#compare-inspector')).toContainText('Shared before');
  await expect(page.locator('#compare-inspector')).toContainText('Shared after');
  await expect(page.locator('#compare-inspector')).toContainText('Absent');
  await expect(page.locator('#compare-inspector')).toContainText('null');
  await page.locator('#compare-search').fill('');
  await page.locator('#compare-next').click();
  await expect(page.locator('#compare-page')).toHaveText('51–100 of 106');
  await page.locator('#compare-mode').selectOption('before-after');
  await expect(page.locator('#compare-maps canvas')).toHaveCount(2);
  const download = page.waitForEvent('download'); await page.locator('#compare-report').click();
  const file = await (await download).path();
  const report = await readJSON(file);
  expect(report.features).toHaveLength(106);
  expect(report.summary.counts).toEqual({added:1,removed:1,geometry_only:1,properties_only:1,both:0,unchanged:102});
  await page.locator('#compare-open').scrollIntoViewIfNeeded();
  await testInfo.attach('comparison-desktop.png', {body:await page.screenshot(), contentType:'image/png'});
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('#compare-baseline')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator('#compare-open').scrollIntoViewIfNeeded();
  await testInfo.attach('comparison-narrow.png', {body:await page.screenshot(), contentType:'image/png'});
  await page.locator('#compare-maps').scrollIntoViewIfNeeded();
  await testInfo.attach('comparison-narrow-maps.png', {body:await page.locator('#compare-maps').screenshot(), contentType:'image/png'});
});

test('static catalog explains comparison availability and keeps browsing functional', async ({page, transport}, testInfo) => {
  transport.comparisonUnavailable = true;
  await select(page, 'public');
  await page.locator('#compare-open').click();
  await page.locator('#compare-run').click();
  await expect(page.locator('#compare-status')).toContainText('authenticated catalog viewer');
  await expect(page.locator('#compare-maps canvas')).toHaveCount(1);
  await expect(page.locator('#compare-table')).toBeHidden();
  await expect(page.locator('#compare-report')).toBeDisabled();
  await page.locator('.compare-local-guide summary').click();
  await expect(page.locator('.compare-local-guide')).toContainText('scripts/compare_releases.py');
  await page.locator('#compare-close').focus(); await page.keyboard.press('Escape');
  await expect(page.locator('#compare-panel')).toBeHidden();
  await expect(page.locator('#compare-open')).toBeFocused();
  await page.locator('#version-select').selectOption('2026-01-01');
  await clickPoint(page, true, testInfo); await expectMetadata(page, true);
});

test('comparison table updates preserve a denied historical map error and allow retry', async ({page, transport}) => {
  await select(page, 'private');
  transport.deny = 403;
  await page.locator('#compare-open').click(); await page.locator('#compare-run').click();
  await expect(page.locator('#compare-status')).toContainText('Comparison complete');
  await expect(page.locator('#compare-page')).toHaveText('1–50 of 105');
  await expect(page.locator('#compare-map-note')).toContainText('Map inspection unavailable');
  await expect(page.locator('#compare-maps canvas')).toHaveCount(0);
  await page.locator('#compare-search').fill('Old');
  await expect(page.locator('#compare-page')).toHaveText('1–2 of 2');
  await page.locator('#compare-rows button').first().click();
  await expect(page.locator('#compare-inspector')).toContainText('Old footprint');
  await expect(page.locator('#compare-map-note')).toContainText('Map inspection unavailable');
  transport.deny = 0;
  await page.locator('#compare-mode').selectOption('before-after');
  await expect(page.locator('#compare-maps canvas')).toHaveCount(2);
  await expect(page.locator('#compare-map-note')).not.toContainText('Map inspection unavailable');
});

test('delayed comparison start cannot overwrite a newer release selection and cancellation', async ({page, transport}) => {
  await select(page, 'public'); await page.locator('#compare-open').click();
  transport.holdComparison = true;
  await page.locator('#compare-run').click();
  await expect.poll(() => transport.comparisonHeld).toBe(true);
  await page.locator('#compare-baseline').selectOption('2026-09-22');
  await page.locator('#compare-target').selectOption('2026-01-01');
  await page.locator('#compare-run').click();
  await expect(page.locator('#compare-status')).toContainText('Comparison complete');
  await expect(page.locator('#compare-summary')).toContainText('Baseline: 2026-09-22');
  transport.release();
  await expect.poll(() => transport.requests.some(r => r.url.endsWith('/cancel'))).toBe(true);
  await expect(page.locator('#compare-summary')).toContainText('Baseline: 2026-09-22');
  await expect(page.locator('#compare-summary')).toContainText('Target: 2026-01-01');
  transport.comparisonHeld = false; transport.holdComparison = true;
  await page.locator('#compare-run').click();
  await expect.poll(() => transport.comparisonHeld).toBe(true);
  await page.locator('#compare-run').click();
  transport.release();
  await expect(page.locator('#compare-status')).toHaveText('Comparison cancelled.');
  await expect(page.locator('#compare-summary')).toBeEmpty();
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
