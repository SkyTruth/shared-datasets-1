import { test, expect } from '@playwright/test';
import { baseURL } from './paths.mjs';

const identity = {'X-Goog-Authenticated-User-Email': 'accounts.google.com:viewer@skytruth.org'};
function fixture() {
  const groups = [{source: 'gcs_usage', activity: 'interest', application: 'Unknown', confidence: 'unknown', requests: 2, active_days: 1, bytes: 10}];
  const asset = (slug, title, state, entries, last) => ({slug, title, state, reason: 'Observed evidence for review', observed_from: '2025-01-01', last_read: last, last_interest: null, last_catalog: null, windows: {30: entries, 90: entries, 365: entries}});
  return {schema_version: 1, generated_at: new Date().toISOString(), observed_through: '2026-10-04', stale: false,
    disclaimer: 'Observed activity only; downloaded copies may remain in use.',
    cost: {target_monthly_usd: 25, estimated_monthly_usd: 12, state: 'within_target'},
    coverage: [{day: '2026-10-04', source: 'gcs_usage', state: 'healthy', reason: 'No known gaps'}],
    source_health: [{source: 'gcs_usage', window_days: 365, healthy_days: 364, gap_days: 1, pending_days: 0, unknown_days: 0}],
    assets: [asset('active', '<img src=x onerror=alert(1)> Active', 'activity_observed', groups, '2026-10-01T00:00:00Z'),
      asset('unused', 'Unused dataset', 'qualified_candidate', [], null)]};
}

test('usage endpoint enforces authentication and never exposes missing private summaries', async ({ request }) => {
  const anonymous = await request.get(`${baseURL}/api/usage`);
  expect(anonymous.status()).toBe(401);
  expect(anonymous.headers()['cache-control']).toBe('no-store');
  expect((await request.get(`${baseURL}/usage`)).status()).toBe(401);
  expect((await request.get(`${baseURL}/api/usage`, {headers: {'X-Goog-Authenticated-User-Email': 'outsider@example.org'}})).status()).toBe(403);
  const missing = await request.get(`${baseURL}/api/usage`, {headers: identity});
  expect(missing.status()).toBe(503);
  expect((await missing.json()).error).toContain('not been deployed');
  expect(missing.headers()['access-control-allow-origin']).toBeUndefined();
  expect((await request.get(`${baseURL}/usage/report.json`, {headers: identity})).status()).toBe(404);
});

test('usage report filters, sorts, and renders separate source evidence safely', async ({ page }) => {
  await page.setExtraHTTPHeaders(identity);
  await page.route('**/api/usage', route => route.fulfill({json: fixture()}));
  await page.goto(`${baseURL}/usage`);
  await expect(page.locator('#rows tr')).toHaveCount(2);
  await expect(page.locator('#rows tr').first()).toContainText('Unused dataset');
  await expect(page.locator('#rows img')).toHaveCount(0);
  await expect(page.locator('#rows')).toContainText('gcs_usage · interest · Unknown (unknown): 2 requests, 1 active days, 10 reported bytes');
  await page.locator('#application').selectOption('Unknown');
  await expect(page.locator('#rows tr')).toHaveCount(1);
  await expect(page.locator('#rows')).toContainText('Active');
  await page.locator('#application').selectOption('');
  await page.locator('#candidate').selectOption('qualified_candidate');
  await expect(page.locator('#rows')).toContainText('Unused dataset');
  await page.locator('#candidate').selectOption('');
  await page.locator('#search').fill('active');
  await expect(page.locator('#rows tr')).toHaveCount(1);
  await page.locator('#search').fill('');
  await page.locator('#sort').selectOption('name');
  await expect(page.locator('#rows tr').first()).toContainText('Active');
  await page.locator('#window').selectOption('90');
  await expect(page.locator('#rows')).toContainText('2 requests');
  await expect(page.locator('#coverage')).toContainText('No known gaps');
  await expect(page.locator('#source-health')).toContainText('364 healthy, 1 gaps');
});

test('usage page detects a dead worker independently and handles missing reports', async ({ page }) => {
  await page.setExtraHTTPHeaders(identity);
  const old = fixture();
  old.generated_at = new Date(Date.now() - 3 * 86400000).toISOString();
  await page.route('**/api/usage', route => route.fulfill({json: old}));
  await page.goto(`${baseURL}/usage`);
  await expect(page.locator('#status')).toContainText('Stale report');
  await page.locator('#candidate').selectOption('qualified_candidate');
  await expect(page.locator('#rows')).toContainText('No datasets match');
  await page.unroute('**/api/usage');
  await page.route('**/api/usage', route => route.fulfill({status: 503, json: {error: 'Usage report unavailable; activity is unknown'}}));
  await page.reload();
  await expect(page.locator('#status')).toContainText('Missing information never means zero use');
  await expect(page.locator('#rows tr')).toHaveCount(0);
});
