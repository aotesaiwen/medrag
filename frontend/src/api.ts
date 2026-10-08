import type { AskResponse, Turn } from './model.ts'
import { requestHistory } from './model.ts'

export class APIError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

async function authenticated(path: string, key: string, options: RequestInit = {}): Promise<Response> {
  let response: Response
  try {
    response = await fetch(path, { ...options, cache: 'no-store',
      headers: { ...options.headers, Authorization: `Bearer ${key}` } })
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    throw new APIError('Cannot reach the service. Check the connection and retry.', 0)
  }
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: 'The API key is missing or invalid. Connect with the correct key and retry.',
      404: 'The requested source is no longer available.',
      422: 'The question or conversation is too long. Shorten it and retry.',
      502: 'The answer service could not complete the request. Please retry.',
      503: 'The service is not configured or ready. Please check its status.',
    }
    throw new APIError(messages[response.status] ?? 'The request failed. Please retry.', response.status)
  }
  return response
}

export async function connect(key: string): Promise<void> {
  await authenticated('/auth', key, { signal: AbortSignal.timeout(15000) })
}

export async function ask(turn: Turn, previous: Turn[], key: string, signal: AbortSignal): Promise<AskResponse> {
  const timeout = AbortSignal.timeout(600000)
  try {
    const response = await authenticated('/ask', key, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: AbortSignal.any([signal, timeout]),
      body: JSON.stringify({ question: turn.requestQuestion, history: requestHistory(previous, turn.id), rag_enabled: turn.ragEnabled }),
    })
    return await response.json() as AskResponse
  } catch (error) {
    if (timeout.aborted && !signal.aborted) throw new APIError('The answer took too long. Please retry.', 0)
    throw error
  }
}

export async function fetchSlidePage(file: string, page: number, key: string, signal: AbortSignal): Promise<Blob> {
  let response: Response
  try {
    response = await authenticated(`/slides/${encodeURIComponent(file)}/pages/${page}`, key, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
    })
  } catch (error) {
    if (error instanceof APIError && error.status === 422) {
      throw new APIError('This slide page could not be rendered. Please retry or contact the service owner.', 422)
    }
    throw error
  }
  if (response.headers.get('content-type')?.split(';')[0] !== 'image/png') {
    throw new APIError('The service returned an invalid slide image. Please retry.', 502)
  }
  return response.blob()
}

export async function fetchSpeech(text: string, key: string, signal: AbortSignal): Promise<Blob> {
  const timeout = AbortSignal.timeout(605000)
  try {
    const response = await authenticated('/speech', key, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }), signal: AbortSignal.any([signal, timeout]),
    })
    if (response.headers.get('content-type')?.split(';')[0] !== 'audio/wav') {
      throw new APIError('The service returned invalid audio. Please retry.', 502)
    }
    const audio = await response.blob()
    if (audio.size <= 44) throw new APIError('The service returned empty audio. Please retry.', 502)
    return audio
  } catch (error) {
    if (signal.aborted) throw error
    if (timeout.aborted || error instanceof APIError && error.status === 504) {
      throw new APIError('Speech took too long. Select a shorter passage and listen again.', 504)
    }
    if (error instanceof APIError) {
      const messages: Record<number, string> = {
        422: 'This passage is too long or has no spoken text. Select a shorter passage.',
        429: 'Speech synthesis is busy. Please retry in a moment.',
        502: 'Speech synthesis failed. Please retry.',
        503: 'The speech service is not ready. Please check its status.',
      }
      if (messages[error.status]) throw new APIError(messages[error.status], error.status)
    }
    throw error
  }
}
