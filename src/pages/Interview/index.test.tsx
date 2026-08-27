// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Provider } from 'react-redux';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as restApi from '@/api/rest';
import store from '@/store';
import InterviewPage from './index';

// 重型 RTC 子组件在 jsdom 下 mock 掉（本测试只验证页面编排）
vi.mock('@/components/AiAvatarCard', () => ({ default: () => <div>avatar</div> }));
vi.mock('@/pages/MainPage/MainArea/Room/CameraArea', () => ({ default: () => <div>camera</div> }));
vi.mock('@/pages/MainPage/MainArea/Room/Conversation', () => ({ default: () => <div>subtitle</div> }));

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

const statePayload = {
  session_id: 's1',
  stage: 'technical',
  round_no: 3,
  current_question: null,
  scores: [{
    dimensions: {
      技术深度: { score: 8, reason: 'r1' },
      项目理解: { score: 7, reason: 'r2' },
      表达沟通: { score: 9, reason: 'r3' },
      临场表现: { score: 6, reason: 'r4' },
    },
    overall_score: 7.5,
    strengths: [],
    improvements: [],
    comment: '',
  }],
};

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('InterviewPage', () => {
  it('以专业面试工作区呈现主舞台、实时对话和设备控制', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const u = String(url);
      if (u.includes('/api/interview/state')) return Promise.resolve(jsonResponse(statePayload));
      if (u.includes('/getScenes')) {
        return Promise.resolve(jsonResponse({ Result: { scenes: [] }, ResponseMetadata: { Action: 'getScenes', RequestId: 't0' } }));
      }
      return Promise.reject(new Error(`unexpected ${u}`));
    }));
    render(
      <Provider store={store}>
        <MemoryRouter initialEntries={['/interview/s1']}>
          <Routes>
            <Route path="/interview/:sessionId" element={<InterviewPage />} />
          </Routes>
        </MemoryRouter>
      </Provider>
    );

    expect(await screen.findByRole('heading', { name: 'AI 技术面试' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '实时对话' })).toBeTruthy();
    expect(screen.getByText('面试官')).toBeTruthy();
    expect(screen.getByRole('button', { name: /麦克风/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /摄像头/ })).toBeTruthy();
  });

  it('渲染阶段指示器与最近评分浮层数据', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const u = String(url);
      if (u.includes('/api/interview/state')) return Promise.resolve(jsonResponse(statePayload));
      if (u.includes('/getScenes')) {
        // app/base 信封：resultHandler 需要 ResponseMetadata 否则抛错；空 scenes 走空守卫提前返回
        return Promise.resolve(jsonResponse({ Result: { scenes: [] }, ResponseMetadata: { Action: 'getScenes', RequestId: 't1' } }));
      }
      return Promise.reject(new Error(`unexpected ${u}`));
    }));
    render(
      <Provider store={store}>
        <MemoryRouter initialEntries={['/interview/s1']}>
          <Routes>
            <Route path="/interview/:sessionId" element={<InterviewPage />} />
          </Routes>
        </MemoryRouter>
      </Provider>
    );
    await waitFor(() => expect(screen.getByText('7.5')).toBeTruthy());
    expect(screen.getAllByText('技术面').length).toBeGreaterThan(0);
    expect(screen.getByText('结束面试')).toBeTruthy();
  });

  it('场景配置获取失败时显示错误卡，重试重新拉取场景', async () => {
    const scenesFn = vi.fn().mockRejectedValue(new Error('getScenes down'));
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const u = String(url);
      if (u.includes('/api/interview/state')) return Promise.resolve(jsonResponse(statePayload));
      if (u.includes('/getScenes')) return scenesFn();
      return Promise.reject(new Error(`unexpected ${u}`));
    }));
    render(
      <Provider store={store}>
        <MemoryRouter initialEntries={['/interview/s1']}>
          <Routes>
            <Route path="/interview/:sessionId" element={<InterviewPage />} />
          </Routes>
        </MemoryRouter>
      </Provider>
    );
    await waitFor(() => expect(screen.getByText('获取场景配置失败，请重试')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: '重试进房' }));
    await waitFor(() => expect(scenesFn).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText('获取场景配置失败，请重试')).toBeTruthy());
  });

  it('结束面试接收 202 job_id，终态后跳转报告', async () => {
    vi.spyOn(restApi, 'pollJob').mockResolvedValue({
      job_id: 'job-1', type: 'interview_finish', status: 'succeeded', attempt: 1,
      created_at: '2026-08-26T10:00:00Z', started_at: null, finished_at: null,
      result_ref: { report_id: 'report-1' }, error_code: null,
    });
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const u = String(url);
      if (u.includes('/api/interview/state')) return Promise.resolve(jsonResponse(statePayload));
      if (u.includes('/api/interview/finish')) {
        return Promise.resolve(new Response(JSON.stringify({
          job_id: 'job-1', session_id: 's1', status: 'pending',
        }), { status: 202, headers: { 'Content-Type': 'application/json' } }));
      }
      if (u.includes('/getScenes')) {
        return Promise.resolve(jsonResponse({ Result: { scenes: [] }, ResponseMetadata: { Action: 'getScenes', RequestId: 't2' } }));
      }
      return Promise.reject(new Error(`unexpected ${u}`));
    }));
    render(
      <Provider store={store}>
        <MemoryRouter initialEntries={['/interview/s1']}>
          <Routes>
            <Route path="/interview/:sessionId" element={<InterviewPage />} />
            <Route path="/report/:reportId" element={<div>finished-report</div>} />
          </Routes>
        </MemoryRouter>
      </Provider>
    );
    fireEvent.click(await screen.findByRole('button', { name: '结束面试' }));
    await waitFor(() => expect(screen.getByText('finished-report')).toBeTruthy());
    expect(restApi.pollJob).toHaveBeenCalledWith('job-1', expect.any(AbortSignal));
    expect(localStorage.getItem('deepscout:interview-job:s1')).toBeNull();
  });

  it('刷新后使用已保存的 job_id 恢复报告查询', async () => {
    localStorage.setItem('deepscout:interview-job:s1', 'job-saved');
    vi.spyOn(restApi, 'pollJob').mockResolvedValue({
      job_id: 'job-saved', type: 'interview_finish', status: 'succeeded', attempt: 1,
      created_at: '2026-08-26T10:00:00Z', started_at: null, finished_at: null,
      result_ref: { report_id: 'report-saved' }, error_code: null,
    });
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const u = String(url);
      if (u.includes('/api/interview/state')) return Promise.resolve(jsonResponse(statePayload));
      if (u.includes('/getScenes')) {
        return Promise.resolve(jsonResponse({ Result: { scenes: [] }, ResponseMetadata: { Action: 'getScenes', RequestId: 't3' } }));
      }
      return Promise.reject(new Error(`unexpected ${u}`));
    }));
    render(
      <Provider store={store}>
        <MemoryRouter initialEntries={['/interview/s1']}>
          <Routes>
            <Route path="/interview/:sessionId" element={<InterviewPage />} />
            <Route path="/report/:reportId" element={<div>restored-report</div>} />
          </Routes>
        </MemoryRouter>
      </Provider>
    );
    await waitFor(() => expect(screen.getByText('restored-report')).toBeTruthy());
    expect(restApi.pollJob).toHaveBeenCalledWith('job-saved', expect.any(AbortSignal));
  });
});
