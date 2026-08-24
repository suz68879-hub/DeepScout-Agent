// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ReportPage from './index';

vi.mock('@/components/ChartPanel', () => ({ default: () => <div>chart</div> }));

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

const reportRow = {
  id: 'r1',
  session_id: 's1',
  position: 'Java后端',
  scores_json: '{"技术深度": 8, "项目理解": 6, "表达沟通": 8, "临场表现": 6}',
  feedback_json: JSON.stringify({
    summary: '总体不错',
    round_scores: [7, 7.5],
    round_details: [{ round_no: 1, question: '自我介绍', answer_summary: '要点', comment: '不错' }],
    strengths: [],
    improvements: ['改进1'],
  }),
  suggestions_json: '["多练技术题"]',
  md_path: '',
  created_at: '2026-08-17T10:00:00',
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ReportPage', () => {
  it('加载报告并渲染总评分与逐题记录', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(reportRow)));
    render(
      <MemoryRouter initialEntries={['/report/r1']}>
        <Routes>
          <Route path="/report/:reportId" element={<ReportPage />} />
        </Routes>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText('7.0')).toBeTruthy());
    expect(screen.getByText('总体不错')).toBeTruthy();
    expect(screen.getByText('自我介绍')).toBeTruthy();
    expect(screen.getByText('改进1')).toBeTruthy();
  });

  it('导出 MD 触发下载', async () => {
    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/export.md')) {
        return Promise.resolve(new Response('# 报告', { status: 200 }));
      }
      return Promise.resolve(jsonResponse(reportRow));
    }));
    let clicked = false;
    const origCreate = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = origCreate(tag);
      if (tag === 'a') {
        vi.spyOn(el, 'click').mockImplementation(() => {
          clicked = true;
        });
      }
      return el;
    });
    render(
      <MemoryRouter initialEntries={['/report/r1']}>
        <Routes>
          <Route path="/report/:reportId" element={<ReportPage />} />
        </Routes>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText('导出 MD')).toBeTruthy());
    fireEvent.click(screen.getByText('导出 MD'));
    await waitFor(() => expect(clicked).toBe(true));
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalled();
  });

  const recordingReportRow = {
    ...reportRow,
    id: 'r2',
    source: 'recording',
    feedback_json: JSON.stringify({
      summary: '录音表现良好',
      round_scores: [],
      round_details: [{ round_no: 1, question: '自我介绍', answer_summary: '要点', comment: '清晰' }],
      strengths: ['s1', 's2'],
      improvements: ['改进1'],
      transcript: [
        { speaker: '0', role: '面试官', start_ms: 0, end_ms: 1000, text: '请做自我介绍' },
        { speaker: '1', role: '候选人', start_ms: 1000, end_ms: 5000, text: '我有三年后端经验' },
      ],
      speaker_assignment: { candidate_speaker: '1', confidence: '高', reason: '' },
    }),
  };

  it('录音报告渲染转写全文折叠区', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(recordingReportRow)));
    render(
      <MemoryRouter initialEntries={['/report/r2']}>
        <Routes>
          <Route path="/report/:reportId" element={<ReportPage />} />
        </Routes>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText('转写全文（2 段）')).toBeTruthy());
    expect(screen.getByText('[候选人]')).toBeTruthy();
    expect(screen.getByText('我有三年后端经验')).toBeTruthy();
  });

  it('无转写的会话报告不渲染转写区', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(reportRow)));
    render(
      <MemoryRouter initialEntries={['/report/r1']}>
        <Routes>
          <Route path="/report/:reportId" element={<ReportPage />} />
        </Routes>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText('总体不错')).toBeTruthy());
    expect(screen.queryByText(/转写全文/)).toBeNull();
  });
});
