// Audit-only browser runner; isolated cache/artifacts, mocked APIs, headless browser.
import { resolve, join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { mkdir, writeFile, readFile } from 'node:fs/promises'
import { spawn } from 'node:child_process'
const repo = resolve(import.meta.dirname, '..')
const root = resolve(process.argv[2] || '')
if (!root.startsWith(join(repo, 'local_data', 'game_imports', 'm4-audit') + '\\')) throw new Error('isolated root required')
await mkdir(root, { recursive: false })
const frontend = join(repo, 'frontend')
const { createServer } = await import(pathToFileURL(join(frontend, 'node_modules/vite/dist/node/index.js')))
const { chromium } = await import(pathToFileURL(join(frontend, 'node_modules/playwright/index.mjs')))
const server = await createServer({ root: frontend, cacheDir: join(root, 'vite-cache'),
  server: { host: '127.0.0.1', port: 19573, strictPort: true, proxy: { '/api': { target: 'http://127.0.0.1:1' } } } })
let browser
let auditPage
const output = { node: process.version, errors: [], api_scope: 'all browser API requests intercepted; no game or real backend' }
try {
  await server.listen()
  const config = `import {defineConfig} from ${JSON.stringify(pathToFileURL(join(frontend,'node_modules/@playwright/test/index.mjs')).href)};
export default defineConfig({testDir:${JSON.stringify(join(frontend,'e2e'))},workers:1,timeout:30000,expect:{timeout:10000},
outputDir:${JSON.stringify(join(root,'playwright-results'))},reporter:[['json',{outputFile:${JSON.stringify(join(root,'playwright.json'))}}]],
use:{baseURL:'http://127.0.0.1:19573',headless:true,channel:'chrome'}});`
  await writeFile(join(root, 'playwright.config.mjs'), config)
  const args = [join(frontend,'node_modules/@playwright/test/cli.js'), 'test', '--config', join(root,'playwright.config.mjs'),
    'session-semantics.spec.ts','contracts.spec.ts','reevaluation.spec.ts','--grep-invert','demo:']
  output.existing_e2e = await new Promise((done, reject) => {
    const p = spawn(process.execPath, args, {cwd:frontend, stdio:['ignore','pipe','pipe']})
    let log = ''
    p.stdout.on('data',d=>log+=d)
    p.stderr.on('data',d=>log+=d)
    p.on('error',reject)
    p.on('exit',async code=>{await writeFile(join(root,'playwright.log'),log);done({pid:p.pid,code})})
  })
  // Exercise actual React hooks against delayed HTTP and ordered/old SSE callbacks.
  browser = await chromium.launch({channel:'chrome',headless:true})
  const page = await browser.newPage()
  auditPage=page
  page.setDefaultTimeout(10000)
  page.on('pageerror', e=>output.errors.push(e.message))
  output.requests=[]
  page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/')) output.requests.push(r.url())})
  page.on('console',m=>{if(m.type()==='error') output.errors.push(m.text())})
  const core = JSON.parse(await readFile(join(repo,'local_data/game_imports/m4-audit/20261026-01/core/core.json'),'utf8'))
  const final = {...core.model.final,id:'audit-mission',conversation_id:'audit-conversation',sequence:20}
  const running = {...final,status:'RUNNING',report:null,result:null,delivery_status:'NOT_GENERATED',sequence:10,available_operations:['view']}
  let served = running, hold = false, delayed = false, release
  const gate = new Promise(r=>release=r)
  await page.addInitScript(()=>{
    localStorage.setItem('fait.mission','audit-mission')
    window.auditStreams=[]
    window.EventSource=class {
      constructor(url){this.url=url;window.auditStreams.push(this);setTimeout(()=>this.onopen?.({}),0)}
      close(){this.closed=true}
    }
  })
  await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
    const path=new URL(route.request().url()).pathname
    if(path==='/api/missions/audit-mission'){
      const snapshot=hold?{...running,sequence:11}:served
      if(hold){delayed=true;await gate}
      return route.fulfill({json:snapshot})
    }
    const body=path==='/api/health'?{status:'ok',mode:'live'}:path==='/api/player'?{name:'Audit',attributes:{},metadata:{state_version:'isolated'}}:
      path==='/api/missions'?[served]:path.endsWith('/recommendations')?{availability:'AVAILABLE',items:[]}:[]
    await route.fulfill({json:body})
  })
  await page.goto('http://127.0.0.1:19573/')
  await page.getByRole('heading',{name:final.title,exact:true}).first().waitFor()
  await page.waitForTimeout(150)
  hold=true
  await page.evaluate(()=>window.auditStreams.at(-1).onerror({}))
  const end=Date.now()+5000
  while(!delayed&&Date.now()<end) await new Promise(r=>setTimeout(r,20))
  if(!delayed) throw new Error('controlled recovery request not observed')
  served=final
  await page.evaluate(snapshot=>window.auditStreams.at(-1).onmessage({data:JSON.stringify({event_id:'audit20',type:'mission.completed',mission_id:snapshot.id,
    timestamp:'2026-10-26T00:00:00Z',sequence:20,data:{snapshot}})}),final)
  await page.getByRole('button',{name:'查看报告',exact:true}).waitFor()
  release()
  hold=false
  await page.waitForTimeout(150)
  output.stale_http_preserved_completed=await page.getByRole('button',{name:'查看报告',exact:true}).isVisible()
  await page.evaluate(snapshot=>window.auditStreams.at(-1).onmessage({data:JSON.stringify({event_id:'audit-old',type:'mission.status_changed',mission_id:snapshot.id,
    timestamp:'2026-10-26T00:00:00Z',sequence:10,data:{snapshot}})}),running)
  output.old_event_preserved_completed=await page.getByRole('button',{name:'查看报告',exact:true}).isVisible()
  await page.reload()
  await page.getByRole('button',{name:'查看报告',exact:true}).waitFor()
  output.refresh_preserved_completed=true
  output.recovery_request_delayed=delayed
  await page.screenshot({path:join(root,'stale-refresh.png'),fullPage:true})
  output.browser_version=browser.version()
} catch(error) {
  output.harness_error=String(error.stack||error)
  if(auditPage){output.page_text=await auditPage.locator('body').innerText().catch(()=>null);await auditPage.screenshot({path:join(root,'harness-error.png')}).catch(()=>{})}
  process.exitCode=1
} finally {
  await browser?.close()
  await server.close()
  await writeFile(join(root,'frontend.json'),JSON.stringify(output,null,2))
  console.log(JSON.stringify(output))
}
