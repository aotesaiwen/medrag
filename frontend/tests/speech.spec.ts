import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

const key = 'speech-browser-test'
const answer = {
  question: '测试', rewritten_question: '测试',
  answer: '**数据保护**需要明确目的 [1]。\n\n第二段解释删除条件。',
  citations: [{ number: 1, chunk_id: 'gdpr:art17:p1', label: 'GDPR Article 17(1)' }],
  sources: [{ id: 'gdpr:art17:p1', text: 'Personal data protection.', heading_path: ['GDPR'], metadata: { corpus: 'gdpr' } }],
}
function wav() {
  const samples = 24000 * 5
  const data = Buffer.alloc(44 + samples * 2)
  data.write('RIFF'); data.writeUInt32LE(36 + samples * 2, 4); data.write('WAVEfmt ', 8)
  data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22)
  data.writeUInt32LE(24000, 24); data.writeUInt32LE(48000, 28)
  data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34)
  data.write('data', 36); data.writeUInt32LE(samples * 2, 40)
  return data
}
async function setup(page: Page) {
  await page.route('https://**/*', route => route.abort())
  await page.route('**/health', route => route.fulfill({ json: { ready: true, llm_configured: true } }))
  await page.route('**/auth', route => route.fulfill({ json: { ok: true } }))
  await page.route('**/ask', route => route.fulfill({ json: answer }))
  await page.goto('/')
  await page.getByRole('button', { name: 'connect', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'connect', exact: true })
  await dialog.getByLabel('API key', { exact: true }).fill(key)
  await dialog.getByRole('button', { name: 'connect', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await page.getByRole('textbox', { name: 'Question', exact: true }).fill('测试')
  await page.getByRole('button', { name: 'Send question' }).click()
  await expect(page.locator('.answer-content')).toContainText('数据保护')
}

test('speech is opt-in, skips citations, pauses, resumes, replays from memory, and stops on navigation', async ({ page }) => {
  const requests: string[] = []
  await page.route('**/speech', route => {
    expect(route.request().headers().authorization).toBe(`Bearer ${key}`)
    requests.push(route.request().postDataJSON().text)
    return route.fulfill({ contentType: 'audio/wav', body: wav() })
  })
  await setup(page)
  expect(requests).toHaveLength(0)
  const controls = page.getByRole('group', { name: 'Speech for answer 1' })
  await controls.getByRole('button', { name: 'listen', exact: true }).click()
  await expect(controls.getByRole('button', { name: 'pause', exact: true })).toBeVisible()
  expect(requests).toEqual(['数据保护需要明确目的 。\n第二段解释删除条件。'])
  await controls.getByRole('button', { name: 'pause', exact: true }).click()
  await expect(controls.getByRole('button', { name: 'resume', exact: true })).toBeVisible()
  await controls.getByRole('button', { name: 'resume', exact: true }).click()
  await expect(controls.getByRole('button', { name: 'pause', exact: true })).toBeVisible()
  await controls.getByRole('button', { name: 'stop', exact: true }).click()
  await controls.evaluate(element => {
    const messages: string[] = []
    Reflect.set(window, 'speechTestReplayMessages', messages)
    new MutationObserver(() => {
      const message = element.querySelector('[role="status"]')?.textContent
      if (message) messages.push(message)
    }).observe(element, { childList: true, subtree: true, characterData: true })
  })
  await controls.getByRole('button', { name: 'listen', exact: true }).click()
  await expect(controls.getByRole('button', { name: 'pause', exact: true })).toBeVisible()
  expect(requests).toHaveLength(1)
  expect(await page.evaluate(() => Reflect.get(window, 'speechTestReplayMessages'))).toEqual([])
  const stored = await page.evaluate(() => JSON.stringify({ ...sessionStorage }))
  expect(stored).not.toContain('blob:'); expect(stored).not.toContain(key)
  await page.getByRole('button', { name: '[1] chat', exact: true }).click()
  await expect(controls).toHaveCount(0)
})

test('preparation stays visible until synthesis completes, including a long wait', async ({ page }) => {
  let release!: () => void
  const blocked = new Promise<void>(resolve => { release = resolve })
  await page.route('**/speech', async route => {
    await blocked
    await route.fulfill({ contentType: 'audio/wav', body: wav() })
  })
  await setup(page)
  await page.clock.install()
  const controls = page.getByRole('group', { name: 'Speech for answer 1' })
  await controls.getByRole('button', { name: 'listen', exact: true }).click()
  await expect(controls.getByRole('status')).toHaveText('Preparing audio…')
  await page.clock.fastForward(11000)
  await expect(controls.getByRole('status')).toHaveText('Still preparing…')
  await expect(controls.getByRole('button', { name: 'cancel audio', exact: true })).toBeVisible()
  release()
  await expect(controls.getByRole('button', { name: 'pause', exact: true })).toBeVisible()
  await expect(controls.getByRole('status')).toHaveCount(0)
})

test('selected answer text is read independently', async ({ page }) => {
  const requests: string[] = []
  await page.route('**/speech', route => {
    requests.push(route.request().postDataJSON().text)
    return route.fulfill({ contentType: 'audio/wav', body: wav() })
  })
  await setup(page)
  await page.locator('.answer-content p').last().evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element)
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range)
    document.dispatchEvent(new Event('selectionchange'))
  })
  await page.getByRole('button', { name: 'listen to selection', exact: true }).click()
  await expect(page.getByRole('button', { name: 'pause', exact: true })).toBeVisible()
  expect(requests).toEqual(['第二段解释删除条件。'])
})

test('cancelled synthesis never starts late playback and errors can be retried', async ({ page }) => {
  let release!: () => void
  const blocked = new Promise<void>(resolve => { release = resolve })
  let count = 0
  await page.route('**/speech', async route => {
    count += 1
    if (count === 1) { await blocked; await route.fulfill({ contentType: 'audio/wav', body: wav() }).catch(() => {}); return }
    if (count === 2) { await route.fulfill({ status: 503, json: { detail: 'not ready' } }); return }
    await route.fulfill({ contentType: 'audio/wav', body: wav() })
  })
  await setup(page)
  const controls = page.getByRole('group', { name: 'Speech for answer 1' })
  await controls.getByRole('button', { name: 'listen', exact: true }).click()
  await expect(controls.getByRole('status')).toContainText('Preparing audio')
  await controls.getByRole('button', { name: 'cancel audio', exact: true }).click()
  release()
  await expect(controls.getByRole('button', { name: 'listen', exact: true })).toBeVisible()
  await controls.getByRole('button', { name: 'listen', exact: true }).click()
  await expect(controls.getByRole('alert')).toContainText('not ready')
  await controls.getByRole('button', { name: 'listen', exact: true }).click()
  await expect(controls.getByRole('button', { name: 'pause', exact: true })).toBeVisible()
})

test('starting another answer or disconnecting stops the active audio element', async ({ page }) => {
  await page.addInitScript(() => {
    const OriginalAudio = window.Audio
    const players: HTMLAudioElement[] = []
    Reflect.set(window, 'speechTestPlayers', players)
    window.Audio = function (src?: string) {
      const audio = new OriginalAudio(src); players.push(audio); return audio
    } as typeof Audio
  })
  await page.route('**/speech', route => route.fulfill({ contentType: 'audio/wav', body: wav() }))
  await setup(page)
  await page.getByRole('textbox', { name: 'Question', exact: true }).fill('第二问')
  await page.getByRole('button', { name: 'Send question' }).click()
  await expect(page.locator('.answer-content')).toHaveCount(2)
  const first = page.getByRole('group', { name: 'Speech for answer 1' })
  const second = page.getByRole('group', { name: 'Speech for answer 2' })
  await first.getByRole('button', { name: 'listen', exact: true }).click()
  await expect(first.getByRole('button', { name: 'pause', exact: true })).toBeVisible()
  await second.getByRole('button', { name: 'listen', exact: true }).click()
  await expect(second.getByRole('button', { name: 'pause', exact: true })).toBeVisible()
  expect(await page.evaluate(() => (Reflect.get(window, 'speechTestPlayers') as HTMLAudioElement[])[0].paused)).toBe(true)
  await page.getByRole('button', { name: 'disconnect', exact: true }).click()
  await expect(second.getByRole('button', { name: 'listen', exact: true })).toBeVisible()
  expect(await page.evaluate(() => (Reflect.get(window, 'speechTestPlayers') as HTMLAudioElement[])
    .every(audio => audio.paused && !audio.getAttribute('src')))).toBe(true)
})
