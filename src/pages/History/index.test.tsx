// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import HistoryPage from './index';

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => vi.unstubAllGlobals());

describe('HistoryPage', () => {
  it('渲染会话列表并可回看报告', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse([
      {
        id: 'r1', session_id: 's1', position: 'Java后端',
        scores_json: '{"技术深度": 8, "项目理解": 6, "表达沟通": 8, "临场表现": 6}',
        feedback_json: '{}', suggestions_json: '[]', md_path: '',
        created_at: '2026-08-17T10:00:00',
      },
    ])));
    render(
      <MemoryRouter initialEntries={['/history']}>
        <Routes>
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/report/:reportId" element={<div>report-probe</div>} />
        </Routes>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText('7.0 / 10')).toBeTruthy());
    expect(screen.getByText('Java后端')).toBeTruthy();
    fireEvent.click(screen.getByText('7.0 / 10'));
    expect(screen.getByText('report-probe')).toBeTruthy();
  });

  it('空列表显示引导文案', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse([])));
    render(
      <MemoryRouter initialEntries={['/history']}>
        <HistoryPage />
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText(/还没有面试记录/)).toBeTruthy());
  });
});
