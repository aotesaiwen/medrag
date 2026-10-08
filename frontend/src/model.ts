export type Source = {
  id: string; number: number; label: string; unit: string; paragraphs: string[]
  url?: string; file?: string; page?: number
}
export type Quotation = { text: string; source: Source; turnId: string }
export type Turn = {
  id: string; question: string; requestQuestion: string; quotation?: Quotation
  status: 'pending' | 'complete' | 'error'; answer: string; rewrittenQuestion: string
  sources: Source[]; error?: string; ragEnabled: boolean
}
export type Conversation = { id: string; createdAt: number; turns: Turn[] }
export type Session = {
  version: 2; conversations: Conversation[]; activeId: string | null
  theme: 'light' | 'dark'; reducedMotion: boolean; ragEnabled: boolean
}
export type AskResponse = {
  question: string; rewritten_question: string; answer: string
  rag_enabled?: boolean
  citations: { number: number; chunk_id: string; label: string }[]
  sources: { id: string; text: string; heading_path: string[]; metadata: Record<string, unknown> }[]
}

// A separate key prevents old prototype answers being mistaken for live results.
export const STORAGE_KEY = 'course-rag.v2'
export const PHRASE = 'Explore GDPR, HIPAA and your lecture materials, with source passages alongside each answer.'
export const EXAMPLE_QUESTIONS = [
  'GDPR 第17条规定，哪些情况下可以请求删除个人数据？',
  'HIPAA 允许患者要求更正健康信息吗？',
  '课程如何解释重新识别与隐私风险？',
]

export function makeTurn(question: string, quotation?: Quotation, ragEnabled = true): Turn {
  const requestQuestion = quotation
    ? `${question}\n\n请结合以下选中文本回答（作为问题背景，仅引用本轮实际检索到的资料）：\n来源：${quotation.source.id}\n选中文本：${quotation.text}`
    : question
  if (requestQuestion.length > 20000) throw new Error('The question and selected passage are too long. Select a shorter passage.')
  return { id: crypto.randomUUID(), question, requestQuestion, quotation, ragEnabled,
    status: 'pending', answer: '', rewrittenQuestion: '', sources: [] }
}

export function stripCitations(text: string): string {
  return text.replace(/\[[A-Za-z][A-Za-z0-9_-]*:[^\[\]\n]+\]/g, '')
    .replace(/\[\d+(?:\s*[,，–-]\s*\d+)*\]/g, '').trim()
}

export function requestHistory(turns: Turn[], beforeId?: string) {
  const end = beforeId ? turns.findIndex(turn => turn.id === beforeId) : turns.length
  return turns.slice(0, end < 0 ? turns.length : end)
    .filter(turn => turn.status === 'complete').slice(-5)
    .map(turn => ({ question: stripCitations(turn.requestQuestion), answer: stripCitations(turn.answer) }))
}

function safeUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  try { const url = new URL(value); return url.protocol === 'https:' ? url.href : undefined }
  catch { return undefined }
}

export function completeTurn(turn: Turn, response: AskResponse): Turn {
  if (!response || typeof response.answer !== 'string' || !response.answer.trim()
    || typeof response.rewritten_question !== 'string'
    || !Array.isArray(response.sources) || !Array.isArray(response.citations)) {
    throw new Error('The service returned an invalid answer. Please retry.')
  }
  const ids = new Set<string>()
  const sources = [...response.citations].sort((a, b) => a.number - b.number).map((citation, index) => {
    const hit = response.sources.find(source => source.id === citation.chunk_id)
    if (!hit || typeof hit.text !== 'string' || !Array.isArray(hit.heading_path)
      || !hit.heading_path.every(part => typeof part === 'string') || !hit.metadata
      || citation.number !== index + 1 || ids.has(citation.chunk_id) || typeof citation.label !== 'string') {
      throw new Error('The service returned inconsistent citations. Please retry.')
    }
    ids.add(citation.chunk_id)
    const metadata = hit.metadata
    const file = typeof metadata.source_file === 'string' && /^[^/\\]+\.pdf$/i.test(metadata.source_file)
      ? metadata.source_file : undefined
    return {
      id: hit.id, number: citation.number, label: String(metadata.corpus ?? 'Source').toUpperCase(),
      unit: citation.label, paragraphs: hit.text.split(/\n\s*\n/), url: safeUrl(metadata.source_url), file,
      page: Number.isInteger(metadata.slide) && Number(metadata.slide) > 0 ? Number(metadata.slide) : undefined,
    }
  })
  return { ...turn, status: 'complete', answer: response.answer,
    rewrittenQuestion: response.rewritten_question, sources, error: undefined }
}

function validSource(value: unknown): value is Source {
  if (!value || typeof value !== 'object') return false
  const source = value as Source
  return typeof source.id === 'string' && typeof source.label === 'string' && typeof source.unit === 'string'
    && Number.isInteger(source.number) && source.number > 0 && Array.isArray(source.paragraphs)
    && source.paragraphs.every(text => typeof text === 'string')
    && (source.url === undefined || safeUrl(source.url) === source.url)
    && (source.file === undefined || (typeof source.file === 'string' && /^[^/\\]+\.pdf$/i.test(source.file)))
}

function validTurn(value: unknown): value is Turn {
  if (!value || typeof value !== 'object') return false
  const turn = value as Turn
  return typeof turn.id === 'string' && typeof turn.question === 'string' && typeof turn.requestQuestion === 'string'
    && typeof turn.answer === 'string' && typeof turn.rewrittenQuestion === 'string'
    && ['pending', 'complete', 'error'].includes(turn.status) && Array.isArray(turn.sources)
    && (turn.ragEnabled === undefined || typeof turn.ragEnabled === 'boolean')
    && turn.sources.every(validSource) && (turn.error === undefined || typeof turn.error === 'string')
    && (!turn.quotation || (typeof turn.quotation.text === 'string' && typeof turn.quotation.turnId === 'string'
      && validSource(turn.quotation.source)))
}

export function readSession(
  storage?: Pick<Storage, 'getItem'>,
  reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches,
): Session {
  const fallback: Session = { version: 2, conversations: [], activeId: null, theme: 'light', reducedMotion, ragEnabled: true }
  try {
    const raw = (storage ?? sessionStorage).getItem(STORAGE_KEY)
    if (!raw) return fallback
    const saved = JSON.parse(raw) as Session
    if (!saved || saved.version !== 2 || !Array.isArray(saved.conversations)
      || !['light', 'dark'].includes(saved.theme) || typeof saved.reducedMotion !== 'boolean'
      || (saved.ragEnabled !== undefined && typeof saved.ragEnabled !== 'boolean')
      || !saved.conversations.every(c => c && typeof c.id === 'string' && typeof c.createdAt === 'number'
        && Array.isArray(c.turns) && c.turns.every(validTurn))) return fallback
    return {
      version: 2, theme: saved.theme, reducedMotion: saved.reducedMotion, ragEnabled: saved.ragEnabled ?? true,
      activeId: saved.conversations.some(c => c.id === saved.activeId) ? saved.activeId : null,
      conversations: saved.conversations.map(c => ({ id: c.id, createdAt: c.createdAt, turns: c.turns.map(t => ({
        id: t.id, question: t.question, requestQuestion: t.requestQuestion, quotation: t.quotation,
        ragEnabled: t.ragEnabled ?? true,
        status: t.status === 'pending' ? 'error' : t.status, answer: t.answer,
        rewrittenQuestion: t.rewrittenQuestion, sources: t.sources,
        error: t.status === 'pending' ? 'The page was reloaded before the answer finished. Please retry.' : t.error,
      })) })),
    }
  } catch { return fallback }
}
