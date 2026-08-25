// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Provider } from 'react-redux';
import { afterEach, describe, expect, it, vi } from 'vitest';
import store from '@/store';
import App from './App';

// 面试间页在 jsdom 下 mock RTC 链路与重型子组件（App 冒烟测试只验证路由可达）
vi.mock('@/components/AiAvatarCard', () => ({ default: () => <div>avatar</div> }));
vi.mock('@/pages/Interview', () => ({ default: () => <button>结束面试</button> }));
vi.mock('@/pages/MainPage/MainArea/Room/CameraArea', () => ({ default: () => <div>camera</div> }));
vi.mock('@/pages/MainPage/MainArea/Room/Conversation', () => ({ default: () => <div>subtitle</div> }));
vi.mock('@/lib/useCommon', () => ({
  useJoin: () => [false, () => Promise.resolve()],
  useLeave: () => () => Promise.resolve(),
  useRTC: () => ({}),
  useDeviceState: () => ({
    isAudioPublished: false,
    isVideoPublished: false,
    isScreenPublished: false,
    switchMic: () => undefined,
    switchCamera: () => undefined,
    switchScreenCapture: () => undefined,
  }),
  useInitScenes: () => ({ reinit: vi.fn() }),
}));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('App 路由骨架', () => {
  it('首页渲染并可导航到 History', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/api/auth/me')) return Promise.resolve(jsonResponse({ id: 'u1', username: 'alice' }));
      if (String(url).includes('/api/resume')) return Promise.resolve(jsonResponse({ detail: '尚无简历' }, 404));
      if (String(url).includes('/api/reports')) return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
      return Promise.reject(new Error(`unexpected ${url}`));
    }));
    render(<App />);
    expect(await screen.findByRole('button', { name: '开始面试' })).toBeTruthy();
    fireEvent.click(screen.getByText('历史记录'));
    expect(await screen.findByText('面试历史')).toBeTruthy();
  });

  it('面试间与报告路由可达', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/api/auth/me')) return Promise.resolve(jsonResponse({ id: 'u1', username: 'alice' }));
      if (String(url).includes('/api/interview/state')) {
        return Promise.resolve(jsonResponse({ session_id: 's1', stage: 'intro', round_no: 0, current_question: null, scores: [] }));
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    }));
    window.history.pushState({}, '', '/interview/s1');
    render(
      <Provider store={store}>
        <App />
      </Provider>
    );
    expect(await screen.findByText('结束面试')).toBeTruthy();
  });
});
