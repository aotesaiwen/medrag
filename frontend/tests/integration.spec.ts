import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

const key = 'browser-test-key'
const fixture = {
  rewritten_question: '关于删除权的独立问题',
  answer: '**真实回答**，先引用条件 [2]，再引用例外 [1]。普通编号 [99] 保留。',
  citations: [{ number: 1, chunk_id: 'gdpr:art17:p3', label: 'GDPR Article 17(3)' },
    { number: 2, chunk_id: 'gdpr:art17:p1', label: 'GDPR Article 17(1)' }],
  sources: [
    { id: 'gdpr:art17:p1', text: 'The personal data are no longer necessary.', heading_path: ['GDPR', 'Article 17'], metadata: { corpus: 'gdpr', source_url: 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679' } },
    { id: 'gdpr:art17:p3', text: 'Processing is necessary for exercising the right of freedom of expression and information.', heading_path: ['GDPR', 'Article 17'], metadata: { corpus: 'gdpr' } },
  ],
}

const directFixture = {
  rewritten_question: '直接回答的问题', answer: '真实回答：这次没有检索资料。', citations: [], sources: [],
}
type AskRequest = { question: string; history: { question: string; answer: string }[]; rag_enabled: boolean }

const slideTranscription = 'TRANSCRIBED SLIDE TEXT MUST NEVER APPEAR IN THE SOURCE VIEW'
const slideFixture = {
  rewritten_question: '讲义中的隐私原则是什么？',
  answer: '真实回答：第一页 [1]，第二页 [2]，相关法规 [3]。',
  citations: [
    { number: 1, chunk_id: 'slides:lecture6:s7', label: 'Lecture 6, Slide 7' },
    { number: 2, chunk_id: 'slides:lecture6:s9', label: 'Lecture 6, Slide 9' },
    { number: 3, chunk_id: 'gdpr:art17:p1', label: 'GDPR Article 17(1)' },
  ],
  sources: [
    { id: 'slides:lecture6:s7', text: slideTranscription, heading_path: ['Lecture 6'],
      metadata: { corpus: 'slides', source_file: 'Lecture 6.pdf', slide: 7 } },
    { id: 'slides:lecture6:s9', text: slideTranscription, heading_path: ['Lecture 6'],
      metadata: { corpus: 'slides', source_file: 'Lecture 6.pdf', slide: 9 } },
    fixture.sources[0],
  ],
}

async function slidePng(page: Page, number: number) {
  const data = await page.evaluate(slide => {
    const canvas = document.createElement('canvas')
    canvas.width = slide * 20; canvas.height = 100
    const context = canvas.getContext('2d')!
    context.fillStyle = slide === 7 ? '#385f8a' : '#846131'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.fillStyle = '#fff'; context.fillText(`Original slide ${slide}`, 8, 24)
    return canvas.toDataURL('image/png').split(',')[1]
  }, number)
  return Buffer.from(data, 'base64')
}

async function expectSlide(page: Page, number: number) {
  const region = page.getByRole('region', { name: 'Original slide page', exact: true })
  const image = region.getByRole('img', { name: `Lecture 6, Slide ${number} — original slide page`, exact: true })
  await expect(image).toBeVisible()
  await expect.poll(() => image.evaluate(element => (element as HTMLImageElement).naturalWidth)).toBe(number * 20)
  await expect(image).toHaveAttribute('src', /^blob:/)
  await expect(page.locator('iframe[title="Source passage"]')).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(slideTranscription)
}

test.beforeEach(async ({ page }) => {
  // Browser checks use local assets and fake providers; no hosted model or CDN calls.
  await page.route('https://**/*', route => route.abort())
  await page.route('**/health', route => route.fulfill({ json: { ready: true, llm_configured: true } }))
  await page.route('**/auth', route => route.fulfill({
    status: route.request().headers().authorization === `Bearer ${key}` ? 200 : 401,
    json: { ok: true },
  }))
})

async function connectUI(page: Page) {
  await page.getByRole('button', { name: 'connect', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'connect', exact: true })
  await dialog.getByLabel('API key', { exact: true }).fill(key)
  await dialog.getByRole('button', { name: 'connect', exact: true }).click()
  await expect(dialog).not.toBeVisible()
}

async function send(page: Page, text: string) {
  await page.getByRole('textbox', { name: 'Question', exact: true }).fill(text)
  await page.getByRole('button', { name: 'Send question', exact: true }).click()
  await expect(page.locator('.turn').last().getByLabel('Answer', { exact: true })).toContainText('真实回答')
}

test('live response mapping, per-turn citations, five-turn history, and storage separation', async ({ page }) => {
  const requests: { question: string; history: { question: string; answer: string }[] }[] = []
  await page.route('**/ask', async route => {
    expect(route.request().headers().authorization).toBe(`Bearer ${key}`)
    const body = route.request().postDataJSON(); requests.push(body)
    await route.fulfill({ json: { ...fixture, question: body.question } })
  })
  await page.goto('/')
  await connectUI(page)
  await send(page, '删除权条件？')
  await page.getByRole('button', { name: 'Open citation 2, answer 1', exact: true }).click()
  const passage = page.frameLocator('iframe[title="Source passage"]').locator('.passage')
  await expect(passage).toContainText('no longer necessary')
  await expect(page.getByRole('button', { name: 'View source 2: GDPR Article 17(1)', exact: true })).toHaveAttribute('aria-pressed', 'true')
  expect(await page.locator('.answer-body .citation-tile').count()).toBe(2)
  await send(page, '它有哪些例外？')
  expect(requests[1].history).toHaveLength(1)
  expect(requests[1].history[0].answer).not.toContain('[1]')
  await page.getByRole('button', { name: 'Open citation 1, answer 1', exact: true }).click()
  await expect(passage).toContainText('freedom of expression')
  for (let n = 2; n < 7; n++) await send(page, `追问${n}`)
  expect(requests[6].history).toHaveLength(5)
  expect(requests[6].history[0].question).toBe('它有哪些例外？')
  const stored = await page.evaluate(() => JSON.stringify({ session: { ...sessionStorage }, local: { ...localStorage } }))
  expect(stored).not.toContain(key)
  expect(await page.evaluate(() => localStorage.length)).toBe(0)
  await page.reload()
  await expect(page.locator('.turn')).toHaveCount(7)
  await expect(page.getByText('API key required', { exact: true })).toBeVisible()
})

test('source selection stays in the iframe and sends its quotation with provenance', async ({ page }) => {
  const requests: AskRequest[] = []
  await page.route('**/ask', route => {
    const body = route.request().postDataJSON(); requests.push(body)
    return route.fulfill({ json: { ...(body.rag_enabled ? fixture : directFixture), question: body.question } })
  })
  await page.goto('/'); await connectUI(page); await send(page, '删除权？')
  const mainRag = page.getByRole('switch', { name: 'RAG', exact: true })
  await mainRag.click()
  await expect(mainRag).toHaveAttribute('aria-checked', 'false')
  const frame = page.frameLocator('iframe[title="Source passage"]')
  await frame.locator('.passage p').first().evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element)
    const selection = document.getSelection()!; selection.removeAllRanges(); selection.addRange(range)
    element.dispatchEvent(new KeyboardEvent('keyup', { key: 'Shift', bubbles: true }))
  })
  const composer = frame.getByRole('complementary', { name: 'Ask about this passage' })
  await expect(composer).toBeVisible()
  const quoteRag = composer.getByRole('switch', { name: 'RAG', exact: true })
  await expect(quoteRag).toHaveAttribute('aria-checked', 'false')
  await quoteRag.click()
  await expect(mainRag).toHaveAttribute('aria-checked', 'true')
  await quoteRag.click()
  await expect(mainRag).toHaveAttribute('aria-checked', 'false')
  await composer.getByRole('textbox', { name: 'Question', exact: true }).fill('解释选中的例外')
  expect(await page.evaluate(() => window.getSelection()?.toString())).toBe('')
  expect(await frame.locator('.passage').evaluate(element => element.ownerDocument.defaultView!.CSS.highlights.has('quoted-passage'))).toBe(true)
  await composer.getByRole('button', { name: 'Send question', exact: true }).click()
  await expect(page.locator('.turn')).toHaveCount(2)
  await expect(page.locator('.turn').last().getByLabel('Answer', { exact: true })).toContainText('真实回答')
  expect(requests[1].question).toContain('gdpr:art17:p3')
  expect(requests[1].question).toContain('freedom of expression')
  expect(requests[1].rag_enabled).toBe(false)
  expect(requests[1].history).toHaveLength(1)
  await expect(page.locator('.turn').last().locator('.user-quotation')).toContainText('freedom of expression')
})

test('RAG can change on, off, and on in one conversation while preserving history and earlier citations', async ({ page }) => {
  const requests: AskRequest[] = []
  await page.route('**/ask', route => {
    const body: AskRequest = route.request().postDataJSON(); requests.push(body)
    return route.fulfill({ json: { ...(body.rag_enabled ? fixture : directFixture), question: body.question } })
  })
  await page.goto('/'); await connectUI(page)
  const rag = page.getByRole('switch', { name: 'RAG', exact: true })
  await expect(rag).toHaveAttribute('aria-checked', 'true')
  await expect(rag).toHaveText('RAG on')
  await send(page, '先检索删除权')
  await rag.click()
  await expect(rag).toHaveText('RAG off')
  await page.reload()
  await expect(rag).toHaveAttribute('aria-checked', 'false')
  await expect(page.locator('.turn')).toHaveCount(1)
  await connectUI(page)
  await send(page, '不检索，解释刚才的答案')
  const directTurn = page.locator('.turn').nth(1)
  await expect(directTurn.getByText('RAG off', { exact: true })).toBeVisible()
  await expect(directTurn.locator('.citation-tile')).toHaveCount(0)
  await expect(page.getByText('RAG was off for this answer. No sources were retrieved.', { exact: true })).toBeVisible()
  await expect(page.locator('iframe[title="Source passage"]')).toHaveCount(0)
  await page.getByRole('button', { name: 'Open citation 1, answer 1', exact: true }).click()
  await expect(page.frameLocator('iframe[title="Source passage"]').locator('.passage')).toContainText('freedom of expression')
  await expect(rag).toHaveAttribute('aria-checked', 'false')
  await rag.click()
  await send(page, '重新检索其例外')
  expect(requests.map(request => request.rag_enabled)).toEqual([true, false, true])
  expect(requests[1].history.map(turn => turn.question)).toEqual(['先检索删除权'])
  expect(requests[2].history.map(turn => turn.question)).toEqual(['先检索删除权', '不检索，解释刚才的答案'])
  expect(requests[2].history[1].answer).toBe(directFixture.answer)
  expect(requests[2].history[0].answer).not.toContain('[1]')
  await expect(page.locator('.turn').first().locator('.citation-tile')).toHaveCount(2)
  await expect(page.locator('.turn').last().locator('.citation-tile')).toHaveCount(2)
  await expect(page.locator('.turn').last().getByText('RAG off', { exact: true })).toHaveCount(0)
})

test('changing RAG while an answer is pending affects the next message and preserves the failed turn mode on retry', async ({ page }) => {
  const requests: AskRequest[] = []
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/ask', async route => {
    const body: AskRequest = route.request().postDataJSON(); requests.push(body)
    if (requests.length === 1) {
      await gate
      return route.fulfill({ status: 502, json: { detail: 'provider unavailable' } }).catch(() => {})
    }
    return route.fulfill({ json: { ...(body.rag_enabled ? fixture : directFixture), question: body.question } })
  })
  try {
    await page.goto('/'); await connectUI(page)
    await page.getByRole('textbox', { name: 'Question', exact: true }).fill('需要检索的问题')
    await page.getByRole('button', { name: 'Send question', exact: true }).click()
    await expect.poll(() => requests.length).toBe(1)
    const rag = page.getByRole('switch', { name: 'RAG', exact: true })
    await expect(rag).toBeEnabled()
    await rag.click()
    await expect(rag).toHaveAttribute('aria-checked', 'false')
    expect(requests[0].rag_enabled).toBe(true)
    release()
    await expect(page.getByRole('alert')).toContainText('could not complete')
    await page.getByRole('button', { name: 'retry', exact: true }).click()
    await expect(page.locator('.turn').first().getByLabel('Answer', { exact: true })).toContainText('真实回答')
    await expect(page.locator('.turn')).toHaveCount(1)
    await expect(rag).toHaveAttribute('aria-checked', 'false')
    expect(requests[1].rag_enabled).toBe(true)
    expect(requests[1].question).toBe(requests[0].question)
    expect(requests[1].history).toEqual([])
    await send(page, '接下来直接回答')
    expect(requests.map(request => request.rag_enabled)).toEqual([true, true, false])
    expect(requests[2].history.map(turn => turn.question)).toEqual(['需要检索的问题'])
    await expect(page.locator('.turn').first().locator('.citation-tile')).toHaveCount(2)
    await expect(page.locator('.turn').last().getByText('RAG off', { exact: true })).toBeVisible()
  } finally { release() }
})

test('errors can be retried without duplicating turns; deletion cannot resurrect a pending chat', async ({ page }) => {
  let count = 0
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/ask', async route => {
    count++
    if (count === 1) return route.fulfill({ status: 502, json: { detail: 'private diagnostics' } })
    if (count === 3) await gate
    await route.fulfill({ json: { ...fixture, question: route.request().postDataJSON().question } }).catch(() => {})
  })
  await page.goto('/'); await connectUI(page)
  await page.getByRole('textbox', { name: 'Question', exact: true }).fill('原问题')
  await page.getByRole('button', { name: 'Send question', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('could not complete')
  await expect(page.getByRole('alert')).not.toContainText('private diagnostics')
  await page.getByRole('button', { name: 'retry', exact: true }).click()
  await expect(page.locator('.turn')).toHaveCount(1)
  await expect(page.locator('.answer-body')).toContainText('真实回答')
  await page.getByRole('textbox', { name: 'Question', exact: true }).fill('待处理问题')
  await page.getByRole('button', { name: 'Send question', exact: true }).click()
  await expect(page.getByText('Preparing an answer…', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '[2] history', exact: true }).click()
  await page.getByRole('button', { name: 'Delete conversation: 原问题', exact: true }).click()
  release()
  await expect(page.locator('.turn')).toHaveCount(0)
  await expect(page.getByText('No conversations yet', { exact: true })).toBeVisible()
  await page.reload(); await expect(page.locator('.turn')).toHaveCount(0)
})

test('the original theme, motion, and instructions controls remain functional', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Switch to dark mode' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: '[4] Motion', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-reduced', 'true')
  await page.getByRole('button', { name: '[5] instructions', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'instructions', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).not.toBeVisible()
})

test('blocked browser storage still allows an in-memory conversation', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'sessionStorage', { get() { throw new DOMException('Storage blocked', 'SecurityError') } })
  })
  await page.route('**/ask', route => route.fulfill({ json: { ...fixture, question: route.request().postDataJSON().question } }))
  await page.goto('/')
  await expect(page.getByText('Conversation history cannot be saved in this tab.', { exact: true })).toBeVisible()
  await connectUI(page); await send(page, '删除权是什么？')
  await expect(page.locator('.turn')).toHaveCount(1)
})

test('slide citations show authenticated original pages, legal passages still work, and reconnect reloads the image', async ({ page }) => {
  const requestedPages: number[] = []
  await page.route('**/ask', route => route.fulfill({ json: { ...slideFixture, question: route.request().postDataJSON().question } }))
  await page.goto('/')
  const images = new Map([[7, await slidePng(page, 7)], [9, await slidePng(page, 9)]])
  await page.route('**/slides/**/pages/*', async route => {
    const url = new URL(route.request().url())
    expect(route.request().headers().authorization).toBe(`Bearer ${key}`)
    expect(url.pathname).toMatch(/^\/slides\/Lecture%206\.pdf\/pages\/(7|9)$/)
    expect(url.search).toBe('')
    expect(url.href).not.toContain(key)
    const number = Number(url.pathname.split('/').at(-1)); requestedPages.push(number)
    await route.fulfill({ contentType: 'image/png', body: images.get(number)! })
  })
  await connectUI(page); await send(page, '讲义中的隐私原则？')
  await expectSlide(page, 7)
  await page.getByRole('button', { name: 'Open citation 2, answer 1', exact: true }).click()
  await expectSlide(page, 9)
  await page.getByRole('button', { name: 'Open citation 3, answer 1', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Original slide page', exact: true })).toHaveCount(0)
  await expect(page.frameLocator('iframe[title="Source passage"]').locator('.passage')).toContainText('no longer necessary')
  await page.getByRole('button', { name: 'Open citation 2, answer 1', exact: true }).click()
  await expectSlide(page, 9)
  const beforeReconnect = requestedPages.length
  await page.getByRole('button', { name: 'disconnect', exact: true }).click()
  const region = page.getByRole('region', { name: 'Original slide page', exact: true })
  await expect(region).toContainText('Connect to view this slide.')
  await expect(region.getByRole('img')).toHaveCount(0)
  await connectUI(page)
  await expectSlide(page, 9)
  expect(requestedPages.length).toBe(beforeReconnect + 1)
  expect(requestedPages.slice(0, 2)).toEqual([7, 9])
  const stored = await page.evaluate(() => JSON.stringify({ session: { ...sessionStorage }, local: { ...localStorage } }))
  expect(stored).not.toContain(key)
  expect(stored).not.toContain('blob:')
})

test('a late response for the previous citation cannot replace the current original page', async ({ page }) => {
  let release: () => void = () => {}
  let finished: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  const staleFinished = new Promise<void>(resolve => { finished = resolve })
  let firstRequested = false
  await page.route('**/ask', route => route.fulfill({ json: { ...slideFixture, question: route.request().postDataJSON().question } }))
  await page.goto('/')
  const first = await slidePng(page, 7), second = await slidePng(page, 9)
  await page.route('**/slides/**/pages/*', async route => {
    if (route.request().url().endsWith('/7')) {
      firstRequested = true
      await gate
      try { await route.fulfill({ contentType: 'image/png', body: first }) }
      catch { /* Citation switching may already have cancelled this request. */ }
      finally { finished() }
    } else {
      await route.fulfill({ contentType: 'image/png', body: second })
    }
  })
  try {
    await connectUI(page); await send(page, '比较两个讲义页面')
    await expect.poll(() => firstRequested).toBe(true)
    await page.getByRole('button', { name: 'Open citation 2, answer 1', exact: true }).click()
    await expectSlide(page, 9)
    release(); await staleFinished
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
    await expectSlide(page, 9)
    await expect(page.getByRole('img', { name: 'Lecture 6, Slide 7 — original slide page', exact: true })).toHaveCount(0)
  } finally { release() }
})

test('failed or unavailable slide images never fall back to transcription and failed images can be retried', async ({ page }) => {
  let attempts = 0
  const missingMetadata = { ...slideFixture, sources: slideFixture.sources.map(source =>
    source.id === 'slides:lecture6:s9' ? { ...source, metadata: { corpus: 'slides' } } : source) }
  await page.route('**/ask', route => route.fulfill({ json: { ...missingMetadata, question: route.request().postDataJSON().question } }))
  await page.goto('/')
  const image = await slidePng(page, 7)
  await page.route('**/slides/**/pages/*', route => {
    attempts++
    return attempts === 1
      ? route.fulfill({ status: 502, json: { detail: 'private renderer diagnostics' } })
      : route.fulfill({ contentType: 'image/png', body: image })
  })
  await connectUI(page); await send(page, '显示讲义原页')
  const region = page.getByRole('region', { name: 'Original slide page', exact: true })
  await expect(region.getByRole('alert')).toBeVisible()
  await expect(page.locator('body')).not.toContainText('private renderer diagnostics')
  await expect(page.locator('body')).not.toContainText(slideTranscription)
  await expect(page.locator('iframe[title="Source passage"]')).toHaveCount(0)
  await region.getByRole('button', { name: 'retry slide', exact: true }).click()
  await expectSlide(page, 7)
  expect(attempts).toBe(2)
  await page.getByRole('button', { name: 'Open citation 2, answer 1', exact: true }).click()
  await expect(region.getByRole('alert')).toContainText('The original page is unavailable for this slide.')
  await expect(region.getByRole('img')).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(slideTranscription)
  await expect(page.locator('iframe[title="Source passage"]')).toHaveCount(0)
  expect(attempts).toBe(2)
})

async function renderMarkdownFixture(page: Page, answer: string) {
  await page.route('**/ask', route => {
    expect(route.request().headers().authorization).toBe(`Bearer ${key}`)
    return route.fulfill({ json: { ...fixture, answer, question: route.request().postDataJSON().question } })
  })
  await page.goto('/')
  await connectUI(page)
  await page.getByRole('textbox', { name: 'Question', exact: true }).fill('比较 GDPR 和 HIPAA 的要求')
  await page.getByRole('button', { name: 'Send question', exact: true }).click()
  const answerBody = page.locator('.turn').last().getByLabel('Answer', { exact: true })
  await expect(answerBody).toHaveAttribute('aria-busy', 'false')
  await expect(answerBody.getByRole('table')).toHaveCount(1)
  return answerBody
}

test('Markdown tables preserve semantic cells, alignment, emphasis, and verified citation behavior', async ({ page }) => {
  const answer = [
    '两类删除权规定：',
    '',
    '| 项目 | 说明 | 依据 |',
    '| :--- | :---: | ---: |',
    '| **删除条件** | *不再需要* | [2] |',
    '| 删除例外 | 普通编号 [99] 与 `代码 [1]` | [1] |',
  ].join('\n')
  const body = await renderMarkdownFixture(page, answer)
  const region = body.getByRole('region', { name: 'Answer table', exact: true })
  const table = region.getByRole('table')
  await expect(table.getByRole('columnheader')).toHaveText(['项目', '说明', '依据'])
  await expect(table.locator('tbody tr')).toHaveCount(2)
  await expect(table.getByRole('cell')).toHaveCount(6)
  await expect(table.locator('strong')).toHaveText('删除条件')
  await expect(table.locator('em')).toHaveText('不再需要')
  for (const [index, alignment] of ['left', 'center', 'right'].entries()) {
    await expect(table.getByRole('columnheader').nth(index)).toHaveCSS('text-align', alignment)
    await expect(table.locator('tbody tr').first().getByRole('cell').nth(index)).toHaveCSS('text-align', alignment)
  }
  await expect(table).toContainText('普通编号 [99]')
  await expect(table.locator('code')).toHaveText('代码 [1]')
  await expect(table.locator('code button')).toHaveCount(0)
  await expect(table.locator('.citation-tile')).toHaveCount(2)
  await table.getByRole('button', { name: 'Open citation 2, answer 1', exact: true }).click()
  const passage = page.frameLocator('iframe[title="Source passage"]').locator('.passage')
  await expect(passage).toContainText('no longer necessary')
  await expect(page.getByRole('button', { name: 'View source 2: GDPR Article 17(1)', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await table.getByRole('button', { name: 'Open citation 1, answer 1', exact: true }).click()
  await expect(passage).toContainText('freedom of expression')
})

test('collapsed Markdown tables render without treating code or unsafe raw HTML as markup', async ({ page }) => {
  const collapsed = '| 项目 | GDPR | HIPAA | | --- | --- | --- | '
    + '| 适用对象 | 数据控制者 | 受规管实体 | '
    + '| 访问权 | 个人数据 | 健康信息 | '
    + '| 更正权 | 不准确的数据 | 病历更正 | '
    + '| 删除权 | 适用法定条件 | 规则不同 | '
    + '| 来源 | 条文 [2] | 另行核实 |'
  const fenced = '| code | remains |\n| --- | --- |\n| [1] | a \\| b |'
  const answer = [
    collapsed,
    '',
    '行内代码：`| not | a table | [1] |`。',
    '',
    '```text', fenced, '```',
    '',
    '<script>window.tableScriptExecuted = true</script>',
    '<img src="missing-image" onerror="window.tableScriptExecuted = true">',
  ].join('\n')
  const body = await renderMarkdownFixture(page, answer)
  const table = body.getByRole('table')
  await expect(table.getByRole('columnheader')).toHaveText(['项目', 'GDPR', 'HIPAA'])
  await expect(table.locator('tbody tr')).toHaveCount(5)
  await expect(table.getByRole('cell')).toHaveCount(15)
  await expect(table.locator('tbody tr').first().getByRole('cell')).toHaveText(['适用对象', '数据控制者', '受规管实体'])
  await expect(table.locator('.citation-tile')).toHaveCount(1)
  await expect(body.locator('pre code')).toHaveText(fenced)
  await expect(body.locator('p code')).toHaveText('| not | a table | [1] |')
  await expect(body.locator('code button')).toHaveCount(0)
  await expect(body.locator('script, img')).toHaveCount(0)
  expect(await page.evaluate(() => 'tableScriptExecuted' in window)).toBe(false)
})

test('wide answer tables scroll inside the keyboard-accessible wrapper at a small laptop viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1120, height: 900 })
  const headers = Array.from({ length: 12 }, (_, index) => `要求 ${index + 1}`)
  const answer = [
    `| ${headers.join(' | ')} |`,
    `| ${headers.map(() => '---').join(' | ')} |`,
    `| ${headers.map((_, index) => `跨境健康信息处理条件 ${index + 1}`).join(' | ')} |`,
  ].join('\n')
  const body = await renderMarkdownFixture(page, answer)
  const region = body.getByRole('region', { name: 'Answer table', exact: true })
  await expect(region).toHaveClass(/answer-table-scroll/)
  await expect(region).toHaveAttribute('tabindex', '0')
  const geometry = await region.evaluate(element => {
    const container = element.closest('.left-panel')!
    const box = element.getBoundingClientRect(), parent = container.getBoundingClientRect()
    return { width: element.clientWidth, contentWidth: element.scrollWidth,
      left: box.left, right: box.right, parentLeft: parent.left, parentRight: parent.right,
      overflow: getComputedStyle(element).overflowX,
      pageWidth: document.documentElement.scrollWidth, viewportWidth: window.innerWidth }
  })
  expect(geometry.contentWidth).toBeGreaterThan(geometry.width)
  expect(['auto', 'scroll']).toContain(geometry.overflow)
  expect(geometry.left).toBeGreaterThanOrEqual(geometry.parentLeft)
  expect(geometry.right).toBeLessThanOrEqual(geometry.parentRight + 1)
  expect(geometry.pageWidth).toBeLessThanOrEqual(geometry.viewportWidth + 1)
  await region.focus()
  await expect(region).toBeFocused()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => region.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)
  const keyboardScroll = await region.evaluate(element => element.scrollLeft)
  await region.evaluate(element => { element.scrollLeft = element.scrollWidth - element.clientWidth })
  await expect.poll(() => region.evaluate(element => element.scrollLeft)).toBeGreaterThan(keyboardScroll)
  await expect(region.getByRole('cell').last()).toBeInViewport()
  expect(await page.evaluate(() => window.scrollX)).toBe(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
})
