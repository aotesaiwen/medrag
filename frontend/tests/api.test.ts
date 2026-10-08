import test from 'node:test'
import assert from 'node:assert/strict'
import { APIError, ask, connect, fetchSlidePage } from '../src/api.ts'
import { makeTurn } from '../src/model.ts'

test('requests attach the key as a header, with quote context and a bounded history', async t => {
  const calls: { path: string; options: RequestInit }[] = []
  t.mock.method(globalThis, 'fetch', async (path: string, options: RequestInit) => {
    calls.push({ path, options }); return new Response(JSON.stringify({ answer: '答案' }), { status: 200 })
  })
  const turn = makeTurn('问题')
  await ask(turn, [], 'private-key', new AbortController().signal)
  assert.equal(calls[0].path, '/ask')
  assert.equal((calls[0].options.headers as Record<string, string>).Authorization, 'Bearer private-key')
  assert.deepEqual(JSON.parse(calls[0].options.body as string), { question: '问题', history: [], rag_enabled: true })
  assert.ok(!(calls[0].options.body as string).includes('private-key'))
  assert.equal(calls[0].options.cache, 'no-store')
})

test('each request sends its captured RAG mode, including retries of an earlier turn', async t => {
  const bodies: { question: string; history: unknown[]; rag_enabled: boolean }[] = []
  t.mock.method(globalThis, 'fetch', async (_path: string, options: RequestInit) => {
    bodies.push(JSON.parse(options.body as string))
    return new Response(JSON.stringify({ answer: '答案' }), { status: 200 })
  })
  const retrieved = makeTurn('检索资料')
  const direct = makeTurn('直接回答', undefined, false)
  for (const turn of [retrieved, direct, retrieved, direct]) {
    await ask(turn, [], 'private-key', new AbortController().signal)
  }
  assert.deepEqual(bodies.map(body => body.rag_enabled), [true, false, true, false])
  assert.deepEqual(bodies.map(body => body.question), ['检索资料', '直接回答', '检索资料', '直接回答'])
  assert.ok(bodies.every(body => body.history.length === 0))
})

test('authentication requests use the protected endpoint', async t => {
  const paths: string[] = []
  t.mock.method(globalThis, 'fetch', async (path: string) => {
    paths.push(path); return new Response('response', { status: 200 })
  })
  await connect('key')
  assert.deepEqual(paths, ['/auth'])
})

test('upstream error bodies cannot expose credentials or questions in the UI', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('private raw body', { status: 401 }))
  await assert.rejects(connect('key'), (error: Error) => error instanceof APIError && error.status === 401 && !error.message.includes('private raw body'))
})

test('cancellation is distinguishable from connection failure', async t => {
  const mocked = t.mock.method(globalThis, 'fetch', async () => { throw new DOMException('cancelled', 'AbortError') })
  await assert.rejects(ask(makeTurn('问题'), [], 'key', new AbortController().signal), { name: 'AbortError' })
  mocked.mock.mockImplementation(async () => { throw new TypeError('network details') })
  await assert.rejects(connect('key'), /Cannot reach the service/)
})

test('slide pages use the exact encoded page endpoint and keep credentials in headers', async t => {
  const calls: { path: string; options: RequestInit }[] = []
  t.mock.method(globalThis, 'fetch', async (path: string, options: RequestInit) => {
    calls.push({ path, options })
    return new Response('image fixture', { headers: { 'Content-Type': 'image/png; charset=binary' } })
  })
  const blob = await fetchSlidePage('Lecture 6 #ethics.pdf', 7, 'private-slide-key', new AbortController().signal)
  assert.ok(blob.type.startsWith('image/png'))
  assert.equal(calls[0].path, '/slides/Lecture%206%20%23ethics.pdf/pages/7')
  assert.equal((calls[0].options.headers as Record<string, string>).Authorization, 'Bearer private-slide-key')
  assert.equal(calls[0].options.cache, 'no-store')
  assert.ok(!calls[0].path.includes('private-slide-key'))
  assert.equal(calls[0].options.body, undefined)
})

test('slide pages reject successful responses that are not PNG images', async t => {
  const mocked = t.mock.method(globalThis, 'fetch', async () => new Response('private response body', {
    headers: { 'Content-Type': 'application/pdf' },
  }))
  for (const contentType of ['application/pdf', 'text/html', null]) {
    mocked.mock.mockImplementation(async () => new Response(new Uint8Array([1]), {
      headers: contentType ? { 'Content-Type': contentType } : {},
    }))
    await assert.rejects(fetchSlidePage('Lecture 6.pdf', 7, 'key', new AbortController().signal),
      (error: Error) => error instanceof APIError && error.status === 502 && /invalid slide image/.test(error.message))
  }
})

test('an unrenderable slide reports a source error without exposing renderer details', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('private renderer diagnostics', { status: 422 }))
  await assert.rejects(fetchSlidePage('Lecture 6.pdf', 7, 'key', new AbortController().signal),
    (error: Error) => error instanceof APIError && error.status === 422
      && /slide page could not be rendered/.test(error.message)
      && !/question|private renderer diagnostics/.test(error.message))
})

test('switching away can cancel a pending slide fetch without a connection-error fallback', async t => {
  let receivedSignal: AbortSignal | undefined
  t.mock.method(globalThis, 'fetch', async (_path: string, options: RequestInit) => {
    receivedSignal = options.signal as AbortSignal
    return new Promise<Response>((_resolve, reject) => {
      receivedSignal!.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })
    })
  })
  const controller = new AbortController()
  const request = fetchSlidePage('Lecture 6.pdf', 7, 'key', controller.signal)
  controller.abort()
  await assert.rejects(request, { name: 'AbortError' })
  assert.equal(receivedSignal?.aborted, true)
})
