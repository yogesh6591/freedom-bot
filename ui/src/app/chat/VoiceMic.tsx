'use client'

/**
 * Chat mic: record → POST /api/chat/transcribe → append text.
 * While recording, the text field becomes a fixed-height, theme-aware waveform
 * bar (ChatGPT-style): pulse dot, live waves, timer, stop, grey cancel.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Mic, Square, X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'

type MicState = 'idle' | 'recording' | 'transcribing'

const BAR_COUNT = 40
const WAVE_MAX_PX = 26

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

/** Soft UI tones for start / stop / cancel (pleasant, short). */
function playPleaseTone(kind: 'start' | 'stop' | 'cancel') {
  if (typeof window === 'undefined') return
  try {
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    const ctx = new AudioCtx()
    const now = ctx.currentTime
    const gain = ctx.createGain()
    gain.connect(ctx.destination)
    gain.gain.setValueAtTime(0.0001, now)

    const beep = (freq: number, t0: number, dur: number, peak = 0.045) => {
      const osc = ctx.createOscillator()
      osc.type = 'sine'
      osc.frequency.setValueAtTime(freq, t0)
      osc.connect(gain)
      gain.gain.cancelScheduledValues(t0)
      gain.gain.setValueAtTime(0.0001, t0)
      gain.gain.exponentialRampToValueAtTime(peak, t0 + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur)
      osc.start(t0)
      osc.stop(t0 + dur + 0.02)
    }

    if (kind === 'start') {
      beep(660, now, 0.09, 0.04)
      beep(880, now + 0.08, 0.11, 0.035)
    } else if (kind === 'stop') {
      beep(720, now, 0.08, 0.04)
      beep(540, now + 0.07, 0.12, 0.03)
    } else {
      beep(420, now, 0.14, 0.035)
    }

    window.setTimeout(() => {
      void ctx.close()
    }, 450)
  } catch {
    /* ignore — tone is optional UX */
  }
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
    <div className="voice-wave" aria-hidden>
      {levels.map((level, i) => {
        const h = Math.max(3, Math.round(3 + level * (WAVE_MAX_PX - 3)))
        return (
          <span
            key={i}
            className={`voice-wave-bar ${active ? 'voice-wave-bar-live' : 'voice-wave-bar-idle'}`}
            style={{
              height: `${h}px`,
              opacity: active ? 0.4 + level * 0.6 : 0.3
            }}
          />
        )
      })}
    </div>
  )
}

/**
 * Replaces the textarea + mic while recording: fixed-height waveform bar.
 * Idle: normal textarea + mic button.
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
  const [levels, setLevels] = useState<number[]>(() => Array.from({ length: BAR_COUNT }, () => 0.14))
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
    setLevels(Array.from({ length: BAR_COUNT }, () => 0.14))
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
    analyser.fftSize = 256
    analyser.smoothingTimeConstant = 0.72
    source.connect(analyser)
    audioCtxRef.current = ctx
    analyserRef.current = analyser
    const data = new Uint8Array(analyser.frequencyBinCount)

    const tick = () => {
      const node = analyserRef.current
      if (!node) return
      node.getByteFrequencyData(data)
      const usable = Math.max(12, Math.floor(data.length * 0.5))
      const next: number[] = []
      for (let i = 0; i < BAR_COUNT; i++) {
        // Sample across the voice band, then boost both outer ends.
        const idx = Math.min(usable - 1, Math.floor((i / (BAR_COUNT - 1)) * (usable - 1)))
        const raw = (data[idx] ?? 0) / 255
        const boosted = Math.min(1, Math.pow(raw, 0.65) * 1.4)
        const t = i / (BAR_COUNT - 1)
        const edge = Math.pow(Math.abs(t - 0.5) * 2, 1.15) // 0 center → 1 edges
        const envelope = 0.55 + 0.45 * edge
        next.push(Math.min(1, boosted * envelope + 0.06))
      }
      setLevels(next)
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
    playPleaseTone('start')
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
    playPleaseTone('stop')
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
    playPleaseTone('cancel')
    discardRef.current = true
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

  const recording = state === 'recording'
  const transcribing = state === 'transcribing'
  const listening = recording || transcribing

  useEffect(() => {
    onListeningChange?.(listening)
  }, [listening, onListeningChange])

  if (listening) {
    return (
      <div
        className={`voice-listening ${recording ? 'voice-listening-live' : 'voice-listening-busy'}`}
        role="status"
        aria-live="polite"
        aria-label={recording ? 'Recording' : 'Transcribing'}
      >
        {recording ? (
          <span className="voice-pulse-dot" aria-hidden />
        ) : (
          <Loader2 size={14} className="shrink-0 animate-spin text-muted" aria-hidden />
        )}

        <VoiceWaveform levels={levels} active={recording} />

        {recording ? (
          <span className="voice-timer tabular-nums">{formatElapsed(elapsedMs)}</span>
        ) : (
          <span className="voice-timer text-muted">…</span>
        )}

        {recording ? (
          <>
            <button
              type="button"
              aria-label="Stop recording"
              title="Stop"
              onClick={stop}
              className="voice-stop"
            >
              <Square size={13} fill="currentColor" strokeWidth={0} aria-hidden />
            </button>
            <button
              type="button"
              aria-label="Cancel recording"
              title="Cancel"
              onClick={discard}
              className="voice-cancel"
            >
              <X size={18} strokeWidth={2} aria-hidden />
            </button>
          </>
        ) : (
          <span className="voice-stop voice-stop-busy" aria-hidden>
            <Loader2 size={16} className="animate-spin" />
          </span>
        )}
      </div>
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
