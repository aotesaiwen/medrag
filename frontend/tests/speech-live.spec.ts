import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { test, expect } from '@playwright/test'

test('real local CosyVoice audio plays through the answer button', async ({ page }) => {
  test.skip(process.env.COURSE_RAG_SPEECH_LIVE !== '1', 'Opt in: requires the local speech worker and GPU.')
  test.setTimeout(120000)
  const root = resolve(import.meta.dirname, '../..')
  const key = execFileSync(resolve(root, '.venv/bin/python'), ['-c',
    'from rag.config import Settings; print(Settings.load().api_key)'], { cwd: root, encoding: 'utf8' }).trim()
  // Keep this check local: a synthetic answer replaces the hosted LLM call.
  await page.route('**/ask', route => route.fulfill({ json: {
    question: '语音测试', rewritten_question: '语音测试',
    answer: '个人数据保护的核心是尊重个人的自主权。收集和使用信息之前，需要明确处理目的，并采取适当的安全措施。',
    citations: [], sources: [],
  } }))
  await page.goto('/')
  await page.getByRole('button', { name: 'connect', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'connect', exact: true })
  await dialog.getByLabel('API key', { exact: true }).fill(key)
  await dialog.getByRole('button', { name: 'connect', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await page.getByRole('textbox', { name: 'Question', exact: true }).fill('语音测试')
  await page.getByRole('button', { name: 'Send question' }).click()
  const controls = page.getByRole('group', { name: 'Speech for answer 1' })
  const audioResponse = page.waitForResponse(response => response.url().endsWith('/speech'))
  await controls.getByRole('button', { name: 'listen', exact: true }).click()
  const response = await audioResponse
  expect(response.status()).toBe(200)
  expect(response.headers()['content-type']).toBe('audio/wav')
  await expect(controls.getByRole('button', { name: 'pause', exact: true })).toBeVisible({ timeout: 60000 })
  await controls.getByRole('button', { name: 'pause', exact: true }).click()
  await expect(controls.getByRole('button', { name: 'resume', exact: true })).toBeVisible()
  await controls.getByRole('button', { name: 'stop', exact: true }).click()
  expect(await page.evaluate(() => JSON.stringify({ ...sessionStorage }))).not.toContain(key)
})
