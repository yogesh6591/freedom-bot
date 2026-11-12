'use client'

/**
 * Chat mic: record → POST /api/chat/transcribe → append text.
 * While listening, the text field becomes a live voice-reactive waveform
 * (WhatsApp / ChatGPT-style), driven by the real microphone level.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Mic, Square, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'

type MicState = 'idle' | 'recording' | 'transcribing'

const BAR_COUNT = 32

function pickMime(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined
  for (const type of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']) {
    if (MediaRecorder.isTypeSupported(type)) return type
  }
  return undefined
}

function extensionFor(mime: string | undefined): string {
  if (!mime) return 'webm'
  if (mime.includes('mp4')) return 'm4a'
  if (mime.includes('ogg')) return 'ogg'
  return 'webm'
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

/** Whisper often inserts newlines between sentences — keep one continuous chat line. */
function flattenSpoken(spoken: string): string {
  return spoken.replace(/\s+/g, ' ').trim()
}

/** Join spoken words onto an existing draft without wiping it. */
export function appendSpoken(existing: string, spoken: string): string {
  const add = flattenSpoken(spoken)
  if (!add) return existing
  const base = existing.replace(/\s+$/, '')
  if (!base) return add
  const needsSpace = !/\s$/.test(base) && !/^[.,!?;:]/.test(add)
  return `${base}${needsSpace ? ' ' : ''}${add}`
}

function VoiceWaveform({ levels, active }: { levels: number[]; active: boolean }) {
  return (
    <div
      className="voice-wave flex h-9 min-w-0 flex-1 items-center justify-center gap-[3px]"
      aria-hidden
    >
      {levels.map((level, i) => {
        const h = Math.max(3, Math.round(3 + level * 30))
        return (
          <span
            key={i}
            className={`voice-wave-bar inline-block w-[3px] rounded-full ${
              active ? 'bg-rose-500' : 'bg-muted/50'
            }`}
            style={{
              height: `${h}px`,
              opacity: active ? 0.45 + level * 0.55 : 0.35,
              transition: 'height 75ms ease-out, opacity 75ms ease-out'
            }}
          />
        )
      })}
    </div>
  )
}

/**
 * Replaces the textarea + mic while recording: live waveform in the text bar,
 * discard / stop controls. Idle: normal textarea + mic button.
 */
export function VoiceComposerField({
  disabled,
  value,
  onChange,
  inputRef,
  onTranscript,
  onError,
  onKeyDown,
  onListeningChange
}: {
  disabled?: boolean
  value: string
  onChange: (value: string) => void
  inputRef: React.RefObject<HTMLTextAreaElement | null>
  onTranscript: (text: string) => void
  onError?: (message: string) => void
  onKeyDown?: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void
  onListeningChange?: (listening: boolean) => void
}) {
  const [state, setState] = useState<MicState>('idle')
  const [levels, setLevels] = useState<number[]>(() => Array.from({ length: BAR_COUNT }, () => 0.12))
  const [elapsedMs, setElapsedMs] = useState(0)

  const mediaRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const cancelledRef = useRef(false)
  const discardRef = useRef(false)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const rafRef = useRef<number>(0)
  const startedAtRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopMeter = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    rafRef.current = 0
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = null
    try {
      void audioCtxRef.current?.close()
    } catch {
      /* ignore */
    }
    audioCtxRef.current = null
    analyserRef.current = null
    setLevels(Array.from({ length: BAR_COUNT }, () => 0.12))
  }, [])

  const stopTracks = useCallback(() => {
    mediaRef.current?.getTracks().forEach((t) => t.stop())
    mediaRef.current = null
    recorderRef.current = null
  }, [])

  const startMeter = useCallback((stream: MediaStream) => {
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    const ctx = new AudioCtx()
    const source = ctx.createMediaStreamSource(stream)
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 128
    analyser.smoothingTimeConstant = 0.65
    source.connect(analyser)
    audioCtxRef.current = ctx
    analyserRef.current = analyser
    const data = new Uint8Array(analyser.frequencyBinCount)

    const tick = () => {
      const node = analyserRef.current
      if (!node) return
      node.getByteFrequencyData(data)
      // Focus on voice band; mirror for a symmetric WhatsApp-style wave.
      const usable = Math.max(8, Math.floor(data.length * 0.55))
      const half = Math.ceil(BAR_COUNT / 2)
      const left: number[] = []
      for (let i = 0; i < half; i++) {
        const idx = Math.min(usable - 1, Math.floor((i / half) * usable))
        const raw = (data[idx] ?? 0) / 255
        // Lift quiet speech so the bar still moves when someone talks softly.
        const boosted = Math.min(1, Math.pow(raw, 0.7) * 1.35)
        left.push(boosted)
      }
      const right = [...left].reverse()
      const next =
        BAR_COUNT % 2 === 0 ? [...left, ...right] : [...left, left[left.length - 1] ?? 0.1, ...right]
      setLevels(next.slice(0, BAR_COUNT))
      rafRef.current = requestAnimationFrame(tick)
    }
    void ctx.resume().then(() => {
      rafRef.current = requestAnimationFrame(tick)
    })
  }, [])

  useEffect(
    () => () => {
      cancelledRef.current = true
      try {
        recorderRef.current?.state === 'recording' && recorderRef.current.stop()
      } catch {
        /* ignore */
      }
      stopMeter()
      stopTracks()
    },
    [stopMeter, stopTracks]
  )

  async function finish(blob: Blob, mime: string | undefined) {
    setState('transcribing')
    setElapsedMs(0)
    const form = new FormData()
    form.append('file', blob, `recording.${extensionFor(mime)}`)
    try {
      const result = await api.upload<{ text: string }>('/api/chat/transcribe', form)
      if (cancelledRef.current) return
      const text = (result.text || '').trim()
      if (text) onTranscript(text)
      else onError?.('No speech was detected. Try again.')
    } catch (err) {
      if (cancelledRef.current) return
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : 'Could not transcribe the recording.'
      onError?.(message)
    } finally {
      if (!cancelledRef.current) setState('idle')
    }
  }

  async function start() {
    if (disabled || state !== 'idle') return
    if (typeof window === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      onError?.('This browser cannot use the microphone.')
      return
    }
    discardRef.current = false
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      })
      mediaRef.current = stream
      const mime = pickMime()
      const recorder = mime
        ? new MediaRecorder(stream, { mimeType: mime })
        : new MediaRecorder(stream)
      chunksRef.current = []
      recorderRef.current = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onerror = () => {
        stopMeter()
        stopTracks()
        setState('idle')
        setElapsedMs(0)
        onError?.('Recording failed. Try again.')
      }
      recorder.onstop = () => {
        const type = recorder.mimeType || mime || 'audio/webm'
        const blob = new Blob(chunksRef.current, { type })
        stopMeter()
        stopTracks()
        if (discardRef.current) {
          discardRef.current = false
          setState('idle')
          setElapsedMs(0)
          return
        }
        if (blob.size < 32) {
          setState('idle')
          setElapsedMs(0)
          onError?.('Recording was too short. Hold a bit longer, then stop.')
          return
        }
        void finish(blob, type)
      }
      recorder.start()
      startedAtRef.current = Date.now()
      setElapsedMs(0)
      timerRef.current = setInterval(() => {
        setElapsedMs(Date.now() - startedAtRef.current)
      }, 200)
      startMeter(stream)
      setState('recording')
    } catch {
      stopMeter()
      stopTracks()
      setState('idle')
      onError?.('Microphone permission is needed to speak a message.')
    }
  }

  function stop() {
    const recorder = recorderRef.current
    if (!recorder || recorder.state === 'inactive') {
      stopMeter()
      stopTracks()
      setState('idle')
      setElapsedMs(0)
      return
    }
    try {
      recorder.stop()
    } catch {
      stopMeter()
      stopTracks()
      setState('idle')
      setElapsedMs(0)
    }
  }

  function discard() {
    discardRef.current = true
    stop()
  }

  const recording = state === 'recording'
  const transcribing = state === 'transcribing'
  const listening = recording || transcribing

  useEffect(() => {
    onListeningChange?.(listening)
  }, [listening, onListeningChange])

  if (listening) {
    return (
      <>
        <div
          className={`voice-listening flex min-h-[44px] min-w-0 flex-1 items-center gap-2 rounded-2xl px-2 py-1 ${
            recording ? 'voice-listening-live' : ''
          }`}
          role="status"
          aria-live="polite"
          aria-label={recording ? 'Listening' : 'Transcribing'}
        >
          {recording ? (
            <button
              type="button"
              aria-label="Cancel recording"
              title="Cancel"
              onClick={discard}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted transition-colors hover:bg-rose-500/10 hover:text-rose-500"
            >
              <Trash2 size={16} aria-hidden />
            </button>
          ) : (
            <span className="flex h-9 w-9 shrink-0 items-center justify-center text-muted">
              <Loader2 size={16} className="animate-spin" aria-hidden />
            </span>
          )}

          <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
            <div className="flex items-center gap-2">
              {recording ? (
                <span className="voice-pulse-dot h-2 w-2 shrink-0 rounded-full bg-rose-500" aria-hidden />
              ) : null}
              <span className="text-[12px] font-medium tracking-wide text-rose-500">
                {recording ? 'Listening…' : 'Transcribing…'}
              </span>
              {recording ? (
                <span className="tabular-nums text-[12px] text-muted">{formatElapsed(elapsedMs)}</span>
              ) : null}
            </div>
            <VoiceWaveform levels={levels} active={recording} />
          </div>

          {recording ? (
            <button
              type="button"
              aria-label="Stop recording"
              title="Stop recording"
              onClick={stop}
              className="voice-stop mb-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-rose-500 text-white shadow-[0_8px_18px_-8px_rgb(244_63_94_/_0.9)] transition-transform hover:scale-[1.03] active:scale-95"
            >
              <Square size={14} fill="currentColor" strokeWidth={0} aria-hidden />
            </button>
          ) : (
            <span className="mb-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-muted/20 text-muted">
              <Loader2 size={16} className="animate-spin" aria-hidden />
            </span>
          )}
        </div>
      </>
    )
  }

  return (
    <>
      <label htmlFor="chat-input" className="sr-only">
        Message
      </label>
      <textarea
        id="chat-input"
        ref={inputRef}
        rows={1}
        value={value}
        onChange={(event) => {
          onChange(event.target.value)
          const el = event.target
          el.style.height = 'auto'
          el.style.height = `${Math.min(el.scrollHeight, 200)}px`
        }}
        onKeyDown={onKeyDown}
        placeholder="Message the assistant"
        disabled={disabled}
        className="max-h-[200px] min-h-[40px] w-full resize-none border-0 bg-transparent py-2 text-[15px] leading-6 text-primary outline-none placeholder:text-muted/70 disabled:opacity-60"
      />
      <button
        type="button"
        disabled={disabled}
        aria-label="Speak a message"
        title="Speak a message"
        onClick={() => void start()}
        className="mb-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted transition-colors hover:bg-background-elevated hover:text-primary disabled:cursor-not-allowed disabled:opacity-45"
      >
        <Mic size={20} aria-hidden />
      </button>
    </>
  )
}

/** @deprecated Prefer VoiceComposerField — kept for any external import of the mic-only control. */
export function VoiceMic({
  disabled,
  onTranscript,
  onError
}: {
  disabled?: boolean
  onTranscript: (text: string) => void
  onError?: (message: string) => void
}) {
  const inputRef = useRef<HTMLTextAreaElement>(null)
  return (
    <VoiceComposerField
      disabled={disabled}
      value=""
      onChange={() => undefined}
      inputRef={inputRef}
      onTranscript={onTranscript}
      onError={onError}
    />
  )
}
