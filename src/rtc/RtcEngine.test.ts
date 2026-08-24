import { beforeEach, describe, expect, it, vi } from 'vitest';

import { rtcEngine } from './RtcEngine';

const mocks = vi.hoisted(() => {
  const engine = {
    on: vi.fn(),
    joinRoom: vi.fn(),
    leaveRoom: vi.fn(() => ({ catch: vi.fn() })),
    registerExtension: vi.fn(),
  };
  return {
    engine,
    createEngine: vi.fn(() => engine),
    destroyEngine: vi.fn(),
  };
});

vi.mock('@volcengine/rtc', () => ({
  default: {
    createEngine: mocks.createEngine,
    destroyEngine: mocks.destroyEngine,
    events: {
      onError: 'onError',
      onUserJoined: 'onUserJoined',
      onUserLeave: 'onUserLeave',
      onTrackEnded: 'onTrackEnded',
      onUserPublishStream: 'onUserPublishStream',
      onUserUnpublishStream: 'onUserUnpublishStream',
      onRemoteStreamStats: 'onRemoteStreamStats',
      onLocalStreamStats: 'onLocalStreamStats',
      onAudioDeviceStateChanged: 'onAudioDeviceStateChanged',
      onLocalAudioPropertiesReport: 'onLocalAudioPropertiesReport',
      onRemoteAudioPropertiesReport: 'onRemoteAudioPropertiesReport',
      onAutoplayFailed: 'onAutoplayFailed',
      onPlayerEvent: 'onPlayerEvent',
      onRoomBinaryMessageReceived: 'onRoomBinaryMessageReceived',
      onNetworkQuality: 'onNetworkQuality',
    },
  },
  RoomProfileType: { chat: 0 },
  MediaType: { AUDIO: 0, VIDEO: 1, AUDIO_AND_VIDEO: 2 },
  VideoRenderMode: { RENDER_MODE_FILL: 0, RENDER_MODE_HIDDEN: 1 },
  MirrorType: { MIRROR_TYPE_RENDER: 0 },
  StreamIndex: { STREAM_INDEX_MAIN: 0, STREAM_INDEX_SCREEN: 1 },
}));

vi.mock('@volcengine/rtc/extension-ainr', () => ({
  default: class AIAnsExtension {
    enable = vi.fn();
  },
}));

describe('RtcEngine', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    rtcEngine.basicInfo = {
      app_id: 'app-1',
      room_id: 'room-1',
      user_id: 'user-1',
      session_id: 'session-1',
      token: 'token-1',
    };
  });

  it('createEngine 创建引擎并注册 AI 降噪扩展', async () => {
    await rtcEngine.createEngine();
    expect(mocks.createEngine).toHaveBeenCalledWith('app-1');
    expect(mocks.engine.registerExtension).toHaveBeenCalled();
  });

  it('configure 在创建引擎前同步映射场景参数', () => {
    rtcEngine.configure({
      AppId: 'app-2',
      RoomId: 'room-2',
      UserId: 'user-2',
      SessionId: 'session-2',
      Token: 'token-2',
    });
    expect(rtcEngine.basicInfo).toEqual({
      app_id: 'app-2',
      room_id: 'room-2',
      user_id: 'user-2',
      session_id: 'session-2',
      token: 'token-2',
    });
  });

  it('addEventListeners 注册全部 15 个 RTC 事件', () => {
    const listeners = {
      handleError: vi.fn(),
      handleUserJoin: vi.fn(),
      handleUserLeave: vi.fn(),
      handleTrackEnded: vi.fn(),
      handleUserPublishStream: vi.fn(),
      handleUserUnpublishStream: vi.fn(),
      handleRemoteStreamStats: vi.fn(),
      handleLocalStreamStats: vi.fn(),
      handleLocalAudioPropertiesReport: vi.fn(),
      handleRemoteAudioPropertiesReport: vi.fn(),
      handleAudioDeviceStateChanged: vi.fn(),
      handleAutoPlayFail: vi.fn(),
      handlePlayerEvent: vi.fn(),
      handleRoomBinaryMessageReceived: vi.fn(),
      handleNetworkQuality: vi.fn(),
    };
    rtcEngine.addEventListeners(listeners);
    expect(mocks.engine.on).toHaveBeenCalledTimes(15);
    expect(mocks.engine.on).toHaveBeenCalledWith('onError', listeners.handleError);
    expect(mocks.engine.on).toHaveBeenCalledWith(
      'onRoomBinaryMessageReceived',
      listeners.handleRoomBinaryMessageReceived
    );
  });

  it('joinRoom 按官方调用序列入房', () => {
    rtcEngine.joinRoom();
    expect(mocks.engine.joinRoom).toHaveBeenCalledWith(
      'token-1',
      'room-1',
      {
        userId: 'user-1',
        extraInfo: JSON.stringify({
          call_scene: 'RTC-AIGC',
          user_name: 'user-1',
          user_id: 'user-1',
        }),
      },
      {
        isAutoPublish: true,
        isAutoSubscribeAudio: true,
        roomProfileType: 0,
      }
    );
  });

  it('leaveRoom 销毁引擎', () => {
    rtcEngine.leaveRoom();
    expect(mocks.engine.leaveRoom).toHaveBeenCalled();
    expect(mocks.destroyEngine).toHaveBeenCalledWith(mocks.engine);
  });
});
