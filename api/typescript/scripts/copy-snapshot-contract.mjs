import {readFile, writeFile} from 'node:fs/promises';
const source = new URL('../../../web/catalog/workspace-contract.js', import.meta.url);
const target = new URL('../src/snapshot-contract.ts', import.meta.url);
await writeFile(target, '// @ts-nocheck\n// Generated from web/catalog/workspace-contract.js by the build.\n' + await readFile(source, 'utf8'));
