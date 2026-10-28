import { confirmSessionEnd } from './sessionActions'
import { test, expect } from '@playwright/test'
import { useSyntheticMicrophone } from './syntheticMicrophone'

test('confirms script deletion and protects the bound document until its session ends', async ({ page }, testInfo) => {
  // This test only mutates the isolated Playwright server and its temporary database.
  expect(testInfo.project.use.baseURL).toBe('http://127.0.0.1:5174')
  await useSyntheticMicrophone(page)
  expect((await page.request.post('/api/settings/model-configs/local-default/activate')).ok()).toBe(true)
  await page.goto('/studio')

  const library = page.locator('.library-column .library-panel')
  const suffix = Date.now().toString(36)
  const firstName = 'e2e-delete-' + suffix + '-取消确认.txt'
  const aName = 'e2e-delete-' + suffix + '-会话保护A.txt'
  const bName = 'e2e-delete-' + suffix + '-其他讲稿B.txt'
  const firstContent = '删除取消与确认的正文。'
  const aContent = '大家好。删除保护验证。'
  const uploadedIds: string[] = []
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))

  const row = (name: string) => library.locator('.script-row').filter({
    has: page.getByText(name, { exact: true }),
  })
  const deleteButton = (name: string) => row(name).getByRole('button', {
    name: '删除讲稿“' + name + '”', exact: true,
  })
  const scriptUrl = (id: string) => '/api/scripts/' + encodeURIComponent(id)
  async function upload(name: string, content: string) {
    const uploaded = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/scripts/upload'
      && response.request().method() === 'POST')
    await library.getByLabel('上传讲稿', { exact: false }).setInputFiles({
      name, mimeType: 'text/plain', buffer: Buffer.from(content),
    })
    const response = await uploaded
    expect(response.status()).toBe(201)
    const documents = await response.json() as { id: string; name: string }[]
    const document = documents.find(item => item.name === name)
    expect(document).toBeDefined()
    uploadedIds.push(document!.id)
    await expect(row(name)).toBeVisible()
    return document!.id
  }
  async function openConfirmation(name: string, keyboard = false) {
    const button = deleteButton(name)
    await expect(button).toBeEnabled()
    if (keyboard) {
      await button.focus()
      await page.keyboard.press('Enter')
    } else {
      await button.click()
    }
    await expect(page.getByText('确定删除讲稿“' + name + '”？此操作不可撤销。', { exact: true })).toBeVisible()
  }
  async function confirmDelete(name: string) {
    await openConfirmation(name)
    await page.getByRole('button', { name: '删除讲稿', exact: true }).click()
    await expect(row(name)).toHaveCount(0)
  }

  try {
    const firstId = await upload(firstName, firstContent)
    await row(firstName).getByRole('radio').check()
    await expect(library.locator('.preview pre')).toHaveText(firstContent)
    await openConfirmation(firstName, true)
    await page.getByRole('button', { name: '取消', exact: true }).click()
    await expect(page.getByText('确定删除讲稿“' + firstName + '”？此操作不可撤销。', { exact: true })).toBeHidden()
    await expect(row(firstName).getByRole('radio')).toBeChecked()
    await expect(library.locator('.preview pre')).toHaveText(firstContent)
    expect((await page.request.get(scriptUrl(firstId))).status()).toBe(200)

    await confirmDelete(firstName)
    await expect(library.getByRole('radio', { checked: true })).toHaveCount(0)
    await expect(library.locator('.preview')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '开始演讲', exact: true })).toBeDisabled()
    expect((await page.request.get(scriptUrl(firstId))).status()).toBe(404)

    const aId = await upload(aName, aContent)
    const bId = await upload(bName, '这是可以独立删除的另一份讲稿。')
    await row(aName).getByRole('radio').check()
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('30000')
    await expect(page.getByRole('button', { name: '开始演讲', exact: true })).toBeEnabled()
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect(page.getByRole('button', { name: '暂停演讲', exact: true })).toBeVisible()
    await expect(page.locator('.reading-cursor')).toHaveText('好')
    await expect(deleteButton(aName)).toBeDisabled()
    await expect(row(aName)).toContainText('讲稿正在会话中使用，请结束后再删除')
    const inUse = await page.request.delete(scriptUrl(aId))
    expect(inUse.status()).toBe(409)
    expect((await inUse.json()).detail.code).toBe('SCRIPT_IN_USE')
    expect((await page.request.get(scriptUrl(aId))).status()).toBe(200)

    await confirmDelete(bName)
    expect((await page.request.get(scriptUrl(bId))).status()).toBe(404)
    await expect(row(aName).getByRole('radio')).toBeChecked()
    await expect(library.locator('.preview pre')).toHaveText(aContent)
    await expect(deleteButton(aName)).toBeDisabled()

    await page.getByRole('button', { name: '暂停演讲', exact: true }).click()
    await expect(page.getByRole('button', { name: '继续演讲', exact: true })).toBeVisible()
    await expect(deleteButton(aName)).toBeDisabled()
    const paused = await page.request.delete(scriptUrl(aId))
    expect(paused.status()).toBe(409)
    expect((await paused.json()).detail.code).toBe('SCRIPT_IN_USE')

    await page.getByRole('button', { name: '结束', exact: true }).click()
  await confirmSessionEnd(page)
    await expect(page.getByText('已结束', { exact: true })).toBeVisible()
    await expect(deleteButton(aName)).toBeEnabled()
    await confirmDelete(aName)
    expect((await page.request.get(scriptUrl(aId))).status()).toBe(404)
    await expect(library.getByRole('radio', { checked: true })).toHaveCount(0)
    await expect(library.locator('.preview')).toHaveCount(0)
    expect(errors).toEqual([])
  } finally {
    if (!page.isClosed()) {
      const end = page.getByRole('button', { name: '结束', exact: true })
      if (await end.isVisible() && await end.isEnabled()) {
        await end.click()
        await expect(page.getByText('已结束', { exact: true })).toBeVisible()
      }
      for (const id of uploadedIds) await page.request.delete(scriptUrl(id))
    }
  }
})
