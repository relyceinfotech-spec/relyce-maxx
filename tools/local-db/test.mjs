// Disposable, loopback-only PostgreSQL + pgvector. Never reads backend/.env.
import { PGlite } from '@electric-sql/pglite';
import { vector } from '@electric-sql/pglite-pgvector';
import { PGLiteSocketServer } from '@electric-sql/pglite-socket';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { existsSync, mkdirSync } from 'node:fs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const live = process.argv[2] === '--live';
const db = await PGlite.create({ extensions: { vector } });
const server = new PGLiteSocketServer({ db, host: '127.0.0.1', port: 0, maxConnections: 20 });
let child;
let timeout;
try {
  await server.start();
  const url = new URL('postgresql://postgres@' + server.getServerConn());
  url.pathname = '/maxxx_test';
  url.searchParams.set('sslmode', 'disable');
  console.log('Local disposable PostgreSQL + pgvector ready. ' + (live ? 'Live providers enabled; hosted database disabled.' : 'Tests use offline provider settings.'));
  mkdirSync(resolve(root, 'backend/.local-state'), { recursive: true });
  const windowsPython = resolve(root, 'backend/.venv/Scripts/python.exe');
  const python = existsSync(windowsPython) ? windowsPython : resolve(root, 'backend/.venv/bin/python');
  const argumentsForPython = live ? ['-m', 'scripts.live_chat_smoke', ...process.argv.slice(3)] : [
    '-m', 'pytest', '-q', '-p', 'pytest_asyncio.plugin',
    '--junitxml=.local-state/local-tests.xml', ...process.argv.slice(2),
  ];
  child = spawn(python, argumentsForPython, {
    cwd: resolve(root, 'backend'), stdio: 'inherit',
    env: { ...process.env, TEST_DATABASE_URL: url.href, PYTEST_DISABLE_PLUGIN_AUTOLOAD: '1',
      MAXXX_LOCAL_TESTS: '1' },
  });
  timeout = setTimeout(() => {
    console.error('Local run exceeded its time limit; stopping the process.');
    child.kill();
  }, live ? 2_000_000 : 180_000);
  process.exitCode = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code) => resolve(code ?? 1));
  });
} finally {
  clearTimeout(timeout);
  if (child && child.exitCode === null) child.kill();
  await server.stop();
  await db.close();
  console.log('Local database discarded.');
}
