import { expect, test } from '@playwright/test';

import { E2E_SESSION_ID } from '../mocks/mockBackend';
import { installMockRoutes } from '../mocks/mockBackend';

test('首页渲染 → 开始面试 → 进入面试间', async ({ page }) => {
  await installMockRoutes(page);

  await page.goto('/');

  // 首页 hero 与岗位预设可见
  await expect(page.getByText('智能面试，成就未来')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Java后端' })).toBeVisible();

  // 点开始面试 → 跳转面试间（mock start 返回 e2e-session）
  await page.getByRole('button', { name: '开始面试' }).click();
  await page.waitForURL(`**/interview/${E2E_SESSION_ID}`);
});
