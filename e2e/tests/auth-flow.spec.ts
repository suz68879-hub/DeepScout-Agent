import { expect, test } from '@playwright/test';
import { installMockRoutes } from '../mocks/mockBackend';

test('未登录用户登录后进入首页并可退出', async ({ page }) => {
  installMockRoutes(page, { authenticated: false });
  await page.goto('/');
  await page.waitForURL('**/login');
  await page.getByLabel('用户名').fill('e2e_user');
  await page.getByLabel('密码').fill('password-123');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL(/\/$/);
  await expect(page.getByText('e2e_user')).toBeVisible();
  await page.getByRole('button', { name: '退出' }).click();
  await page.waitForURL('**/login');
});
