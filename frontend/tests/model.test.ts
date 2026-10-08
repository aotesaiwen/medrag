import test from 'node:test'
import assert from 'node:assert/strict'
import { completeTurn, makeTurn, readSession, requestHistory, stripCitations, STORAGE_KEY } from '../src/model.ts'
import type { AskResponse, Session } from '../src/model.ts'

const response: AskResponse = {
  question: '问题', rewritten_question: '独立问题', answer: '回答 [1] 和 [2]',
  citations: [{ number: 1, chunk_id: 'gdpr:art17:p3', label: 'GDPR Article 17(3)' },
    { number: 2, chunk_id: 'gdpr:art17:p1', label: 'GDPR Article 17(1)' }],
  sources: [
    { id: 'gdpr:art17:p1', text: 'Erasure conditions.\n\nSecond paragraph.', heading_path: ['GDPR', 'Article 17'], metadata: { corpus: 'gdpr', source_url: 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679' } },
    { id: 'gdpr:art17:p3', text: 'Erasure exceptions.', heading_path: ['GDPR', 'Article 17'], metadata: { corpus: 'gdpr' } },
  ],
}

test('citations use backend numbering rather than retrieval order and retain full passages', () => {
  const turn = completeTurn(makeTurn('问题'), response)
  assert.equal(turn.status, 'complete')
  assert.equal(turn.sources[0].id, 'gdpr:art17:p3')
  assert.equal(turn.sources[1].number, 2)
  assert.deepEqual(turn.sources[1].paragraphs, ['Erasure conditions.', 'Second paragraph.'])
  assert.equal(turn.answer, response.answer)
  assert.equal(turn.rewrittenQuestion, '独立问题')
})

test('an uncited answer does not acquire synthetic citations', () => {
  assert.deepEqual(completeTurn(makeTurn('问题'), { ...response, citations: [] }).sources, [])
})

test('RAG defaults on and each turn retains its mode through completion and history', () => {
  const retrieved = completeTurn(makeTurn('检索问题'), response)
  const direct = completeTurn(makeTurn('直接追问', undefined, false), {
    ...response, answer: '直接回答', citations: [], sources: [],
  })
  assert.equal(retrieved.ragEnabled, true)
  assert.equal(direct.ragEnabled, false)
  assert.deepEqual(direct.sources, [])
  assert.equal(retrieved.sources.length, 2)
  assert.deepEqual(requestHistory([retrieved, direct]), [
    { question: '检索问题', answer: '回答  和' },
    { question: '直接追问', answer: '直接回答' },
  ])
  assert.equal(readSession({ getItem: () => null }, false).ragEnabled, true)
})

test('unknown, repeated, or noncontiguous citations fail instead of binding another passage', () => {
  for (const citations of [
    [{ number: 1, chunk_id: 'unknown', label: 'Unknown' }],
    [{ number: 2, chunk_id: 'gdpr:art17:p1', label: 'GDPR' }],
    [{ number: 1, chunk_id: 'gdpr:art17:p1', label: 'GDPR' }, { number: 2, chunk_id: 'gdpr:art17:p1', label: 'GDPR' }],
  ]) assert.throws(() => completeTurn(makeTurn('问题'), { ...response, citations }), /inconsistent citations/)
})

test('only the last five completed earlier turns enter a retry or follow-up', () => {
  const turns = Array.from({ length: 8 }, (_, n) => completeTurn(makeTurn(`问题${n}`), response))
  turns[2].status = 'error'
  turns[5].status = 'pending'
  const history = requestHistory(turns)
  assert.deepEqual(history.map(t => t.question), ['问题1', '问题3', '问题4', '问题6', '问题7'])
  assert.ok(history.every(t => !t.answer.includes('[1]')))
  assert.deepEqual(requestHistory(turns, turns[4].id).map(t => t.question), ['问题0', '问题1', '问题3'])
  assert.ok(turns[0].answer.includes('[1]'), 'earlier displayed answers remain unchanged')
})

test('quotation context survives history without reusing source citation numbers', () => {
  const earlier = completeTurn(makeTurn('之前的问题'), response)
  const quotation = { text: 'Erasure exceptions.', source: earlier.sources[0], turnId: earlier.id }
  const turn = completeTurn(makeTurn('解释这句话', quotation), response)
  assert.equal(turn.question, '解释这句话')
  assert.ok(turn.requestQuestion.includes(quotation.text))
  assert.ok(requestHistory([turn])[0].question.includes('gdpr:art17:p3'))
  assert.equal(turn.quotation?.turnId, earlier.id)
  assert.throws(() => makeTurn('x'.repeat(20001)), /too long/)
})

test('source links reject active URL schemes and slide paths', () => {
  const malformed = { ...response, sources: response.sources.map(s => ({ ...s,
    metadata: { corpus: 'slides', source_url: 'javascript:alert(1)', source_file: '../secret.pdf' } })) }
  const sources = completeTurn(makeTurn('问题'), malformed).sources
  assert.ok(sources.every(s => s.url === undefined && s.file === undefined))
})

test('history strips only citation markers and leaves normal prose', () => {
  assert.equal(stripCitations('第17条 [1] 条件 [gdpr:art17:p1] [2, 3]'), '第17条  条件')
})

test('restored sessions retain live answers, mark interrupted requests, and omit unknown credentials', () => {
  const complete = completeTurn(makeTurn('问题'), response)
  const pending = makeTurn('追问')
  const saved: Session = { version: 2, activeId: 'chat', theme: 'dark', reducedMotion: true, ragEnabled: true,
    conversations: [{ id: 'chat', createdAt: 1, turns: [complete, pending] }] }
  const restored = readSession({ getItem: key => key === STORAGE_KEY ? JSON.stringify({ ...saved, apiKey: 'private' }) : null }, false)
  assert.equal(restored.conversations[0].turns[0].answer, complete.answer)
  assert.equal(restored.conversations[0].turns[1].status, 'error')
  assert.match(restored.conversations[0].turns[1].error!, /reloaded/)
  assert.ok(!JSON.stringify(restored).includes('private'))
})

test('saved RAG preference and mixed turn modes survive a tab reload', () => {
  const retrieved = completeTurn(makeTurn('检索问题'), response)
  const direct = completeTurn(makeTurn('直接回答', undefined, false), {
    ...response, answer: '没有检索来源的回答', citations: [], sources: [],
  })
  const pending = makeTurn('尚未完成', undefined, false)
  const saved: Session = { version: 2, activeId: 'chat', theme: 'light', reducedMotion: false, ragEnabled: false,
    conversations: [{ id: 'chat', createdAt: 1, turns: [retrieved, direct, pending] }] }
  const restored = readSession({ getItem: () => JSON.stringify(saved) }, false)
  assert.equal(restored.ragEnabled, false)
  assert.deepEqual(restored.conversations[0].turns.map(turn => turn.ragEnabled), [true, false, false])
  assert.deepEqual(restored.conversations[0].turns[0].sources.map(source => [source.id, source.number, source.paragraphs]),
    retrieved.sources.map(source => [source.id, source.number, source.paragraphs]))
  assert.deepEqual(restored.conversations[0].turns[1].sources, [])
  assert.equal(restored.conversations[0].turns[2].status, 'error')
})

test('legacy version 2 sessions migrate missing RAG modes to on independently of the current preference', () => {
  const { ragEnabled: _mode, ...legacyTurn } = completeTurn(makeTurn('旧问题'), response)
  const legacy = { version: 2, activeId: 'chat', theme: 'light', reducedMotion: false,
    conversations: [{ id: 'chat', createdAt: 1, turns: [legacyTurn] }] }
  const restored = readSession({ getItem: () => JSON.stringify(legacy) }, false)
  assert.equal(restored.ragEnabled, true)
  assert.equal(restored.conversations[0].turns[0].ragEnabled, true)
  assert.equal(restored.conversations[0].turns[0].answer, response.answer)
  const changedPreference = readSession({ getItem: () => JSON.stringify({ ...legacy, ragEnabled: false }) }, false)
  assert.equal(changedPreference.ragEnabled, false)
  assert.equal(changedPreference.conversations[0].turns[0].ragEnabled, true)
})

test('prototype, malformed, and inaccessible storage never show fixture answers', () => {
  for (const raw of ['null', 'broken', '{"version":1}', '{"version":2,"conversations":[null]}']) {
    assert.deepEqual(readSession({ getItem: () => raw }, true).conversations, [])
  }
  assert.equal(readSession({ getItem: () => { throw new Error('blocked') } }, true).reducedMotion, true)
})
