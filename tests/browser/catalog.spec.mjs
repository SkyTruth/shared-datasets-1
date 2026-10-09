import { PNG } from 'pngjs';
import ts from '../../api/typescript/node_modules/typescript/lib/typescript.js';
import { test as base, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { baseURL, packageDir, siteDir, workDir } from './paths.mjs';

const readJSON = async (path) => JSON.parse(await readFile(path, 'utf8'));
const comparisonColors = [[195,59,59], [22,129,83], [227,189,32]];
const nearColor = (image, color) => {
  for (let i=0; i<image.data.length; i+=4) if (Math.hypot(...color.map((v,j) => v-image.data[i+j])) < 20) return true;
  return false;
};
const sampleCanvas = async canvas => PNG.sync.read(await canvas.screenshot());
const viewport = page => page.evaluate(async () => {
  const url = performance.getEntriesByType('resource').find(item => new URL(item.name).pathname === '/map-preview.js').name;
  return (await import(url)).captureViewport();
});
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
    const scenarioEmail = `browser-${testInfo.testId.replace(/[^a-z0-9]/gi, '')}@skytruth.org`;
    const storedFiles = [...indexes.values()].flatMap(index => index.releases.flatMap(release => release.files)).map(file => structuredClone(file));
    let releaseHeld;
    const state = { execution: {
      schema_version: 1, job_name: 'projects/test/locations/test/jobs/wdpa-monthly', observed_at: new Date().toISOString(),
      latest_execution: {id: 'wdpa-monthly-new', state: 'failed', completed_at: new Date().toISOString(), reason_code: 'NON_ZERO_EXIT_CODE'},
      latest_completed_execution: {id: 'wdpa-monthly-new', state: 'failed', completed_at: new Date().toISOString()},
    }, deny: 0, progress: null, comparisonUnavailable: false, holdMetadata: false, held: false, holdComparison: false, comparisonHeld: false, holdMapIndex: false, mapIndexHeld: false, truncateMapIndex: false, release: () => releaseHeld?.(), requests, indexes, unavailable: new Set() };
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (['error', 'warning'].includes(message.type())) errors.push({ type: message.type(), text: message.text(), url: message.location().url });
    });
    await context.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      requests.push({ url: url.href, method: request.method(), range: request.headers().range || null });
      const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
      if (url.origin === baseURL && url.pathname === "/wdpa-monthly-execution.json") return json(state.execution);
      if (libraries.has(url.href)) {
        const [file, contentType] = libraries.get(url.href);
        return route.fulfill({ path: resolve(packageDir, 'node_modules', file), contentType });
      }
      if (/^https:\/\/tile\.openstreetmap\.org\/\d+\/\d+\/\d+\.png$/.test(url.href)) return route.fulfill({ body: basemapPNG, contentType: 'image/png' });
      if (/^https:\/\/services\.arcgisonline\.com\/ArcGIS\/rest\/services\/World_Imagery\/MapServer\/tile\/\d+\/\d+\/\d+$/.test(url.href)) return route.fulfill({body: basemapPNG, contentType: 'image/png'});
      const asset = catalog.assets.find((entry) => new URL(entry.release_index_url, baseURL).href === `${url.origin}${url.pathname}`);
      if (asset) return json(indexes.get(asset.slug));
      // Resolve package imports to the real browser libraries already loaded by the viewer.
      if (url.origin === baseURL && url.pathname === '/sdk/vendor-maplibre.js') return route.fulfill({contentType:'text/javascript', body:'export default window.maplibregl;'});
      if (url.origin === baseURL && url.pathname === '/sdk/vendor-pmtiles.js') return route.fulfill({contentType:'text/javascript', body:'export const PMTiles=window.pmtiles.PMTiles; export const Protocol=window.pmtiles.Protocol;'});
      if (url.origin === baseURL && url.pathname === '/sdk/maplibre.js') {
        const source = await readFile(resolve(siteDir, 'sdk/maplibre.js'), 'utf8');
        return route.fulfill({contentType:'text/javascript', body:source.replace("from 'maplibre-gl'", "from './vendor-maplibre.js'").replace("from 'pmtiles'", "from './vendor-pmtiles.js'")});
      }
      if (url.origin === baseURL && /^\/sdk\/[a-z-]+\.js$/.test(url.pathname)) {
        return route.fulfill({path: resolve(siteDir, url.pathname.slice(1)), contentType: 'text/javascript'});
      }
      if (url.origin === baseURL && url.pathname.startsWith('/api/comparisons')) {
        if (state.comparisonUnavailable) {
          expectedErrors.add(url.href);
          return route.fulfill({status:404,contentType:'text/plain',body:'Not found'});
        }
        const headers = {...request.headers(), 'X-Goog-Authenticated-User-Email': `accounts.google.com:${scenarioEmail}`};
        if (url.pathname.endsWith('/map-index')) {
          if (state.holdMapIndex) {
            state.holdMapIndex = false; state.mapIndexHeld = true;
            await new Promise(resolveHeld => { releaseHeld = resolveHeld; });
          }
          if (state.truncateMapIndex) {
            const response = await route.fetch({headers});
            const lines = (await response.text()).trimEnd().split('\n');
            lines.pop();
            return route.fulfill({status:200, contentType:'application/x-ndjson', body:lines.join('\n')+'\n'});
          }
          return route.continue({headers}); // Preserve the real streaming HTTP/gzip transport.
        }
        const response = await route.fetch({headers});
        if (state.holdComparison && request.method() === 'POST' && url.pathname === '/api/comparisons') {
          state.holdComparison = false; state.comparisonHeld = true;
          await new Promise(resolveHeld => { releaseHeld = resolveHeld; });
        }
        if (state.progress && (url.pathname === '/api/comparisons' || /^\/api\/comparisons\/[a-f0-9]{32}$/.test(url.pathname))) return json({...await response.json(), state:'running', progress:state.progress});
        return route.fulfill({response});
      }
      if (url.origin === baseURL && url.pathname.endsWith('.js') && !url.pathname.startsWith('/sdk/')) {
        const existing = new Set(['/app.js', '/map-preview.js', '/release-reference.js', '/compare-releases.js', '/workspace.js', '/workspace-contract.js']);
        if (!existing.has(url.pathname)) {
          forbidden.push(`Module unavailable in the previously deployed viewer: ${url.href}`);
          return route.fulfill({status:404, contentType:'text/plain', body:'Not found'});
        }
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
      // Stop new requests, then finish handlers while their fetch responses remain alive.
      await page.close();
      await context.unrouteAll({ behavior: 'wait' });
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

test('WDPA execution status preserves publication and refreshes running, cancellation and stale observations', async ({page, transport}) => {
  await page.clock.install();
  await page.goto(baseURL);
  for (const slug of ['wdpa-marine', 'wdpa-terrestrial']) {
    await page.locator(`#asset-list [data-slug="${slug}"]`).click();
    await expect(page.locator('#detail-execution-status')).toContainText('failed');
    await expect(page.locator('#detail-updated')).toHaveText(slug === 'wdpa-marine' ? '2026-10-01' : '2026-09-30');
  }
  transport.execution.latest_execution = {id: 'wdpa-monthly-running', state: 'running', started_at: new Date().toISOString()};
  await page.clock.fastForward(61_000);
  await expect(page.locator('#detail-execution-status')).toContainText('running');
  await expect(page.locator('#detail-execution-status')).toContainText('Last completed: failed');
  transport.execution.latest_execution.state = 'cancelled';
  transport.execution.observed_at = new Date(Date.now() - 30 * 60_000).toISOString();
  await page.clock.fastForward(61_000);
  await expect(page.locator('#detail-execution-status')).toContainText('cancelled');
  await expect(page.locator('#detail-execution-status')).toContainText('Stale observation');
});

test('comparison automatically takes over the primary map with compact tables and keyboard inspection', async ({page, transport}, testInfo) => {
  await select(page, 'comparison');
  await expect(page.locator('#use-section')).toBeVisible();
  await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#compare-open').focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#compare-before')).toBeFocused();
  await expect(page.locator('#compare-before')).toHaveValue('2026-01-01');
  await expect(page.locator('#compare-after')).toHaveValue('2026-09-22');
  await expect(page.locator('#version-select')).toBeHidden();
  await expect(page.locator('#compare-open')).toHaveText('Close comparison');
  await expect(page.locator('#compare-run, #compare-close, #compare-mode, #compare-maps')).toHaveCount(0);
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  await expect(page.locator('#compare-panel')).toBeHidden();
  await expect(page.locator('#compare-details')).toHaveAttribute('aria-expanded', 'false');
  const controls = await Promise.all(['#compare-before', '#compare-after', '#compare-open', '#compare-details'].map(id => page.locator(id).boundingBox()));
  expect(Math.max(...controls.map(b => b.y + b.height)) - Math.min(...controls.map(b => b.y + b.height))).toBeLessThan(2);
  expect(controls[3].x).toBeGreaterThan(controls[2].x + controls[2].width);
  expect(await page.locator('.compare-before-label').evaluate(n => getComputedStyle(n).color)).toBe('rgb(195, 59, 59)');
  expect(await page.locator('.compare-after-label').evaluate(n => getComputedStyle(n).color)).toBe('rgb(22, 129, 83)');

  await expect(page.locator('#compare-page')).toHaveText('1–50 of 106');
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  const canvas = page.locator('#map-preview canvas');
  await expect(page.locator('canvas')).toHaveCount(1); await expect(canvas).toBeVisible();
  await expect(page.locator('#compare-legend button').first()).toBeEnabled();
  expect(transport.requests.filter(r => new URL(r.url).pathname.endsWith('/map-index')).length).toBe(2);
  expect(transport.requests.filter(r => r.url.endsWith('/map'))).toHaveLength(0);
  expect(transport.requests.some(r => r.range && r.url.includes('/2026-01-01/') && r.url.includes('.pmtiles'))).toBe(true);
  expect(transport.requests.some(r => r.range && r.url.includes('/2026-09-22/') && r.url.includes('.pmtiles'))).toBe(true);
  // The primary map preserves the previous viewport, fitted to the after archive.
  const box = await canvas.boundingBox();
  const mercatorY = lat => (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2;
  const scale = Math.min((box.width - 68) / (3 / 360), (box.height - 68) / (mercatorY(-1) - mercatorY(1)), 512 * 2 ** 8);
  await canvas.click({position:{x:box.width / 2 - 1.5 / 360 * scale, y:box.height / 2 - (0.5 - mercatorY(1)) * scale}});
  await expect(page.locator('#feature-inspector')).toContainText('Shared before');
  await expect(page.locator('#feature-inspector')).toContainText('Shared after');
  await expect(page.locator('#compare-panel')).toBeHidden();
  await page.locator('#compare-details').click();
  await page.locator('#compare-search').fill('Shared');
  await expect(page.locator('#compare-page')).toHaveText('1–1 of 1');
  await page.locator('#compare-rows button').focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#compare-inspector')).toContainText('Absent');
  await expect(page.locator('#compare-inspector')).toContainText('null');
  await expect(page.locator('#compare-inspector tr.metadata-changed')).toHaveCount(2);
  await expect(page.locator('#compare-inspector tr.metadata-changed').first()).toContainText('name');
  await page.locator('#compare-search').fill(''); await page.locator('#compare-next').click();
  await expect(page.locator('#compare-page')).toHaveText('51–100 of 106');
  await expect(page.locator('#compare-report')).toHaveCount(0);
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

test('comparison filters wait for complete map indexes and truncated streams stay unavailable', async ({page, transport}) => {
  await select(page, 'comparison');
  transport.holdMapIndex = true;
  await page.locator('#compare-open').click();
  await expect.poll(() => transport.mapIndexHeld).toBe(true);
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  for (const category of ['novel','removed','metadata_changed','unchanged']) await expect(page.locator(`#compare-legend [data-change="${category}"]`)).toBeDisabled();
  await expect(page.locator('#compare-progress')).toBeVisible();
  await expect(page.locator('#compare-progress-label')).toHaveText('Loading map classifications');
  await page.locator('#basemap-select').selectOption('satellite');
  await expect(page.locator('#map-status')).toBeHidden();
  await expect(page.locator('#compare-legend button').first()).toBeDisabled();
  transport.release();
  await expect(page.locator('#compare-legend button').first()).toBeEnabled();
  await expect(page.locator('#compare-progress')).toBeHidden();
  expect(transport.requests.filter(r => new URL(r.url).pathname.endsWith('/map-index'))).toHaveLength(2);
  expect(transport.requests.filter(r => r.url.endsWith('/map'))).toHaveLength(0);
  await page.locator('#compare-open').click();
  transport.truncateMapIndex = true;
  await page.locator('#compare-open').click();
  await expect(page.locator('#compare-status')).toContainText('ended before completion');
  await expect(page.locator('#compare-progress')).toBeHidden();
  for (const category of ['novel','removed','metadata_changed','unchanged']) await expect(page.locator(`#compare-legend [data-change="${category}"]`)).toBeDisabled();
});

test('static catalog explains comparison availability and restores ordinary browsing', async ({page, transport}, testInfo) => {
  transport.comparisonUnavailable = true;
  await select(page, 'public');
  await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#compare-open').click();
  await expect(page.locator('#compare-status')).toContainText('authenticated catalog viewer');
  await expect(page.locator('#map-preview canvas')).toHaveCount(1);
  await expect(page.locator('#compare-table')).toBeHidden();
  await expect(page.locator('#compare-report')).toHaveCount(0);
  await clickPoint(page, false, testInfo); await expectMetadata(page, false);
  await expect(page.locator('#feature-inspector')).toContainText('After ·');
  await expect(page.locator('#compare-panel')).toBeHidden();
  await page.locator('#compare-details').click();
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
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  await expect(page.locator('#compare-page')).toHaveText('1–50 of 105');
  await expect(page.locator('#compare-map-note')).toContainText('Map inspection unavailable');
  await expect(page.locator('#map-preview canvas')).toHaveCount(0);
  await page.locator('#compare-details').click();
  await page.locator('#compare-search').fill('Old');
  await expect(page.locator('#compare-page')).toHaveText('1–2 of 2');
  await page.locator('#compare-rows button').first().click();
  await expect(page.locator('#compare-inspector')).toContainText('Old footprint');
  await expect(page.locator('#compare-map-note')).toContainText('Map inspection unavailable');
  transport.deny = 0;
  await page.locator('#compare-before').selectOption('2026-09-22');
  await page.locator('#compare-after').selectOption('2026-01-01');
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  await expect(page.locator('#map-preview canvas')).toHaveCount(1);
  await expect(page.locator('#compare-map-note')).not.toContainText('Map inspection unavailable');
});

test('automatic comparison rejects delayed starts and closing cancels pending work', async ({page, transport}) => {
  await select(page, 'public');
  transport.progress = {phase:'baseline', rows:38500, completed:38500, total:128786};
  await page.locator('#compare-open').click();
  const progress = page.locator('#compare-progress'), bar = page.locator('#compare-progress-bar');
  await expect(progress).toBeVisible();
  transport.progress = {phase:'baseline', rows:38500};
  await expect(bar).not.toHaveAttribute('value');
  transport.progress = {phase:'baseline', rows:38500, completed:38500, total:128786};
  await expect(page.locator('#compare-progress-label')).toHaveText('Validating Before');
  await expect(bar).toHaveAttribute('value','38500');
  await expect(bar).toHaveAttribute('max','128786');
  const rowBox = await page.locator('#version-path-row').boundingBox(), barBox = await bar.boundingBox();
  expect(barBox.width).toBeGreaterThan(rowBox.width - 35);
  expect(barBox.y + barBox.height).toBeLessThan(rowBox.y + rowBox.height);
  await test.info().attach('comparison-progress.png', {body:await progress.screenshot(),contentType:'image/png'});
  transport.progress = null;
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  await expect(progress).toBeHidden();
  await page.locator('#compare-open').click();
  transport.holdComparison = true;
  await page.locator('#compare-open').click();
  await expect.poll(() => transport.comparisonHeld).toBe(true);
  await page.locator('#compare-before').selectOption('2026-09-22');
  await page.locator('#compare-after').selectOption('2026-01-01');
  const releaseRow = page.locator('#compare-summary tbody tr').first();
  await expect(page.locator('#compare-summary')).toBeEmpty();
  const firstReleased = page.waitForResponse(response => response.url().endsWith('/api/comparisons') && response.request().method() === 'POST');
  const firstCancelled = page.waitForResponse(response => response.url().endsWith('/cancel'));
  transport.release();
  await (await firstReleased).finished(); expect((await firstCancelled).status()).toBe(200);
  await expect(releaseRow.locator('td').first()).toHaveText('2026-09-22');
  await expect(releaseRow.locator('td').last()).toHaveText('2026-01-01');
  transport.comparisonHeld = false; transport.holdComparison = true;
  await page.locator('#compare-after').selectOption('2026-09-22');
  await expect.poll(() => transport.comparisonHeld).toBe(true);
  const released = page.waitForResponse(response => response.url().endsWith('/api/comparisons') && response.request().method() === 'POST');
  const cancelled = page.waitForResponse(response => response.url().endsWith('/cancel'));
  await page.locator('#compare-open').click(); transport.release();
  await (await released).finished(); expect((await cancelled).status()).toBe(200);
  await expect(page.locator('#compare-panel')).toBeHidden();
  await expect(page.locator('#compare-summary')).toBeEmpty();
  await expect(page.locator('#version-select')).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(1);
});

test('polygons render red green yellow and faint gray across a generated ID reset', async ({page, transport}, testInfo) => {
  await select(page, 'polygons'); await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#compare-open').click();
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  await expect(page.locator('#compare-summary')).toContainText('Feature IDs cannot be matched');
  await expect(page.locator('#compare-summary')).toContainText('Unique geometries');
  await expect(page.locator('#compare-table')).toBeHidden();
  await expect(page.locator('canvas')).toHaveCount(1);
  const canvas = page.locator('#map-preview canvas');
  await expect(page.locator('#compare-legend button').first()).toBeEnabled();
  expect(transport.requests.filter(r => new URL(r.url).pathname.endsWith('/map-index'))).toHaveLength(2);
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

  const clickPolygon = (lon, lat) => canvas.click({position: {
    x: box.width / 2 + lon / 360 * scale,
    y: box.height / 2 + (mercatorY(lat) - (low + high) / 2) * scale,
  }});
  const hits = page.locator('#feature-inspector .feature-hit');
  // Both releases must be inspectable even when their feature IDs cannot be
  // joined. Transparent Before geometry is still an overlapping map hit.
  await clickPolygon(.5, .5);
  await expect(hits).toHaveCount(2);
  const before = hits.filter({hasText: 'Before ·'}), after = hits.filter({hasText: 'After ·'});
  await expect(before).toContainText('Shared before');
  await expect(before).toContainText('2026-01-01');
  await expect(after).toContainText('Shared after');
  await expect(after).toContainText('2026-09-22');
  await expect(page.locator('#compare-map-note')).toContainText('Geometry-only comparison');
  await expect(page.locator('#compare-legend [data-change="metadata_changed"]')).toHaveText('Contents differ here');
  await expect(hits.locator('.metadata-changed')).toHaveCount(0);
  await expect(page.locator('#compare-panel')).toBeHidden();
  await expect(page.locator('.map-click-target')).toHaveCount(1);
  await testInfo.attach('comparison-polygon-details.png', {body:await page.locator('#feature-inspector').screenshot(), contentType:'image/png'});
  await clickPolygon(-2.5, 2.5);
  // Identical properties in two release layers must not collapse to one hit.
  await expect(hits).toHaveCount(2);
  await expect(before).toContainText('2026-01-01');
  await expect(after).toContainText('2026-09-22');
  await expect(page.locator('#feature-inspector .metadata-changed')).toHaveCount(0);
  await clickPolygon(-1.5, -1);
  await expect(hits).toHaveCount(1);
  await expect(before).toBeVisible();
  await expect(after).toHaveCount(0);
  await clickPolygon(2.5, -1);
  // The same numeric ID belongs to a different Before polygon elsewhere.
  await expect(hits).toHaveCount(1);
  await expect(after).toBeVisible();
  await expect(before).toHaveCount(0);
  await clickPolygon(-1.5, 2.5);
  await expect(page.locator('#feature-inspector')).toBeHidden();
  await expect(page.locator('.map-click-target')).toHaveCount(0);
  await page.locator('#compare-details').click();
  await testInfo.attach('comparison-polygon-summary.png', {body:await page.locator('#compare-panel').screenshot(), contentType:'image/png'});
  await expect(page.locator('#compare-summary')).toContainText('New geometry');
});

test('category filters preserve the camera, extents zoom explicitly and changes draw above gray', async ({page}, testInfo) => {
  await select(page, 'overlap'); await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#compare-open').click();
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  const canvas = page.locator('#map-preview canvas'), legend = page.locator('#compare-legend');
  await expect(legend.locator('[data-change="metadata_changed"]')).toHaveText('Metadata changed');
  await expect(legend.locator('[data-change="novel"]')).toHaveText('Added / moved here');
  // Gray points occupy the same pixels as changes. Assert actual paint output
  // across both release sources, rather than testing style expression strings.
  await expect.poll(async () => { const image = await sampleCanvas(canvas); return comparisonColors.every(color => nearColor(image,color)); }).toBe(true);
  await testInfo.attach('comparison-overlapping-points.png', {body:await page.locator('#map-section').screenshot(), contentType:'image/png'});
  const union = await viewport(page), hits = page.locator('#feature-inspector .feature-hit');
  for (const [category, count, side, longitude] of [['novel',1,'After',1], ['removed',1,'Before',-1], ['metadata_changed',2,'',.1]]) {
    const beforeFilter = await viewport(page);
    await legend.locator(`[data-change="${category}"]`).click();
    await expect(legend.locator('[aria-pressed="true"]')).toHaveCount(1);
    await expect(page.locator('#compare-page')).toHaveText('1–1 of 1');
    expect(await viewport(page)).toEqual(beforeFilter);
    await page.getByRole('button',{name:'Zoom to extents',exact:true}).click();
    await expect.poll(async () => {
      const view = await viewport(page);
      return view.zoom > union.zoom + 1 && Math.abs(view.center[0] - longitude) < .02;
    }).toBe(true);
    await canvas.screenshot();
    const box = await canvas.boundingBox();
    await canvas.click({position:{x:box.width/2,y:box.height/2}});
    await expect(hits).toHaveCount(count);
    if (side) await expect(hits.first()).toContainText(`${side} ·`);
    if (category === 'metadata_changed') {
      await expect(hits.filter({hasText:'Before ·'})).toContainText('Shared before');
      await expect(hits.filter({hasText:'After ·'})).toContainText('Shared after');
      await expect(hits.locator('th.metadata-changed')).toHaveCount(3);
    }
    const image = await sampleCanvas(canvas), selected = category === 'novel' ? 1 : category === 'removed' ? 0 : 2;
    expect(nearColor(image, comparisonColors[selected])).toBe(true);
    expect(comparisonColors.filter((_,i) => i!==selected).some(color => nearColor(image,color))).toBe(false);
    if (category === 'metadata_changed') {
      // Reveal the distinct unchanged object at precisely the same coordinates.
      // Each release keeps both hits; only the matched edited object highlights.
      await legend.locator('[data-change="metadata_changed"]').click();
      await canvas.click({position:{x:box.width/2,y:box.height/2}});
      await expect(hits).toHaveCount(4);
      const unchanged = hits.filter({hasText:'grayCommon'});
      await expect(unchanged).toHaveCount(2);
      await expect(unchanged.locator('th.metadata-changed')).toHaveCount(0);
      await expect(hits.locator('th.metadata-changed')).toHaveCount(3);
      expect(await unchanged.first().evaluate(node => node.style.getPropertyValue('--feature-color'))).toBe('#949d97');
      await testInfo.attach('comparison-colocated-identities.png', {body:await page.locator('#feature-inspector').screenshot(), contentType:'image/png'});
    }
  }
});

test('unchanged filters survive basemap changes and release changes reset filters', async ({page, transport}) => {
  await select(page, 'overlap'); await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#compare-open').click();
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  const canvas = page.locator('#map-preview canvas'), legend = page.locator('#compare-legend');
  const union = await viewport(page);
  await legend.locator('[data-change="unchanged"]').click();
  await expect(page.locator('#compare-page')).toHaveText('1–3 of 3');
  expect(await viewport(page)).toEqual(union);
  await page.getByRole('button',{name:'Zoom to extents',exact:true}).click();
  await expect.poll(async () => (await viewport(page)).zoom).toBeLessThan(union.zoom + 1);
  await canvas.screenshot();
  {const image=await sampleCanvas(canvas);expect(comparisonColors.some(color=>nearColor(image,color))).toBe(false);}
  await page.locator('#basemap-select').selectOption('satellite');
  await expect(page.locator('#map-status')).toBeHidden();
  await expect(legend.locator('[data-change="unchanged"]')).toHaveAttribute('aria-pressed','true');
  await expect(legend.locator('[data-change="unchanged"]')).toBeEnabled();
  expect(transport.requests.filter(r => new URL(r.url).pathname.endsWith('/map-index'))).toHaveLength(2);
  {const image=await sampleCanvas(canvas);expect(comparisonColors.some(color=>nearColor(image,color))).toBe(false);}
  // Clicking the selected category restores all geometry and table rows.
  const beforeUnfilter = await viewport(page);
  await legend.locator('[data-change="unchanged"]').click();
  await expect(legend.locator('[aria-pressed="true"]')).toHaveCount(0);
  await expect(page.locator('#compare-page')).toHaveText('1–6 of 6');
  expect(await viewport(page)).toEqual(beforeUnfilter);
  await page.getByRole('button',{name:'Zoom to extents',exact:true}).click();
  await expect.poll(async () => {const image=await sampleCanvas(canvas);return comparisonColors.every(color=>nearColor(image,color));}).toBe(true);
  await legend.locator('[data-change="novel"]').click();
  await legend.locator('[data-change="removed"]').click();
  await expect(legend.locator('[aria-pressed="true"]')).toHaveCount(2);
  await page.locator('#compare-before').selectOption('2026-09-22');
  await expect(page.locator('#compare-summary tbody tr').first().locator('td').first()).toHaveText('2026-09-22');
  await expect(legend.locator('[aria-pressed="true"]')).toHaveCount(0);
  const emptyViewport = await viewport(page);
  await legend.locator('[data-change="novel"]').click();
  await expect(page.locator('#compare-page')).toHaveText('No matching features');
  expect(await viewport(page)).toEqual(emptyViewport);
});

test('comparison selection changes discard late polygon metadata', async ({page, transport}) => {
  await select(page, 'polygons');
  await expect(page.locator('#map-status')).toBeHidden();
  await page.locator('#compare-open').click();
  await expect(page.locator('#compare-summary table')).toHaveCount(3);
  const canvas = page.locator('#map-preview canvas'), box = await canvas.boundingBox();
  const mercatorY = lat => (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2;
  const low = mercatorY(-1.5), high = mercatorY(3), scale = Math.min((box.width - 68) / (6 / 360), (box.height - 68) / (low - high), 512 * 2 ** 8);
  const clickShared = () => canvas.click({position: {x: box.width / 2 + .5 / 360 * scale, y: box.height / 2 + (mercatorY(.5) - (low + high) / 2) * scale}});
  const hits = page.locator('#feature-inspector .feature-hit');
  transport.holdMetadata = true;
  await clickShared();
  await expect(hits).toHaveCount(2);
  await expect.poll(() => transport.held).toBe(true);
  await page.locator('#compare-before').selectOption('2026-09-22');
  await expect(page.locator('#feature-inspector')).toBeHidden();
  await expect(page.locator('#compare-summary tbody tr').first().locator('td').first()).toHaveText('2026-09-22');
  await expect(page.locator('#map-status')).toBeHidden();
  await clickShared();
  await expect(hits).toHaveCount(2);
  await expect(hits.filter({hasText: 'Before ·'})).toContainText('Shared after');
  await expect(hits.filter({hasText: 'After ·'})).toContainText('Shared after');
  const completed = page.waitForResponse(response => response.url().includes('/2026-01-01/') && response.url().includes('.metadata.ndjson.gz'));
  transport.release();
  await (await completed).finished();
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  await expect(page.locator('#feature-inspector')).not.toContainText('Shared before');
  await page.locator('#compare-open').click();
  await expect(page.locator('#feature-inspector')).toBeHidden();
  await expect(page.locator('.map-click-target')).toHaveCount(0);
});

test('inline dataset examples are short, copyable and execute a real map integration', async ({page, context}, testInfo) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await select(page, 'public');
  await expect(page.locator('#map-status')).toBeHidden();
  const section = page.locator('#use-section');
  await expect(section).toBeVisible();
  expect(await page.locator('#open-workspace, #open-workspace-file, #workspace-status, #save-workspace, #use-dataset-dialog, #use-install, #use-provenance, #use-export').count()).toBe(0);
  expect(await section.evaluate(node => node.nextElementSibling.className)).toBe('path-section');
  expect(await page.locator('#version-path-row').evaluate(node => node.previousElementSibling.id)).toBe('map-section');
  await page.locator('#use-copy-code').click();
  const python = await page.evaluate(() => navigator.clipboard.readText());
  expect(python.split('\n').length).toBe(4);
  expect(python).toContain('fetch_artifact');
  expect(python).toContain('#200');
  await page.locator('#use-tab-typescript').click();
  await page.locator('#use-copy-code').click();
  const code = await page.evaluate(() => navigator.clipboard.readText());
  expect(code.split('\n').length).toBe(5);
  expect(code).toContain('showDataset');
  expect(code).toContain('#201'); expect(code).toContain('#202');
  await expect(page.locator('#use-attribution, #use-copy-attribution')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Zoom to extents',exact:true})).toBeVisible();
  await testInfo.attach('inline-use-desktop.png', {body: await section.screenshot(), contentType: 'image/png'});
  await testInfo.attach('generated-python.py', {body: python, contentType: 'text/x-python'});
  await testInfo.attach('generated-typescript.ts', {body: code, contentType: 'text/plain'});
  const compiledCode = ts.transpileModule(code.replace(/^import .*;$/gm, ''), {compilerOptions:{target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext}}).outputText;
  const generated = await page.evaluate(async code => {
    const {showDataset} = await import('/sdk/maplibre.js');
    const container = document.createElement('div'); container.id='map'; container.style.height='400px'; document.body.append(container);
    const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
    const result = await new AsyncFunction('showDataset', code + '\nreturn dataset;')(showDataset);
    await new Promise((resolve, reject) => {result.map.once('idle',resolve);result.map.once('error',event=>reject(event.error));});
    window.generatedIntegration = result;
    return {ids:[...result.records.keys()],tileUrl:result.layer.tileUrl,metadataUrl:result.layer.metadata.url};
  }, compiledCode);
  expect(generated.ids).toEqual(['b1', 'b2']);
  expect(generated.tileUrl).toContain('generation=201'); expect(generated.metadataUrl).toContain('generation=202');
  await expect.poll(() => page.evaluate(() => window.generatedIntegration.map.queryRenderedFeatures().map(f => f.properties.feature_id))).toContain('b2');
  await testInfo.attach('generated-map.png', {body: await page.locator('#map').screenshot(), contentType:'image/png'});
  await page.evaluate(() => {window.generatedIntegration.map.remove();document.querySelector('#map').remove();});
  await page.locator('#version-select').selectOption('2026-01-01');
  await expect(page.locator('#use-typescript')).toContainText('#101');
  await expect(page.locator('#use-typescript')).toContainText('#102');
  // The description must span the header even with a long title and body.
  await page.evaluate(() => {
    document.querySelector('#detail-title').textContent='Large Scale International Boundaries';
    document.querySelector('#detail-description').textContent='Large Scale International Boundaries is an international boundary dataset. '.repeat(8);
  });
  const widths = await page.evaluate(() => [document.querySelector('.detail-header').clientWidth, document.querySelector('#detail-description').clientWidth]);
  expect(widths[1]).toBeGreaterThan(widths[0]*0.95);
  await page.locator('#detail-title').scrollIntoViewIfNeeded();
  await testInfo.attach('description-desktop.png', {body:await page.screenshot(),contentType:'image/png'});
  await page.setViewportSize({width:390,height:844});
  await section.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await testInfo.attach('inline-use-mobile.png', {body:await page.screenshot(),contentType:'image/png'});
});

test('single-release assets hide comparison while normal version selection remains usable', async ({page, transport}) => {
  const index = transport.indexes.get('smoke-public');
  index.releases = [index.releases[0]];
  await select(page, 'public');
  await expect(page.locator('#version-select')).toBeVisible();
  await expect(page.locator('#compare-open')).toBeHidden();
  await expect(page.locator('#map-status')).toBeHidden();
});
