// Actual Stream5G playback. Media route uses an experimental DN→UPF→UE relay;
// no timings, packets, pauses or player events are simulated.
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const output = process.env.NWDAF_QOE_OUTPUT
const relay = process.env.NWDAF_QOE_RELAY
const phase = process.env.NWDAF_QOE_PHASE
if (!output || !/^http:\/\/127\.0\.0\.1:\d+$/.test(relay ?? '')) throw Error('Explicit local experiment configuration required')
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
const result = { phase, client: 'MAEstro Stream5G / Hls.js / HTMLVideoElement', media: [], errors: [] }
let page
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(process.env.MAESTRO_CHECK_TOKEN), url: 'http://127.0.0.1:5173' },
    { name: 'ems-user', value: JSON.stringify({ username: process.env.MAESTRO_CHECK_USER, role: process.env.NWDAF_QOE_ROLE ?? 'student', testbed: 'local' }), url: 'http://127.0.0.1:5173' },
  ])
  page = await context.newPage()
  page.on('pageerror', e => result.errors.push(e.message))
  await page.route('**/terminal/media/**', async route => {
    const url = new URL(route.request().url())
    const asset = url.pathname.split('/').pop()
    if (!/^(index\.m3u8|init\.mp4|seg\d{3}\.m4s)$/.test(asset)) return route.abort()
    try {
      const response = await fetch(`${relay}/media/${asset}`)
      const body = Buffer.from(await response.arrayBuffer())
      result.media.push({ asset, bytes: body.length, status: response.status, path: response.headers.get('x-maestro-path') })
      await route.fulfill({ status: response.status, body, contentType: response.headers.get('content-type') ?? 'application/octet-stream' })
    } catch (error) {
      result.media.push({ asset, status: 'transport_error', error: error.message })
      await route.abort('failed').catch(() => {})
    }
  })
  await page.goto('http://127.0.0.1:5173/')
  await page.getByRole('button', { name: 'Abrir terminal 5G' }).click()
  const selector = page.getByLabel('Cambiar terminal supervisado')
  await page.getByTitle('Stream5G', { exact: true }).waitFor({ timeout: 20000 })
  if (await selector.count()) await selector.selectOption('imsi-999700000000001')
  await page.getByTitle('Stream5G', { exact: true }).click()
  await page.locator('video').waitFor()
  await page.locator('video').evaluate(video => {
    const trace = window.__nwdafQoe = { events: [], samples: [], stalls: [], firstPlaying: null, waiting: null }
    for (const type of ['loadstart','play','playing','waiting','stalled','pause','ended','error','seeking','seeked']) {
      video.addEventListener(type, () => {
        const now = performance.now()
        trace.events.push({ type, monotonic_ms: now, utc_ms: Date.now(), current_time: video.currentTime, ready_state: video.readyState, paused: video.paused })
        if (type === 'waiting' && trace.firstPlaying !== null && !video.paused && !video.ended && trace.waiting === null) {
          trace.waiting = { start: video.currentTime, wall: now }
        }
        if (type === 'playing') {
          if (trace.firstPlaying === null) trace.firstPlaying = now
          if (trace.waiting !== null) {
            trace.stalls.push([trace.waiting.start, (now - trace.waiting.wall) / 1000])
            trace.waiting = null
          }
        }
      })
    }
    trace.timer = setInterval(() => trace.samples.push({ monotonic_ms: performance.now(), current_time: video.currentTime,
      ready_state: video.readyState, paused: video.paused, buffer_end: video.buffered.length ? video.buffered.end(video.buffered.length - 1) : 0 }), 200)
  })
  const ready = await fetch(`${relay}/start`, { method: 'POST' })
  if (!ready.ok) throw Error('Load preparation failed')
  await page.evaluate(() => { window.__nwdafQoe.requested = performance.now() })
  await page.getByRole('button', { name: 'Reproducir video', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('video')?.ended, null, { timeout: 75000 })
  result.status = 'PLAYED_TO_END'
} catch (error) {
  result.status = 'FAILED'
  result.error = error.message
} finally {
  if (page) {
    result.player = await page.evaluate(() => {
      const video = document.querySelector('video')
      const trace = window.__nwdafQoe
      if (!trace || !video) return null
      clearInterval(trace.timer)
      return { ...trace, timer: undefined, width: video.videoWidth, height: video.videoHeight, duration: video.duration,
        startup_seconds: trace.firstPlaying === null ? null : (trace.firstPlaying - trace.requested) / 1000,
        played_seconds: video.currentTime, quality: video.getVideoPlaybackQuality?.(), ended: video.ended,
        error: video.error?.message, muted: video.muted }
    }).catch(() => null)
    if (result.status !== 'PLAYED_TO_END') result.ui_text = await page.locator('body').innerText()
    await page.screenshot({ path: path.join(output, `${phase}-stream5g.png`) }).catch(() => {})
  }
  fs.writeFileSync(path.join(output, `${phase}-player.json`), JSON.stringify(result, null, 2))
  await browser.close()
  console.log(JSON.stringify({ phase, status: result.status, startup: result.player?.startup_seconds, stalls: result.player?.stalls, error: result.error }))
}
if (result.status !== 'PLAYED_TO_END') process.exitCode = 1
