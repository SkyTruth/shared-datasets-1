import { spawn, spawnSync } from 'node:child_process';
import { repoDir, workDir } from './paths.mjs';

const built = spawnSync('uv', ['run', '--no-sync', 'python', 'tests/browser/build_fixture.py', workDir], { cwd: repoDir, stdio: 'inherit' });
if (built.status !== 0) process.exit(built.status || 1);
const server = spawn('uv', ['run', '--no-sync', 'python', 'tests/browser/comparison_server.py', workDir], { cwd: repoDir, stdio: 'inherit' });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.kill(signal));
server.on('exit', (code) => process.exit(code || 0));
