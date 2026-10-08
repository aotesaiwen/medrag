import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { test, expect } from '@playwright/test'

test('live backend answers a question and a quoted follow-up, and serves an authenticated PDF', async ({ page }) => {
  test.skip(process.env.COURSE_RAG_LIVE !== '1', 'Opt in explicitly: this test calls the configured DeepSeek service.')
  test.setTimeout(600000)
  const root = resolve(import.meta.dirname, '../..')
  // Capture the key privately in memory; never write it into a report or browser storage.
  const key = execFileSync(resolve(root, '.venv/bin/python'), ['-c',
    'from rag.config import Settings; print(Settings.load().api_key)'], { cwd: root, encoding: 'utf8' }).trim()
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await page.getByRole('button', { name: 'connect', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'connect', exact: true })
  await dialog.getByLabel('API key', { exact: true }).fill(key)
  await dialog.getByRole('button', { name: 'connect', exact: true }).click()
  await expect(dialog).not.toBeVisible()

  const question = 'GDPR 第17条第1款规定，个人在什么情况下有权要求删除其个人数据？'
  await page.getByRole('textbox', { name: 'Question', exact: true }).fill(question)
  const firstResponse = page.waitForResponse(response => response.url().endsWith('/ask') && response.request().method() === 'POST', { timeout: 300000 })
  await page.getByRole('button', { name: 'Send question', exact: true }).click()
  const firstHttp = await firstResponse
  expect(firstHttp.status()).toBe(200)
  const first = await firstHttp.json()
  expect(first.citations.length).toBeGreaterThan(0)
  await expect(page.locator('.turn').first().locator('.answer-body')).toContainText('删除', { timeout: 300000 })
  await page.getByRole('button', { name: 'Open citation 1, answer 1', exact: true }).first().click()
  await expect(page.locator('.source-header h2')).toHaveText(first.citations[0].label)

  const frame = page.frameLocator('iframe[title="Source passage"]')
  await frame.locator('.passage p').first().evaluate(element => {
    const range = document.createRange(); range.selectNodeContents(element)
    const selection = document.getSelection()!; selection.removeAllRanges(); selection.addRange(range)
    element.dispatchEvent(new KeyboardEvent('keyup', { key: 'Shift', bubbles: true }))
  })
  const quoteComposer = frame.getByRole('complementary', { name: 'Ask about this passage' })
  await quoteComposer.getByRole('textbox', { name: 'Question', exact: true }).fill('这项权利有哪些例外？')
  const nextResponse = page.waitForResponse(response => response.url().endsWith('/ask') && response.request().method() === 'POST', { timeout: 300000 })
  await quoteComposer.getByRole('button', { name: 'Send question', exact: true }).click()
  const secondHttp = await nextResponse
  expect(secondHttp.status()).toBe(200)
  expect(secondHttp.request().postDataJSON().history).toHaveLength(1)
  expect(secondHttp.request().postDataJSON().question).toContain(first.citations[0].chunk_id)
  const second = await secondHttp.json()
  expect(second.rewritten_question).toMatch(/GDPR|17|删除/)
  await expect(page.locator('.turn').nth(1).locator('.answer-body')).toContainText('例外')
  expect(second.citations[0].number).toBe(1)
  await page.getByRole('button', { name: 'Open citation 1, answer 1', exact: true }).first().click()
  await expect(page.locator('.source-header h2')).toHaveText(first.citations[0].label)

  // Exercise the actual authenticated file handler independently of which sources the LLM cites.
  const fileResult = await page.evaluate(async key => {
    const response = await fetch('/slides/BME2133_Fall2025_Lecture8.pdf', { headers: { Authorization: `Bearer ${key}` } })
    return { status: response.status, type: response.headers.get('content-type'), size: (await response.blob()).size }
  }, key)
  expect(fileResult.status).toBe(200)
  expect(fileResult.type).toBe('application/pdf')
  expect(fileResult.size).toBeGreaterThan(1000)
  expect(await page.evaluate(() => JSON.stringify({ ...sessionStorage }))).not.toContain(key)
  expect(errors).toEqual([])
})
