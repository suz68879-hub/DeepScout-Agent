// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AnalyticsPage from './index';

vi.mock('@/components/ChartPanel', () => ({ default: () => <div>chart</div> }));

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('AnalyticsPage', () => {
  it('模板查询返回结果并渲染 SQL 与图表', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      sql: 'SELECT scores_json, created_at FROM interview_report',
      explanation: '趋势',
      chart_type: 'line',
      rows: [{
        created_at: '2026-08-17',
        scores_json: '{"技术深度": 8, "项目理解": 6, "表达沟通": 8, "临场表现": 6}',
      }],
    })));
    render(<AnalyticsPage />);
    fireEvent.click(screen.getByText('近 5 次总评分趋势'));
    await waitFor(() => expect(screen.getByText(/SELECT scores_json/)).toBeTruthy());
    expect(screen.getByText('chart')).toBeTruthy();
    expect(screen.getByText('趋势')).toBeTruthy();
  });

  it('table 类型渲染表格', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      sql: 'SELECT * FROM interview_session',
      explanation: '明细',
      chart_type: 'table',
      rows: [{ id: 's1', position: 'Java后端' }],
    })));
    render(<AnalyticsPage />);
    fireEvent.click(screen.getByText('面试频次与平均分'));
    await waitFor(() => expect(screen.getByText('s1')).toBeTruthy());
  });
});
