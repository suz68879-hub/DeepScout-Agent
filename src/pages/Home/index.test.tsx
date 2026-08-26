// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Message } from '@arco-design/web-react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as restApi from '@/api/rest';
import { MAX_UPLOAD_BYTES } from '@/domain/recording/types';
import HomePage from './index';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const reportRow = {
  id: 'r1', session_id: 's1',
  scores_json: '{"技术深度": 8, "项目理解": 6, "表达沟通": 8, "临场表现": 6}',
  feedback_json: '{}', suggestions_json: '[]', md_path: '',
  created_at: '2026-08-17T10:00:00', position: 'Java后端',
};

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('HomePage', () => {
  it('加载最近报告并显示总评分', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/api/reports')) {
        return Promise.resolve(jsonResponse({ items: [reportRow], next_cursor: null }));
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    }));
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText('7.0 / 10')).toBeTruthy());
    expect(within(screen.getByRole('list')).getByText('Java后端')).toBeTruthy();
  });

  it('点击开始面试创建会话并跳转面试间', async () => {
    const startBody: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/api/reports')) return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
      if (u.includes('/api/interview/start')) {
        startBody.push(JSON.parse(String(init?.body ?? '{}')));
        return Promise.resolve(jsonResponse({ session_id: 's-1', position: 'Java后端', stage: 'intro' }));
      }
      return Promise.reject(new Error(`unexpected ${u}`));
    }));
    render(
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/interview/:sessionId" element={<div>interview-probe</div>} />
        </Routes>
      </MemoryRouter>
    );
    fireEvent.click(screen.getByRole('button', { name: '开始面试' }));
    await waitFor(() => expect(screen.getByText('interview-probe')).toBeTruthy());
    expect(startBody[0]).toEqual({ position: 'Java后端', resume_id: null });
  });

  it('报告加载失败时显示错误提示', async () => {
    const errorSpy = vi.spyOn(Message, 'error');
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const u = String(url);
      if (u.includes('/api/reports')) return Promise.reject(new Error('network down'));
      if (u.includes('/api/resume')) return Promise.resolve(jsonResponse({ detail: '尚无简历' }, 404));
      return Promise.reject(new Error(`unexpected ${u}`));
    }));
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );
    await waitFor(() => expect(errorSpy).toHaveBeenCalledTimes(1));
    expect(screen.getByText('暂无报告，完成第一场面试后在此查看')).toBeTruthy();
  });

  describe('录音分析面板', () => {
    const stubHomeFetch = (recordingResponses: Record<string, unknown>) => {
      vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
        const u = String(url);
        if (u.includes('/api/reports')) return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
        if (u.includes('/api/resume')) return Promise.resolve(jsonResponse({ detail: '尚无简历' }, 404));
        if (u.includes('/api/recording/upload')) {
          return Promise.resolve(jsonResponse({
            recording_id: 'rec-1', job_id: 'job-1', status: 'processing',
          }, 202));
        }
        for (const [frag, body] of Object.entries(recordingResponses)) {
          if (u.includes(frag)) return Promise.resolve(jsonResponse(body));
        }
        return Promise.reject(new Error(`unexpected ${u}`));
      }));
    };

    it('上传成功后轮询完成跳转报告', async () => {
      stubHomeFetch({});
      vi.spyOn(restApi, 'pollJob').mockResolvedValue({
        job_id: 'job-1', type: 'recording_analysis', status: 'succeeded', attempt: 1,
        created_at: '2026-08-26T10:00:00Z', started_at: null, finished_at: null,
        result_ref: { report_id: 'rep-1' }, error_code: null,
      });
      render(
        <MemoryRouter>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/report/:reportId" element={<div>report-probe</div>} />
          </Routes>
        </MemoryRouter>
      );
      const file = new File(['audio'], 'a.mp3', { type: 'audio/mpeg' });
      fireEvent.change(screen.getByLabelText('选择录音文件'), { target: { files: [file] } });
      fireEvent.click(screen.getByRole('button', { name: '上传并分析' }));
      await waitFor(() => expect(screen.getByRole('button', { name: '分析中' })).toBeTruthy());
      await waitFor(() => expect(screen.getByText('report-probe')).toBeTruthy());
    });

    it('轮询失败显示错误并可重试', async () => {
      stubHomeFetch({});
      vi.spyOn(restApi, 'pollJob').mockResolvedValue({
        job_id: 'job-1', type: 'recording_analysis', status: 'failed', attempt: 1,
        created_at: '2026-08-26T10:00:00Z', started_at: null, finished_at: null,
        result_ref: null, error_code: 'PROVIDER_ERROR',
      });
      render(
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      );
      const file = new File(['audio'], 'a.wav', { type: 'audio/wav' });
      fireEvent.change(screen.getByLabelText('选择录音文件'), { target: { files: [file] } });
      fireEvent.click(screen.getByRole('button', { name: '上传并分析' }));
      await waitFor(() => expect(screen.getByText(/AI 服务暂时不可用，请稍后重试/)).toBeTruthy());
      expect(screen.getByRole('button', { name: '上传并分析' })).toBeTruthy();
      expect(localStorage.getItem('deepscout:recording-job')).toBeNull();
    });

    it('刷新后从已保存的 job_id 恢复查询并跳转报告', async () => {
      localStorage.setItem('deepscout:recording-job', JSON.stringify({
        job_id: 'job-saved', recording_id: 'rec-saved',
      }));
      stubHomeFetch({});
      const poll = vi.spyOn(restApi, 'pollJob').mockResolvedValue({
        job_id: 'job-saved', type: 'recording_analysis', status: 'succeeded', attempt: 1,
        created_at: '2026-08-26T10:00:00Z', started_at: null, finished_at: null,
        result_ref: { report_id: 'rep-saved' }, error_code: null,
      });
      render(
        <MemoryRouter>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/report/:reportId" element={<div>restored-report</div>} />
          </Routes>
        </MemoryRouter>
      );
      await waitFor(() => expect(screen.getByText('restored-report')).toBeTruthy());
      expect(poll).toHaveBeenCalledWith('job-saved', expect.any(AbortSignal));
      expect(localStorage.getItem('deepscout:recording-job')).toBeNull();
    });

    it('网络查询失败时保留 job_id 并允许继续查询', async () => {
      localStorage.setItem('deepscout:recording-job', JSON.stringify({
        job_id: 'job-saved', recording_id: 'rec-saved',
      }));
      stubHomeFetch({});
      vi.spyOn(restApi, 'pollJob').mockRejectedValue(new TypeError('network down'));
      render(<MemoryRouter><HomePage /></MemoryRouter>);
      await waitFor(() => expect(screen.getByText(/任务查询中断/)).toBeTruthy());
      expect(screen.getByRole('button', { name: '继续查询' })).toBeTruthy();
      expect(localStorage.getItem('deepscout:recording-job')).not.toBeNull();
    });

    it('超过 200MB 的录音给出错误提示', async () => {
      const errorSpy = vi.spyOn(Message, 'error');
      stubHomeFetch({});
      render(
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      );
      const file = new File(['audio'], 'a.mp3', { type: 'audio/mpeg' });
      Object.defineProperty(file, 'size', { value: MAX_UPLOAD_BYTES + 1 });
      fireEvent.change(screen.getByLabelText('选择录音文件'), { target: { files: [file] } });
      fireEvent.click(screen.getByRole('button', { name: '上传并分析' }));
      await waitFor(() => expect(errorSpy).toHaveBeenCalled());
    });

    it('未选择文件点击上传给出提示', async () => {
      const warningSpy = vi.spyOn(Message, 'warning');
      stubHomeFetch({});
      render(
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      );
      fireEvent.click(screen.getByRole('button', { name: '上传并分析' }));
      await waitFor(() => expect(warningSpy).toHaveBeenCalledWith('请先选择录音文件'));
    });
  });
});
