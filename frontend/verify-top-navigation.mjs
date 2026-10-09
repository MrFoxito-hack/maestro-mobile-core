import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const browser = await chromium.launch({headless:true,channel:'msedge'})
try {
  const page = await browser.newPage({viewport:{width:1920,height:1100}})
  const errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  const login=await page.request.post('http://127.0.0.1:8000/api/v1/auth/login',{data:{
    username:process.env.EMS_VERIFY_USER||'docente',password:process.env.EMS_VERIFY_PASSWORD||'teacher-change-me',
  }})
  assert.ok(login.ok())
  const auth=await login.json()
  await page.context().addCookies([
    {name:'thisisjustarandomstring',value:JSON.stringify(auth.access_token),url:'http://127.0.0.1:5173'},
    {name:'ems-user',value:JSON.stringify(auth.user),url:'http://127.0.0.1:5173'},
  ])
  await page.goto('http://127.0.0.1:5173/performance')
  const nav=page.getByRole('navigation',{name:'Navegación principal'})
  await nav.waitFor()
  assert.equal(await nav.getByRole('link').count(),9)
  assert.equal(await page.locator('[data-sidebar="sidebar"]').count(),0)
  for(const path of ['/commands','/alarms','/traces','/subscribers','/configuration','/audit','/topology','/','/performance']) {
    await nav.locator(`a[href="${path}"]`).click()
    await page.waitForURL('http://127.0.0.1:5173'+path)
    await page.locator('main').waitFor()
    assert.equal(await nav.locator(`a[href="${path}"]`).getAttribute('aria-current'),'page')
    assert.ok(await page.locator('main').evaluate(el=>Math.abs(el.getBoundingClientRect().width-innerWidth)<2))
  }
  await page.getByRole('button',{name:'Menú de usuario'}).click()
  await page.getByRole('menuitem',{name:'Cerrar sesión'}).waitFor()
  assert.equal(await page.getByRole('button',{name:'Toggle theme'}).count(),0)
  assert.ok(await page.locator('html').evaluate(el=>el.classList.contains('dark')))
  await page.screenshot({path:'../reportes/navegacion-superior.png',fullPage:true})
  for (const width of [1280,768,390]) {
    await page.setViewportSize({width,height:1000})
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No overflow at '+width)
    await nav.getByRole('link',{name:'Auditoría',exact:true}).click()
    await page.waitForURL('**/audit')
    await nav.getByRole('link',{name:'Performance',exact:true}).click()
    await page.waitForURL('**/performance')
  }
  assert.deepEqual(errors,[])
  console.log('PASS: nine sections, active route, full-width main, account menu, theme, tablet/mobile navigation. No operational writes.')
}finally{await browser.close()}
