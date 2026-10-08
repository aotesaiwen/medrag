import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchSpeech } from './api'

type Status = 'loading' | 'playing' | 'paused' | 'ready' | 'error'
export type SpeechState = { turnId: string; status: Status; error?: string; slow?: boolean }
type CachedAudio = { url: string; bytes: number }

export function useSpeech(conversationId: string | null, apiKey: string) {
  const [state, setState] = useState<SpeechState | null>(null)
  const player = useRef<HTMLAudioElement | null>(null)
  const request = useRef<AbortController | null>(null)
  const cache = useRef(new Map<string, CachedAudio>())
  const generation = useRef(0)

  useEffect(() => {
    if (state?.status !== 'loading') return
    const timer = window.setTimeout(() => setState(previous => previous?.status === 'loading'
      ? { ...previous, slow: true } : previous), 10000)
    return () => clearTimeout(timer)
  }, [state?.status, state?.turnId])

  const stop = useCallback(() => {
    generation.current += 1
    request.current?.abort(); request.current = null
    const audio = player.current
    if (audio) { audio.pause(); audio.removeAttribute('src'); audio.load() }
    player.current = null
    setState(null)
  }, [])

  useEffect(() => {
    stop()
    return () => {
      stop()
      for (const entry of cache.current.values()) URL.revokeObjectURL(entry.url)
      cache.current.clear()
    }
  }, [conversationId, apiKey, stop])

  async function listen(turnId: string, text: string) {
    stop()
    if (!text.trim()) { setState({ turnId, status: 'error', error: 'There is no text to read.' }); return }
    if (!apiKey) { setState({ turnId, status: 'error', error: 'Connect with your API key to listen.' }); return }
    const current = generation.current
    const controller = new AbortController()
    request.current = controller
    const cacheKey = JSON.stringify([turnId, text])
    try {
      let entry = cache.current.get(cacheKey)
      if (!entry) {
        setState({ turnId, status: 'loading' })
        const blob = await fetchSpeech(text, apiKey, controller.signal)
        if (generation.current !== current) return
        // Bound in-memory audio; nothing is put in browser storage or on disk.
        let bytes = [...cache.current.values()].reduce((sum, item) => sum + item.bytes, 0)
        while (cache.current.size && (cache.current.size >= 8 || bytes + blob.size > 64 * 1024 * 1024)) {
          const oldest = cache.current.keys().next().value!
          const removed = cache.current.get(oldest)!
          bytes -= removed.bytes; URL.revokeObjectURL(removed.url); cache.current.delete(oldest)
        }
        entry = { url: URL.createObjectURL(blob), bytes: blob.size }
        cache.current.set(cacheKey, entry)
      }
      if (generation.current !== current) return
      request.current = null
      const audio = new Audio(entry.url)
      player.current = audio
      audio.onended = () => { if (generation.current === current) setState({ turnId, status: 'ready' }) }
      audio.onerror = () => {
        if (generation.current === current) {
          cache.current.delete(cacheKey); URL.revokeObjectURL(entry!.url)
          setState({ turnId, status: 'error', error: 'Audio could not be played. Please retry.' })
        }
      }
      try { await audio.play() }
      catch {
        if (generation.current === current) setState(audio.error
          ? { turnId, status: 'error', error: 'Audio could not be played. Please retry.' }
          : { turnId, status: 'ready' })
        return
      }
      if (generation.current === current) setState({ turnId, status: 'playing' })
    } catch (error) {
      if (generation.current !== current || controller.signal.aborted) return
      request.current = null
      setState({ turnId, status: 'error', error: error instanceof Error ? error.message : 'Speech synthesis failed. Please retry.' })
    }
  }

  function pause() {
    player.current?.pause()
    setState(previous => previous ? { ...previous, status: 'paused' } : null)
  }

  async function resume() {
    const audio = player.current
    const current = generation.current
    if (!audio) return
    try {
      await audio.play()
      if (generation.current === current) setState(previous => previous ? { ...previous, status: 'playing' } : null)
    } catch {
      if (generation.current === current) setState(previous => previous ? { ...previous, status: 'error', error: 'Playback was blocked. Please try listening again.' } : null)
    }
  }

  return { state, listen, pause, resume, stop }
}
export type SpeechController = ReturnType<typeof useSpeech>
