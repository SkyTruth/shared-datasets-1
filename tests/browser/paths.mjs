import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const packageDir = dirname(fileURLToPath(import.meta.url));
export const repoDir = resolve(packageDir, '../..');
export const workDir = resolve(process.env.SHARED_DATASETS_WORKDIR || resolve(tmpdir(), 'shared-datasets-1'), 'catalog-browser-smoke');
export const siteDir = resolve(workDir, 'site');
export const baseURL = 'http://127.0.0.1:4179';
