import { newRequestId } from '@/lib/request-id'

type PlaybackState = 'loading' | 'playing' | 'buffering' | 'paused' | 'ended' | 'stopped'

export type PlaybackObservation = {
  imsi: string
  session_id: string
  sequence: number
  profile: '720p' | '1080p'
  state: PlaybackState
  startup_seconds: number | null
  played_seconds: number
  rebuffer_count: number
  rebuffer_seconds: number
  received_bytes: number
}

export function observePlayback(
  video: HTMLVideoElement,
  imsi: string,
  profile: '720p' | '1080p',
  publish: (observation: PlaybackObservation) => void
) {
  const normalizedImsi = imsi.startsWith('imsi-') ? imsi : `imsi-${imsi}`
  const started = performance.now()
  const data: PlaybackObservation = {
    imsi: normalizedImsi, profile, session_id: newRequestId(), sequence: 0,
    state: 'loading', startup_seconds: null, played_seconds: 0,
    rebuffer_count: 0, rebuffer_seconds: 0, received_bytes: 0,
  }
  let bufferingSince: number | null = null
  let disposed = false
  const closeBuffer = () => {
    if (bufferingSince !== null) {
      data.rebuffer_seconds += (performance.now() - bufferingSince) / 1000
      bufferingSince = null
    }
  }
  const emit = () => {
    if (disposed) return
    let played = 0
    for (let i = 0; i < video.played.length; i++) played += video.played.end(i) - video.played.start(i)
    data.played_seconds = played
    publish({ ...data, sequence: data.sequence++,
      rebuffer_seconds: data.rebuffer_seconds + (bufferingSince === null ? 0 : (performance.now() - bufferingSince) / 1000) })
  }
  const playing = () => {
    data.startup_seconds ??= (performance.now() - started) / 1000
    closeBuffer()
    data.state = 'playing'
    emit()
  }
  const waiting = () => {
    if (video.paused || video.seeking) return
    if (data.startup_seconds !== null && bufferingSince === null) {
      bufferingSince = performance.now()
      data.rebuffer_count++
    }
    data.state = 'buffering'
  }
  const pause = () => { closeBuffer(); data.state = video.ended ? 'ended' : 'paused'; emit() }
  const seeking = () => { closeBuffer() }
  const ended = () => { closeBuffer(); data.state = 'ended'; emit() }
  const handlers = { playing, waiting, pause, seeking, ended }
  for (const [name, handler] of Object.entries(handlers)) video.addEventListener(name, handler)
  const interval = window.setInterval(emit, 5000)
  emit()
  return {
    received(bytes: number) { data.received_bytes += bytes },
    dispose() {
      if (disposed) return
      closeBuffer()
      if (data.state !== 'ended') data.state = 'stopped'
      emit()
      disposed = true
      window.clearInterval(interval)
      for (const [name, handler] of Object.entries(handlers)) video.removeEventListener(name, handler)
    },
  }
}
