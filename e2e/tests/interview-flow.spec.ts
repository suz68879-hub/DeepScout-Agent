import { expect, test } from '@playwright/test';

import { E2E_REPORT_ID, E2E_SESSION_ID, installMockRoutes } from '../mocks/mockBackend';

test('面试间：字幕驱动 → 结束面试 → 报告页', async ({ page }) => {
  await installMockRoutes(page);

  await page.goto(`/interview/${E2E_SESSION_ID}`);

  await expect.poll(() => page.evaluate(() => getComputedStyle(document.body).overflowY)).not.toBe('hidden');

  // E2E 模式确定性注入完整面试字幕
  const transcript = page.getByRole('region', { name: '实时对话' });
  await expect(transcript).toContainText('请先做一下自我介绍');
  await expect(transcript).toContainText(/库存是怎么扣的/);

  // E2E 占位自视窗存在（无 RTC 引擎）
  await expect(page.getByTestId('e2e-selfview')).toBeVisible();

  // 结束面试 → 跳转报告页（mock finish 返回 e2e-report）
  await page.getByRole('button', { name: '结束面试' }).click();
  await page.waitForURL(`**/report/${E2E_REPORT_ID}`);

  // 报告页：总结与维度分可见
  await expect(page.getByText(/E2E 测试报告/)).toBeVisible();
  await expect(page.getByText('技术深度')).toBeVisible();
});
