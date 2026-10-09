import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import Hls from 'hls.js'
import {
  AlertCircle,
  CreditCard,
  GripHorizontal,
  Loader2,
  Maximize2,
  Pause,
  Play,
  Square,
  Volume2,
  VolumeX,
  X,
  Zap,
} from 'lucide-react'
import { useAuthStore } from '@/stores/auth-store'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { observePlayback } from './playback-observation'

export interface VideoLabProps {
  connected: boolean
  exhausted: boolean
  afBoost?: {
    active: boolean
    qos: '5QI=2' | '5QI=9'
    pcc_rule?: string | null
    session_url?: string | null
    gbr_dl?: string
    mbr_dl?: string
    applied_at?: string
    note?: string
  }
  onToggleAfBoost?: (enable: boolean) => Promise<unknown>
  isAfBoostPending?: boolean
  selectedImsi?: string | null
  isPip?: boolean
  onOpenStream?: () => void
  onOpenBalance?: () => void
  onActiveChange?: (active: boolean) => void
}

export function VideoLab({
  connected,
  exhausted,
  afBoost,
  onToggleAfBoost,
  isAfBoostPending = false,
  selectedImsi,
  isPip = false,
  onOpenStream,
  onOpenBalance,
  onActiveChange,
}: VideoLabProps) {
  const video = useRef<HTMLVideoElement>(null)
  const player = useRef<Hls | null>(null)
  const observation = useRef<ReturnType<typeof observePlayback> | null>(null)
  const generation = useRef(0)
  const lastRefresh = useRef(0)
  const recoveryAttempts = useRef(0)
  const pip = useRef<HTMLDivElement>(null)
  const pipDrag = useRef({
    pointerId: -1,
    offsetX: 0,
    offsetY: 0,
    originX: 0,
    originY: 0,
    moved: false,
  })
  const queries = useQueryClient()

  const [qualityMode, setQualityMode] = useState<'720p' | '1080p' | '4k'>(
    afBoost?.active ? '4k' : '720p'
  )
  const [active, setActive] = useState(false)
  const [isBuffering, setIsBuffering] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [notice, setNotice] = useState('')
  const [received, setReceived] = useState(0)
  const [isMuted, setIsMuted] = useState(true)
  const [pipCoordinates, setPipCoordinates] = useState<{ x: number; y: number } | null>(null)

  useEffect(() => {
    onActiveChange?.(active)
  }, [active, onActiveChange])

  // Keep local quality in sync if external AF state changes
  useEffect(() => {
    if (afBoost?.active && qualityMode !== '4k') {
      setQualityMode('4k')
    } else if (!afBoost?.active && qualityMode === '4k') {
      setQualityMode('720p')
    }
  }, [afBoost?.active])

  useEffect(
    () => () => {
      generation.current++
      observation.current?.dispose()
      player.current?.destroy()
      player.current = null
    },
    []
  )

  useEffect(() => {
    if (!isPip || !active) return

    const keepInsidePhone = () => {
      const pipElement = pip.current
      const phoneScreen = pipElement?.closest('main')
      if (!pipElement || !phoneScreen) return

      const screenRect = phoneScreen.getBoundingClientRect()
      const pipRect = pipElement.getBoundingClientRect()
      const edge = 8
      const minX = screenRect.left + edge
      const minY = screenRect.top + edge
      const maxX = Math.max(minX, screenRect.right - pipRect.width - edge)
      const maxY = Math.max(minY, screenRect.bottom - pipRect.height - edge)
      const originX = pipRect.left - pipElement.offsetLeft
      const originY = pipRect.top - pipElement.offsetTop
      const viewportX = Math.min(maxX, Math.max(minX, pipRect.left))
      const viewportY = Math.min(maxY, Math.max(minY, pipRect.top))

      setPipCoordinates({
        x: viewportX - originX,
        y: viewportY - originY,
      })
    }

    const frame = window.requestAnimationFrame(keepInsidePhone)
    const observer = new ResizeObserver(keepInsidePhone)
    const phoneScreen = pip.current?.closest('main')
    if (phoneScreen) observer.observe(phoneScreen)
    window.addEventListener('resize', keepInsidePhone)

    return () => {
      window.cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', keepInsidePhone)
    }
  }, [active, isPip])

  const movePip = (clientX: number, clientY: number) => {
    const pipElement = pip.current
    const phoneScreen = pipElement?.closest('main')
    if (!pipElement || !phoneScreen) return

    const screenRect = phoneScreen.getBoundingClientRect()
    const pipRect = pipElement.getBoundingClientRect()
    const edge = 8
    const minX = screenRect.left + edge
    const minY = screenRect.top + edge
    const maxX = Math.max(minX, screenRect.right - pipRect.width - edge)
    const maxY = Math.max(minY, screenRect.bottom - pipRect.height - edge)
    const originX = pipRect.left - pipElement.offsetLeft
    const originY = pipRect.top - pipElement.offsetTop
    const viewportX = Math.min(maxX, Math.max(minX, clientX - pipDrag.current.offsetX))
    const viewportY = Math.min(maxY, Math.max(minY, clientY - pipDrag.current.offsetY))

    setPipCoordinates({
      x: viewportX - originX,
      y: viewportY - originY,
    })
  }

  const beginPipDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!isPip || event.button !== 0) return
    if ((event.target as HTMLElement).closest('button')) return

    const pipRect = pip.current?.getBoundingClientRect()
    if (!pipRect) return

    pipDrag.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - pipRect.left,
      offsetY: event.clientY - pipRect.top,
      originX: event.clientX,
      originY: event.clientY,
      moved: false,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    event.preventDefault()
    event.stopPropagation()
  }

  const dragPip = (event: React.PointerEvent<HTMLDivElement>) => {
    if (pipDrag.current.pointerId !== event.pointerId) return

    if (
      Math.hypot(
        event.clientX - pipDrag.current.originX,
        event.clientY - pipDrag.current.originY
      ) > 3
    ) {
      pipDrag.current.moved = true
    }
    movePip(event.clientX, event.clientY)
    event.preventDefault()
    event.stopPropagation()
  }

  const endPipDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (pipDrag.current.pointerId !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    pipDrag.current.pointerId = -1
    if (pipDrag.current.moved) {
      window.setTimeout(() => {
        pipDrag.current.moved = false
      }, 0)
    }
    event.stopPropagation()
  }

  useEffect(() => {
    if (!connected || exhausted) {
      observation.current?.dispose()
      player.current?.stopLoad()
      video.current?.pause()
      setActive(false)
    } else if (connected && !exhausted) {
      setNotice((prev) =>
        prev === 'Transmisión interrumpida por política de red o cuota agotada.'
          ? ''
          : prev
      )
    }
  }, [connected, exhausted])

  const stop = () => {
    observation.current?.dispose()
    observation.current = null
    generation.current++
    player.current?.destroy()
    player.current = null
    if (video.current) {
      video.current.pause()
      video.current.currentTime = 0
    }
    setActive(false)
    setIsBuffering(false)
    setIsPaused(false)
    setNotice('')
    recoveryAttempts.current = 0
  }

  const handleSelectQuality = async (newQuality: '720p' | '1080p' | '4k') => {
    setQualityMode(newQuality)
    if (newQuality === '4k' && !afBoost?.active) {
      try {
        await onToggleAfBoost?.(true)
      } catch (err) {
        console.error('Failed to enable AF Boost:', err)
        setQualityMode('720p')
      }
    } else if (newQuality !== '4k' && afBoost?.active) {
      try {
        await onToggleAfBoost?.(false)
      } catch (err) {
        console.error('Failed to disable AF Boost:', err)
        setQualityMode('4k')
      }
    }

    if (active) {
      // Reload stream smoothly with the new profile
      setTimeout(() => {
        startStream(newQuality === '4k' ? '1080p' : newQuality)
      }, 200)
    }
  }

  const startStream = (targetProfile: '720p' | '1080p') => {
    if (!video.current) {
      setNotice('El reproductor no está disponible.')
      return
    }
    if (!Hls.isSupported()) {
      setNotice('Este navegador no admite reproducción HLS protegida.')
      return
    }

    stop()
    const runId = ++generation.current
    if (!selectedImsi) {
      setNotice('Seleccione un terminal autorizado.')
      return
    }
    const activeImsi = selectedImsi.startsWith('imsi-')
      ? selectedImsi
      : `imsi-${selectedImsi}`
    observation.current = observePlayback(video.current, activeImsi, targetProfile, (data) => {
      void api.post('/terminal/media/experience', data).catch(() => {
        // Analytics failures must not interrupt media playback.
      })
    })
    setNotice('Conectando…')
    setReceived(0)
    setActive(true)
    setIsBuffering(true)
    setIsPaused(false)

    try {
      // Ensure DOM element is explicitly muted so autoplay is never blocked
      video.current.muted = isMuted
      video.current.defaultMuted = isMuted

      const imsiParam = `?imsi=${encodeURIComponent(activeImsi)}`
      const source = new URL(
        api.getUri({ url: `/terminal/media/${targetProfile}/index.m3u8${imsiParam}` }),
        window.location.href
      )
      const prefix = source.pathname.slice(0, source.pathname.lastIndexOf('/') + 1)

      const robustRetry = {
        default: {
          maxTimeToFirstByteMs: 15000,
          maxLoadTimeMs: 25000,
          timeoutRetry: {
            maxNumRetry: 4,
            retryDelayMs: 250,
            maxRetryDelayMs: 1500,
          },
          errorRetry: {
            maxNumRetry: 4,
            retryDelayMs: 250,
            maxRetryDelayMs: 1500,
          },
        },
      }

      const hls = new Hls({
        maxBufferLength: 30,
        maxMaxBufferLength: 60,
        maxBufferSize: 60_000_000,
        backBufferLength: 15,
        enableWorker: true,
        startFragPrefetch: true,
        progressive: true,
        highBufferWatchdogPeriod: 1.5,
        nudgeOffset: 0.1,
        nudgeMaxRetry: 5,
        maxBufferHole: 0.5,
        fragLoadPolicy: robustRetry,
        manifestLoadPolicy: robustRetry,
        playlistLoadPolicy: robustRetry,
        fetchSetup: (context, initParams) => {
          const target = new URL(context.url, source)
          if (target.origin !== source.origin || !target.pathname.startsWith(prefix)) {
            throw new Error('Destino no autorizado')
          }
          target.searchParams.set('imsi', activeImsi)
          const token = useAuthStore.getState().auth.accessToken
          if (!token) throw new Error('Sesión no autorizada')
          const headers = new Headers(initParams.headers)
          headers.set('Authorization', `Bearer ${token}`)
          return new Request(target, { ...initParams, headers })
        },
        xhrSetup: (xhr, url) => {
          const target = new URL(url, source)
          if (target.origin !== source.origin || !target.pathname.startsWith(prefix)) {
            throw new Error('Destino no autorizado')
          }
          target.searchParams.set('imsi', activeImsi)
          xhr.open('GET', target.href, true)
          const token = useAuthStore.getState().auth.accessToken
          if (!token) throw new Error('Sesión no autorizada')
          xhr.setRequestHeader('Authorization', `Bearer ${token}`)
        },
      })

      player.current = hls

      hls.on(Hls.Events.MANIFEST_LOADING, () => {
        if (generation.current !== runId) return
        setIsBuffering(true)
        setNotice('Preparando video…')
      })

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        if (generation.current !== runId) return
        setNotice('Reproduciendo por 5G')
        if (video.current) {
          video.current.muted = isMuted
          const p = video.current.play()
          if (p !== undefined) {
            p.then(() => {
              setIsBuffering(false)
              setIsPaused(false)
            }).catch((err) => {
              console.warn('Autoplay error:', err)
              setIsBuffering(false)
              setIsPaused(true)
              setNotice('Toca para iniciar reproducción')
            })
          }
        }
      })

      hls.on(Hls.Events.FRAG_LOADING, () => {
        if (generation.current !== runId) return
        if (!video.current || video.current.paused || video.current.readyState < 3) {
          setIsBuffering(true)
        }
      })

      hls.on(Hls.Events.FRAG_LOADED, (_, event) => {
        if (generation.current !== runId) return
        recoveryAttempts.current = 0
        setIsBuffering(false)
        setReceived((value) => value + event.frag.stats.loaded)
        observation.current?.received(event.frag.stats.loaded)
        if (Date.now() - lastRefresh.current > 4000) {
          lastRefresh.current = Date.now()
          void queries.invalidateQueries({ queryKey: ['charging'] })
          void queries.invalidateQueries({ queryKey: ['nwdaf', 'experience'] })
        }
      })

      hls.on(Hls.Events.ERROR, (_, event) => {
        if (generation.current !== runId) return
        if (!event.fatal) {
          return
        }

        const halt = (message: string) => {
          observation.current?.dispose()
          hls.stopLoad()
          video.current?.pause()
          if (player.current === hls) player.current = null
          hls.destroy()
          setActive(false)
          setIsBuffering(false)
          setIsPaused(false)
          setNotice(message)
        }

        if (event.type === Hls.ErrorTypes.NETWORK_ERROR) {
          const statusCode = (event as { response?: { code?: number } }).response?.code
          if (statusCode === 401) {
            halt('La sesión del EMS expiró. Inicia sesión nuevamente.')
          } else if (statusCode === 409 || statusCode === 403) {
            void queries.invalidateQueries({ queryKey: ['charging'] })
            void queries.invalidateQueries({ queryKey: ['terminal-status'] })
            halt('Reproducción bloqueada por red o cuota.')
          } else if (statusCode === 502 || statusCode === 503) {
            halt('El enlace N6 no está disponible.')
          } else if (recoveryAttempts.current < 2) {
            recoveryAttempts.current += 1
            setNotice('Reconectando…')
            window.setTimeout(() => {
              if (generation.current === runId) hls.startLoad()
            }, 500 * recoveryAttempts.current)
          } else {
            console.warn('HLS network recovery exhausted:', event)
            halt('No se pudo cargar el video. Intenta nuevamente.')
          }
        } else if (event.type === Hls.ErrorTypes.MEDIA_ERROR) {
          if (recoveryAttempts.current < 1) {
            recoveryAttempts.current += 1
            hls.recoverMediaError()
          } else {
            halt('El navegador no pudo decodificar el video.')
          }
        } else {
          halt('No se pudo iniciar la reproducción.')
        }
      })

      hls.attachMedia(video.current)
      hls.loadSource(source.href)
    } catch {
      setActive(false)
      setIsBuffering(false)
      setNotice('Error al inicializar la reproducción de video')
    }
  }

  const handleStart = () => {
    setNotice('')
    const profile = qualityMode === '4k' ? '1080p' : qualityMode
    startStream(profile)
  }

  const toggleMute = () => {
    if (video.current) {
      video.current.muted = !video.current.muted
      setIsMuted(video.current.muted)
    }
  }

  if (isPip && !active) {
    return null
  }

  return (
    <div
      className={cn(
        isPip ? 'contents' : 'flex flex-col gap-3 animate-in fade-in duration-150'
      )}
    >
      {/* Video Streaming Player Card (Full View or Floating PiP) */}
      <div
        ref={pip}
        onPointerDown={beginPipDrag}
        onPointerMove={dragPip}
        onPointerUp={endPipDrag}
        onPointerCancel={endPipDrag}
        style={
          isPip && pipCoordinates
            ? { left: pipCoordinates.x, top: pipCoordinates.y }
            : undefined
        }
        className={cn(
          'overflow-hidden bg-black select-none',
          isPip
            ? cn(
                'fixed z-50 w-44 touch-none cursor-grab active:cursor-grabbing rounded-2xl shadow-[0_16px_40px_rgba(0,0,0,0.95),0_0_0_1px_rgba(255,255,255,0.2)] ring-1 ring-white/20 transition-shadow duration-150',
                !pipCoordinates && 'right-3 bottom-14'
              )
            : 'relative rounded-2xl shadow-xl ring-1 ring-white/10 transition-all duration-300'
        )}
      >
        {/* If isPip && active: Mini PiP Top Floating Controls */}
        {isPip && active && (
          <div className='absolute top-0 inset-x-0 z-30 flex items-center justify-between p-1.5 bg-gradient-to-b from-black/90 via-black/50 to-transparent'>
            <div className='flex items-center gap-1'>
              {afBoost?.active ? (
                <span className='rounded bg-amber-400 px-1 py-0.5 text-[8px] font-black text-black leading-none'>
                  5G+
                </span>
              ) : (
                <span className='rounded bg-white/20 px-1 py-0.5 text-[8px] font-semibold text-white leading-none'>
                  5QI=9
                </span>
              )}
            </div>

            <div className='flex items-center gap-1'>
              <span
                className='flex h-5 w-7 items-center justify-center text-white/55'
                aria-hidden='true'
              >
                <GripHorizontal className='size-3.5' />
              </span>

              {/* Maximize / Open Stream5G */}
              <button
                type='button'
                onClick={(e) => {
                  e.stopPropagation()
                  onOpenStream?.()
                }}
                className='flex size-5 items-center justify-center rounded-full bg-black/60 text-white/80 hover:text-white transition cursor-pointer'
                title='Abrir Stream5G'
              >
                <Maximize2 className='size-2.5' />
              </button>

              {/* Stop video */}
              <button
                type='button'
                onClick={(e) => {
                  e.stopPropagation()
                  stop()
                }}
                className='flex size-5 items-center justify-center rounded-full bg-black/60 text-rose-400 hover:bg-rose-500/20 transition cursor-pointer'
                title='Detener video'
              >
                <X className='size-3' />
              </button>
            </div>
          </div>
        )}

        {/* Video Element (Same DOM node preserved across all views) */}
        <div
          className={cn(
            'relative aspect-video w-full bg-black flex items-center justify-center overflow-hidden',
            isPip && 'cursor-pointer'
          )}
          onClick={() => {
            if (isPip) {
              if (pipDrag.current.moved) {
                pipDrag.current.moved = false
                return
              }
              onOpenStream?.()
            } else if (active && video.current) {
              if (video.current.paused) {
                void video.current.play()
              } else {
                video.current.pause()
              }
            }
          }}
          title={isPip ? 'Arrastra para mover o toca para abrir Stream5G' : undefined}
        >
          <video
            ref={video}
            muted={isMuted}
            playsInline
            preload='auto'
            className={cn(
              'size-full object-cover transition-opacity duration-300',
              (!active || isBuffering) && 'opacity-60'
            )}
            onWaiting={() => setIsBuffering(true)}
            onPlaying={() => {
              setIsBuffering(false)
              setIsPaused(false)
            }}
            onPause={() => setIsPaused(true)}
            onCanPlay={() => setIsBuffering(false)}
            onEnded={() => {
              observation.current?.dispose()
              player.current?.stopLoad()
              setActive(false)
              setIsBuffering(false)
              setIsPaused(false)
              setNotice('Reproducción finalizada')
            }}
          />

          {/* Buffering Indicator */}
          {active && isBuffering && (
            <div className='pointer-events-none absolute inset-0 z-20 flex flex-col items-center justify-center bg-black/55 animate-in fade-in duration-150'>
              <div className='flex size-10 items-center justify-center rounded-full bg-white/10 backdrop-blur-md'>
                <Loader2 className='size-5 animate-spin text-white' />
              </div>
              <span className='mt-2 text-[11px] font-medium text-white/85'>
                Preparando video…
              </span>
            </div>
          )}

          {/* Paused Overlay */}
          {active && isPaused && !isBuffering && (
            <div className='absolute inset-0 z-20 flex flex-col items-center justify-center bg-black/40 backdrop-blur-xs animate-in fade-in duration-150 pointer-events-none'>
              <div className='flex size-13 items-center justify-center rounded-full bg-white/20 ring-1 ring-white/30 backdrop-blur-md shadow-xl'>
                <Play className='size-6 fill-white text-white pl-0.5' />
              </div>
              <span className='mt-2 text-[10.5px] font-medium text-white/80'>
                Pausado · Toca para continuar
              </span>
            </div>
          )}

          {/* Idle / Poster Overlay (Only shown in full mode when not active) */}
          {!isPip && !active && (
            <div className='absolute inset-0 flex flex-col items-center justify-center bg-gradient-to-t from-black via-black/35 to-black/10 p-4 text-center'>
              {exhausted ? (
                <div className='flex flex-col items-center gap-1.5 animate-in fade-in zoom-in-95 duration-200'>
                  <div className='flex size-10 items-center justify-center rounded-full bg-rose-500/15 text-rose-400'>
                    <AlertCircle className='size-5' />
                  </div>
                  <p className='text-[12px] font-semibold text-white'>Sin datos disponibles</p>
                  {onOpenBalance && (
                    <button
                      type='button'
                      onClick={onOpenBalance}
                      className='mt-1 flex items-center gap-1.5 rounded-full bg-white px-4 py-1.5 text-[11.5px] font-semibold text-black transition active:scale-95 cursor-pointer'
                    >
                      <CreditCard className='size-3.5' />
                      Recargar en Mi 5G
                    </button>
                  )}
                </div>
              ) : !connected ? (
                <div className='flex flex-col items-center gap-2 animate-in fade-in duration-200'>
                  <div className='flex size-10 items-center justify-center rounded-full bg-white/10'>
                    <Loader2 className='size-5 animate-spin text-white' />
                  </div>
                  <p className='text-[11px] font-medium text-white/75'>Conectando a 5G…</p>
                </div>
              ) : (
                <>
                  <button
                    onClick={handleStart}
                    disabled={isAfBoostPending}
                    className='group flex size-13 items-center justify-center rounded-full bg-white text-black shadow-xl transition-all hover:scale-105 active:scale-95 disabled:opacity-40 cursor-pointer'
                    aria-label='Reproducir video'
                  >
                    {isAfBoostPending ? (
                      <Loader2 className='size-6 animate-spin' />
                    ) : (
                      <Play className='size-5 fill-black pl-0.5 transition-transform group-hover:scale-110' />
                    )}
                  </button>
                  <p className='mt-2 text-[11px] text-white/65'>
                    Reproducir por 5G
                  </p>
                </>
              )}
            </div>
          )}

          {/* Active Player Floating Controls (Only shown in full mode) */}
          {!isPip && active && (
            <div className='absolute right-2 bottom-2 z-20 flex items-center gap-1.5'>
              <button
                type='button'
                onClick={(e) => {
                  e.stopPropagation()
                  if (video.current) {
                    if (video.current.paused) void video.current.play()
                    else video.current.pause()
                  }
                }}
                className='flex size-7 items-center justify-center rounded-full bg-black/60 text-white backdrop-blur-md border border-white/10 transition hover:bg-black/80 cursor-pointer'
                aria-label={isPaused ? 'Reproducir' : 'Pausar'}
              >
                {isPaused ? <Play className='size-3.5 fill-white' /> : <Pause className='size-3.5 fill-white' />}
              </button>
              <button
                type='button'
                onClick={(e) => {
                  e.stopPropagation()
                  toggleMute()
                }}
                className='flex size-7 items-center justify-center rounded-full bg-black/60 text-white backdrop-blur-md border border-white/10 transition hover:bg-black/80 cursor-pointer'
                aria-label={isMuted ? 'Activar sonido' : 'Silenciar'}
              >
                {isMuted ? <VolumeX className='size-3.5' /> : <Volume2 className='size-3.5' />}
              </button>
              <button
                type='button'
                onClick={(e) => {
                  e.stopPropagation()
                  stop()
                }}
                className='flex size-7 items-center justify-center rounded-full bg-black/60 text-rose-400 backdrop-blur-md border border-rose-500/20 transition hover:bg-black/80 cursor-pointer'
                aria-label='Detener video'
              >
                <Square className='size-3 fill-rose-400' />
              </button>
            </div>
          )}
        </div>

        {/* Live Status Toast Banner (Only in full mode) */}
        {!isPip && notice && (
          <div className='flex items-center border-t border-white/10 bg-[#1c1c1e] px-3 py-1.5 text-[10.5px]'>
            <span className='flex items-center gap-1.5'>
              <span
                className={cn(
                  'size-1.5 rounded-full',
                  active ? 'bg-[#30d158]' : 'bg-amber-400'
                )}
              />
              <span className='truncate text-white/75'>{notice}</span>
            </span>
          </div>
        )}
      </div>

      {/* Compact iOS controls rendered only when NOT in PiP */}
      {!isPip && (
        <div className='space-y-2'>
          <div className='grid grid-cols-3 gap-1 rounded-xl bg-[#1c1c1e] p-1'>
            {(
              [
                ['720p', 'HD', '720p'],
                ['1080p', 'FHD', '1080p'],
                ['4k', '5G+', '5QI 2'],
              ] as const
            ).map(([value, label, caption]) => (
              <button
                key={value}
                type='button'
                aria-label={`Calidad ${label} ${caption}`}
                disabled={isAfBoostPending}
                onClick={() => void handleSelectQuality(value)}
                className={cn(
                  'relative flex min-h-11 flex-col items-center justify-center rounded-lg transition active:scale-95 disabled:opacity-40',
                  qualityMode === value
                    ? 'bg-[#3a3a3c] text-white shadow-sm'
                    : 'text-[#8e8e93] hover:text-white',
                  value === '4k' && qualityMode === value && 'text-amber-300'
                )}
              >
                <span className='flex items-center gap-1 text-[12px] font-semibold'>
                  {value === '4k' && <Zap className='size-3 fill-current' />}
                  {label}
                </span>
                <span className='text-[9px] font-medium opacity-65'>{caption}</span>
                {value === '4k' && isAfBoostPending && (
                  <span className='absolute inset-0 flex items-center justify-center rounded-lg bg-[#2c2c2e]/90'>
                    <Loader2 className='size-3.5 animate-spin' />
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className='flex items-center justify-between rounded-xl bg-[#1c1c1e] px-3 py-2.5'>
            <span className='flex min-w-0 items-center gap-2 text-[11.5px] font-medium text-white'>
              <span
                className={cn(
                  'size-2 shrink-0 rounded-full',
                  afBoost?.active ? 'bg-amber-400' : 'bg-[#30d158]'
                )}
              />
              {afBoost?.active ? 'Prioridad 5G+' : 'Internet · 5QI 9'}
            </span>
            <span className='font-mono text-[10px] text-[#8e8e93]'>
              {active ? `${(received / 1_000_000).toFixed(1)} MB` : 'Listo'}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
