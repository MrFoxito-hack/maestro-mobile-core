// C6 acquisition: real Edge decoder, pinned project Hls.js, UE-only media relay.
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'
const [relay, output] = process.argv.slice(2)
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(relay)) throw Error('local relay required')
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
const page = await browser.newPage()
let result
try {
  await page.goto(relay)
  await page.addScriptTag({ path: path.resolve('node_modules/hls.js/dist/hls.min.js') })
  await page.evaluate(() => {
    const video = document.querySelector('video')
    const trace = window.trace = { events: [], samples: [], stalls: [], fragments: [], switches: [], errors: [], firstPlaying: null, waiting: null }
    for (const type of ['playing', 'waiting', 'stalled', 'ended', 'pause', 'seeking', 'error']) video.addEventListener(type, () => {
      const now = performance.now()
      trace.events.push({ type, monotonic_ms: now, utc_ms: Date.now(), current_time: video.currentTime })
      if (type === 'waiting' && trace.firstPlaying !== null && !video.paused && !video.ended && trace.waiting === null) trace.waiting = { start: video.currentTime, wall: now }
      if (type === 'playing') {
        if (trace.firstPlaying === null) trace.firstPlaying = now
        if (trace.waiting) { trace.stalls.push([trace.waiting.start, (now-trace.waiting.wall)/1000]); trace.waiting = null }
      }
    })
    const hls = window.hls = new Hls({ startLevel: 1, maxBufferLength: 4, maxMaxBufferLength: 6, backBufferLength: 0 })
    trace.hls_version = Hls.version
    hls.on(Hls.Events.FRAG_CHANGED, (_, { frag }) => trace.fragments.push({ start: frag.start, duration: frag.duration, level: frag.level, sn: frag.sn, url: frag.url, media_time: video.currentTime }))
    hls.on(Hls.Events.LEVEL_SWITCHED, (_, data) => trace.switches.push({ level: data.level, media_time: video.currentTime, monotonic_ms: performance.now() }))
    hls.on(Hls.Events.ERROR, (_, data) => trace.errors.push({ type: data.type, details: data.details, fatal: data.fatal }))
    trace.timer = setInterval(() => trace.samples.push({ time: performance.now(), media_time: video.currentTime, buffer_end: video.buffered.length ? video.buffered.end(video.buffered.length-1) : 0 }), 200)
    trace.requested = performance.now()
    hls.loadSource('/media/master.m3u8'); hls.attachMedia(video)
    hls.on(Hls.Events.MANIFEST_PARSED, () => video.play())
  })
  await page.waitForFunction(() => document.querySelector('video').ended, null, { timeout: 110000 })
} catch (error) {
  result = { failure: error.message }
} finally {
  result = { ...result, ...await page.evaluate(() => {
    const t = window.trace, v = document.querySelector('video')
    if (!t) return { ended: false }
    clearInterval(t.timer)
    return { ...t, timer: undefined, ended: v.ended, played_seconds: v.currentTime, duration: v.duration,
      startup_seconds: t.firstPlaying === null ? null : (t.firstPlaying-t.requested)/1000,
      quality: v.getVideoPlaybackQuality(), width: v.videoWidth, height: v.videoHeight, user_agent: navigator.userAgent }
  }) }
  await page.screenshot({ path: output.replace(/\.json$/, '.png') })
  fs.writeFileSync(output, JSON.stringify(result, null, 2))
  await browser.close()
}
console.log(JSON.stringify({ ended: result.ended, startup: result.startup_seconds, stalls: result.stalls, switches: result.switches, failure: result.failure }))
if (!result.ended) process.exitCode = 1
