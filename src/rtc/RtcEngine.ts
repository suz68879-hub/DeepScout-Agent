/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 *
 * RTC 引擎生命周期 / 流管理 / 视频渲染（原官方单例拆分产物，协议级代码逐行移植）
 */

import VERTC, {
  AutoPlayFailedEvent,
  DeviceInfo,
  IRTCEngine,
  LocalAudioPropertiesInfo,
  LocalStreamStats,
  MediaType,
  MirrorType,
  NetworkQuality,
  onUserJoinedEvent,
  onUserLeaveEvent,
  PlayerEvent,
  RemoteAudioPropertiesInfo,
  RemoteStreamStats,
  RoomProfileType,
  ScreenEncoderConfig,
  StreamIndex,
  StreamRemoveReason,
  VideoRenderMode,
} from '@volcengine/rtc';
import RTCAIAnsExtension from '@volcengine/rtc/extension-ainr';

export interface IEventListener {
  handleError: (e: { errorCode: any }) => void;
  handleUserJoin: (e: onUserJoinedEvent) => void;
  handleUserLeave: (e: onUserLeaveEvent) => void;
  handleTrackEnded: (e: { kind: string; isScreen: boolean }) => void;
  handleUserPublishStream: (e: { userId: string; mediaType: MediaType }) => void;
  handleUserUnpublishStream: (e: {
    userId: string;
    mediaType: MediaType;
    reason: StreamRemoveReason;
  }) => void;
  handleRemoteStreamStats: (e: RemoteStreamStats) => void;
  handleLocalStreamStats: (e: LocalStreamStats) => void;
  handleLocalAudioPropertiesReport: (e: LocalAudioPropertiesInfo[]) => void;
  handleRemoteAudioPropertiesReport: (e: RemoteAudioPropertiesInfo[]) => void;
  handleAudioDeviceStateChanged: (e: DeviceInfo) => void;
  handleAutoPlayFail: (e: AutoPlayFailedEvent) => void;
  handlePlayerEvent: (e: PlayerEvent) => void;
  handleRoomBinaryMessageReceived: (e: { userId: string; message: ArrayBuffer }) => void;
  handleNetworkQuality: (
    uplinkNetworkQuality: NetworkQuality,
    downlinkNetworkQuality: NetworkQuality
  ) => void;
}

export interface BasicBody {
  app_id: string;
  room_id: string;
  user_id: string;
  session_id: string;
  token?: string;
}

interface RTCSceneConfig {
  AppId: string;
  RoomId: string;
  UserId: string;
  SessionId: string;
  Token?: string;
}

export class RtcEngine {
  engine!: IRTCEngine;

  basicInfo!: BasicBody;

  configure = (config: RTCSceneConfig): void => {
    this.basicInfo = {
      app_id: config.AppId,
      room_id: config.RoomId,
      user_id: config.UserId,
      session_id: config.SessionId,
      token: config.Token,
    };
  };

  createEngine = async (): Promise<void> => {
    if (!this.basicInfo?.app_id) {
      throw new Error('RTC configuration is not initialized');
    }
    this.engine = VERTC.createEngine(this.basicInfo.app_id);
    try {
      const AIAnsExtension = new RTCAIAnsExtension();
      await this.engine.registerExtension(AIAnsExtension);
      AIAnsExtension.enable();
    } catch (error) {
      console.warn(
        `当前环境不支持 AI 降噪, 此错误可忽略, 不影响实际使用, e: ${(error as any).message}`
      );
    }
  };

  addEventListeners = ({
    handleError,
    handleUserJoin,
    handleUserLeave,
    handleTrackEnded,
    handleUserPublishStream,
    handleUserUnpublishStream,
    handleRemoteStreamStats,
    handleLocalStreamStats,
    handleLocalAudioPropertiesReport,
    handleRemoteAudioPropertiesReport,
    handleAudioDeviceStateChanged,
    handleAutoPlayFail,
    handlePlayerEvent,
    handleRoomBinaryMessageReceived,
    handleNetworkQuality,
  }: IEventListener): void => {
    this.engine.on(VERTC.events.onError, handleError);
    this.engine.on(VERTC.events.onUserJoined, handleUserJoin);
    this.engine.on(VERTC.events.onUserLeave, handleUserLeave);
    this.engine.on(VERTC.events.onTrackEnded, handleTrackEnded);
    this.engine.on(VERTC.events.onUserPublishStream, handleUserPublishStream);
    this.engine.on(VERTC.events.onUserUnpublishStream, handleUserUnpublishStream);
    this.engine.on(VERTC.events.onRemoteStreamStats, handleRemoteStreamStats);
    this.engine.on(VERTC.events.onLocalStreamStats, handleLocalStreamStats);
    this.engine.on(VERTC.events.onAudioDeviceStateChanged, handleAudioDeviceStateChanged);
    this.engine.on(VERTC.events.onLocalAudioPropertiesReport, handleLocalAudioPropertiesReport);
    this.engine.on(VERTC.events.onRemoteAudioPropertiesReport, handleRemoteAudioPropertiesReport);
    this.engine.on(VERTC.events.onAutoplayFailed, handleAutoPlayFail);
    this.engine.on(VERTC.events.onPlayerEvent, handlePlayerEvent);
    this.engine.on(VERTC.events.onRoomBinaryMessageReceived, handleRoomBinaryMessageReceived);
    this.engine.on(VERTC.events.onNetworkQuality, handleNetworkQuality);
  };

  joinRoom = () => {
    console.log(' ------ userJoinRoom\n', `roomId: ${this.basicInfo.room_id}\n`, `uid: ${this.basicInfo.user_id}`);
    return this.engine.joinRoom(
      this.basicInfo.token!,
      `${this.basicInfo.room_id!}`,
      {
        userId: this.basicInfo.user_id!,
        extraInfo: JSON.stringify({
          call_scene: 'RTC-AIGC',
          user_name: this.basicInfo.user_id,
          user_id: this.basicInfo.user_id,
        }),
      },
      {
        isAutoPublish: true,
        isAutoSubscribeAudio: true,
        roomProfileType: RoomProfileType.chat,
      }
    );
  };

  leaveRoom = (): void => {
    // 拆分说明：官方 leaveRoom 同时重置 audioBotEnabled 与 _audioCaptureDevice。
    // 前者已归属 AgentChannel（useLeave 挂断前必先 stopAgent，状态由其复位）；
    // 后者已归属 DeviceManager（每次进房都会重新 getDevices 枚举覆盖）。
    // 行为差异经评估无影响，属职责分离的刻意调整。
    this.engine.leaveRoom().catch();
    VERTC.destroyEngine(this.engine);
  };

  publishStream = (mediaType: MediaType): void => {
    this.engine.publishStream(mediaType);
  };

  unpublishStream = (mediaType: MediaType): void => {
    this.engine.unpublishStream(mediaType);
  };

  publishScreenStream = async (mediaType: MediaType): Promise<void> => {
    await this.engine.publishScreen(mediaType);
  };

  unpublishScreenStream = async (mediaType: MediaType): Promise<void> => {
    await this.engine.unpublishScreen(mediaType);
  };

  setScreenEncoderConfig = async (description: ScreenEncoderConfig): Promise<void> => {
    await this.engine.setScreenEncoderConfig(description);
  };

  /**
   * @brief 设置业务标识参数
   * @param businessId
   */
  setBusinessId = (businessId: string): void => {
    this.engine.setBusinessId(businessId);
  };

  setLocalVideoMirrorType = (type: MirrorType): void => {
    this.engine.setLocalVideoMirrorType(type);
  };

  setLocalVideoPlayer = (
    userId: string,
    renderDom?: string | HTMLElement,
    isScreenShare = false,
    renderMode = VideoRenderMode.RENDER_MODE_FILL
  ): void => {
    this.engine.setLocalVideoPlayer(
      isScreenShare ? StreamIndex.STREAM_INDEX_SCREEN : StreamIndex.STREAM_INDEX_MAIN,
      {
        renderDom,
        userId,
        renderMode,
      }
    );
  };

  setRemoteVideoPlayer = (
    userId: string,
    renderDom?: string | HTMLElement,
    renderMode = VideoRenderMode.RENDER_MODE_HIDDEN
  ): void => {
    this.engine.setRemoteVideoPlayer(StreamIndex.STREAM_INDEX_MAIN, {
      renderDom,
      userId,
      renderMode,
    });
  };

  /**
   * @brief 移除播放器
   */
  removeLocalVideoPlayer = (userId: string, scope: StreamIndex | 'Both' = 'Both'): void => {
    let removeScreen = scope === StreamIndex.STREAM_INDEX_SCREEN;
    let removeCamera = scope === StreamIndex.STREAM_INDEX_MAIN;
    if (scope === 'Both') {
      removeCamera = true;
      removeScreen = true;
    }
    if (removeScreen) {
      this.engine.setLocalVideoPlayer(StreamIndex.STREAM_INDEX_SCREEN, { userId });
    }
    if (removeCamera) {
      this.engine.setLocalVideoPlayer(StreamIndex.STREAM_INDEX_MAIN, { userId });
    }
  };
}

export const rtcEngine = new RtcEngine();
