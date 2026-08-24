import { beforeEach, describe, expect, it, vi } from 'vitest';

import store from '@/store';
import { updateRTCConfig, updateScene } from '@/store/slices/room';
import { rtcEngine } from '@/rtc/RtcEngine';

// 防御性 mock：store 加载链若经由 room.ts → handler.ts → AgentChannel，会触碰浏览器 SDK；
// node 测试环境下统一隔离，避免真实模块副作用
// （room.ts initialState 在模块作用域使用 NetworkQuality.UNKNOWN，需在 mock 中提供）
vi.mock('@volcengine/rtc', () => ({
  NetworkQuality: { UNKNOWN: 0 },
}));
vi.mock('@volcengine/rtc/extension-ainr', () => ({ default: class {} }));

vi.mock('@/rtc/RtcEngine', () => ({
  rtcEngine: {
    basicInfo: {},
    configure(config: Record<string, string>) {
      this.basicInfo = {
        app_id: config.AppId,
        room_id: config.RoomId,
        user_id: config.UserId,
        session_id: config.SessionId,
        token: config.Token,
      };
    },
  },
}));

const config = {
  AppId: 'app-1',
  RoomId: 'room-1',
  UserId: 'user-1',
  Token: 'token-1',
  SessionId: 'session-1',
};

describe('rtcConfig listener middleware', () => {
  beforeEach(() => {
    (rtcEngine.basicInfo as unknown as Record<string, string>) = {};
  });

  it('updateRTCConfig 派发后由 middleware 写入 basicInfo', async () => {
    store.dispatch(updateScene('Custom'));
    store.dispatch(updateRTCConfig({ Custom: config }));

    await vi.waitFor(() => {
      expect(rtcEngine.basicInfo).toEqual({
        app_id: 'app-1',
        room_id: 'room-1',
        user_id: 'user-1',
        session_id: 'session-1',
        token: 'token-1',
      });
    });
  });

  it('再次派发时 basicInfo 反映最新配置', async () => {
    store.dispatch(updateScene('Custom'));
    store.dispatch(updateRTCConfig({ Custom: { ...config, AppId: 'app-2', Token: 'token-2' } }));

    await vi.waitFor(() => {
      expect(rtcEngine.basicInfo).toEqual({
        app_id: 'app-2',
        room_id: 'room-1',
        user_id: 'user-1',
        session_id: 'session-1',
        token: 'token-2',
      });
    });
  });
});
