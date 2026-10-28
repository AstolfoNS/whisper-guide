import { expect, type Page } from '@playwright/test'

/** Drive the same explicit confirmation used by a person ending an active session. */
export async function confirmSessionEnd(page: Page) {
  const confirm = page.getByRole('button', { name: '确认结束', exact: true })
  await expect(confirm).toBeVisible()
  await confirm.click()
}
