import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
import { baseURL, packageDir, workDir } from './paths.mjs';

export default defineConfig({
  testDir: '.',
  testMatch: 'catalog.spec.mjs',
  timeout: 30000,
  expect: { timeout: 10000 },
  workers: 1,
  retries: 0,
  forbidOnly: true,
  outputDir: resolve(workDir, 'results'),
  reporter: [['list'], ['json', { outputFile: resolve(workDir, 'report.json') }]],
  use: {
    browserName: 'chromium',
    viewport: { width: 1440, height: 1000 },
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] },
  },
  webServer: { command: 'node serve.mjs', cwd: packageDir, url: baseURL, reuseExistingServer: false, timeout: 30000 },
});
