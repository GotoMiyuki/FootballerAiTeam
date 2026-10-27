// Isolated headless browser/React verification with controlled lost responses.
import { resolve, join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { mkdir, writeFile, readFile } from 'node:fs/promises'
import { spawn } from 'node:child_process'
const repo = resolve(import.meta.dirname, '..')
const root = resolve(process.argv[2] || '')
if (!root.startsWith(join(repo, 'local_data', 'game_imports', 'm4-implementation') + '\\')) throw new Error('isolated root required')
await mkdir(root, { recursive: false })
await mkdir(join(root, 'temp'))
const childEnv = { ...process.env, TEMP: join(root, 'temp'), TMP: join(root, 'temp') }
const frontend = join(repo, 'frontend')
const { createServer } = await import(pathToFileURL(join(frontend, 'node_modules/vite/dist/node/index.js')))
const server = await createServer({ root: frontend, cacheDir: join(root, 'vite-cache'),
  server: { host: '127.0.0.1', port: 19585, strictPort: true, proxy: { '/api': { target: 'http://127.0.0.1:1' } } } })
async function run(command, args, name) {
  return new Promise((resolve, reject) => {
    const p = spawn(command, args, { cwd: frontend, env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] })
    let log = ''
    p.stdout.on('data', d => log += d)
    p.stderr.on('data', d => log += d)
    p.on('error', reject)
    p.on('exit', async code => {
      await writeFile(join(root, name + '.log'), log)
      resolve({ pid: p.pid, code })
    })
  })
}
const result = {}
try {
  await server.listen()
  const config = `import {defineConfig} from ${JSON.stringify(pathToFileURL(join(frontend,'node_modules/@playwright/test/index.mjs')).href)};
export default defineConfig({testDir:${JSON.stringify(join(frontend,'e2e'))},workers:1,timeout:30000,expect:{timeout:10000},
outputDir:${JSON.stringify(join(root,'playwright-results'))},reporter:[['json',{outputFile:${JSON.stringify(join(root,'playwright.json'))}}]],
use:{baseURL:'http://127.0.0.1:19585',headless:true,channel:'chrome'}});`
  await writeFile(join(root, 'playwright.config.mjs'), config)
  result.unit = await run(process.execPath, [join(frontend, 'node_modules/vitest/vitest.mjs'), 'run', 'src', '--reporter=json', '--outputFile=' + join(root, 'vitest.json')], 'vitest')
  result.browser = await run(process.execPath, [join(frontend, 'node_modules/@playwright/test/cli.js'), 'test', '--config', join(root, 'playwright.config.mjs'),
    ...(process.argv.slice(3).length ? process.argv.slice(3) : ['m45-runtime.spec.ts', 'm44-cancellation.spec.ts', 'm42-requests.spec.ts', 'session-semantics.spec.ts', 'contracts.spec.ts', 'reevaluation.spec.ts']), '--grep-invert', 'demo:'], 'playwright')
  result.browserStats = JSON.parse(await readFile(join(root, 'playwright.json'), 'utf8')).stats
  result.passed = result.unit.code === 0 && result.browser.code === 0
  process.exitCode = result.passed ? 0 : 1
} finally {
  await server.close()
  await writeFile(join(root, 'summary.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result))
}
