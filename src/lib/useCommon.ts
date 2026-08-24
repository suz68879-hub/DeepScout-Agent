/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useCallback, useEffect, useState, useRef } from 'react';
import { useSelector, useDispatch } from 'react-redux';
import VERTC, { MediaType } from '@volcengine/rtc';
import { Modal } from '@arco-design/web-react';
import { deviceManager } from '@/rtc/DeviceManager';
import { rtcEngine } from '@/rtc/RtcEngine';
import { agentChannel } from '@/rtc/AgentChannel';
import {
  clearCurrentMsg,
  clearHistoryMsg,
  localJoinRoom,
  localLeaveRoom,
  RTCConfig,
  SceneConfig,
  updateAIGCState,
  updateLocalUser,
  updateRTCConfig,
  updateScene,
  updateSceneConfig,
} from '@/store/slices/room';

import useRtcListeners from '@/lib/listenerHooks';
import { E2E_RTC_CONFIG, E2E_SCENE_CONFIG, E2E_SCENE_ID, E2E_USER_ID, isE2EMode } from '@/lib/e2eMock';
import { RootState } from '@/store';

import {
  updateMediaInputs,
  updateSelectedDevice,
  setDevicePermissions,
} from '@/store/slices/device';
import logger from '@/utils/logger';
import Apis from '@/app/index';

export const ABORT_VISIBILITY_CHANGE = 'abortVisibilityChange';
export interface FormProps {
  username: string;
  roomId: string;
  publishAudio: boolean;
}

export const useScene = () => {
  const { scene, sceneConfigMap } = useSelector((state: RootState) => state.room);
  return sceneConfigMap[scene] || {};
}

export const useRTC = () => {
  const { scene, rtcConfigMap } = useSelector((state: RootState) => state.room);
  return rtcConfigMap[scene] || {};
}

export const useDeviceState = () => {
  const dispatch = useDispatch();
  const room = useSelector((state: RootState) => state.room);
  const localUser = room.localUser;
  const isAudioPublished = localUser.publishAudio;
  const isVideoPublished = localUser.publishVideo;
  const isScreenPublished = localUser.publishScreen;
  const queryDevices = async (type: MediaType) => {
    const mediaDevices = await deviceManager.getDevices({
      audio: type === MediaType.AUDIO,
      video: type === MediaType.VIDEO,
    });
    if (type === MediaType.AUDIO) {
      dispatch(
        updateMediaInputs({
          audioInputs: mediaDevices.audioInputs,
        })
      );
      dispatch(
        updateSelectedDevice({
          selectedMicrophone: mediaDevices.audioInputs[0]?.deviceId,
        })
      );
    } else {
      dispatch(
        updateMediaInputs({
          videoInputs: mediaDevices.videoInputs,
        })
      );
      dispatch(
        updateSelectedDevice({
          selectedCamera: mediaDevices.videoInputs[0]?.deviceId,
        })
      );
    }
    return mediaDevices;
  };

  const switchMic = async (controlPublish = true) => {
    if (isE2EMode()) {
      // P7 E2E：跳过引擎/设备调用，只切 Redux 状态
      dispatch(updateLocalUser({ publishAudio: !isAudioPublished }));
      return;
    }
    if (controlPublish) {
      await (!isAudioPublished
        ? rtcEngine.publishStream(MediaType.AUDIO)
        : rtcEngine.unpublishStream(MediaType.AUDIO));
    }
    queryDevices(MediaType.AUDIO);
    await (!isAudioPublished
      ? deviceManager.startAudioCapture()
      : deviceManager.stopAudioCapture());
    dispatch(
      updateLocalUser({
        publishAudio: !isAudioPublished,
      })
    );
  };

  const switchCamera = async (controlPublish = true) => {
    if (isE2EMode()) {
      // P7 E2E：跳过引擎/设备调用，只切 Redux 状态
      dispatch(updateLocalUser({ publishVideo: !isVideoPublished }));
      return;
    }
    if (controlPublish) {
      await (!isVideoPublished
        ? rtcEngine.publishStream(MediaType.VIDEO)
        : rtcEngine.unpublishStream(MediaType.VIDEO));
    }
    queryDevices(MediaType.VIDEO);
    await (!isVideoPublished
      ? deviceManager.startVideoCapture()
      : deviceManager.stopVideoCapture());
    dispatch(
      updateLocalUser({
        publishVideo: !isVideoPublished,
      })
    );
  };

  const switchScreenCapture = async (controlPublish = true) => {
    try {
      !isScreenPublished
        ? sessionStorage.setItem(ABORT_VISIBILITY_CHANGE, 'true')
        : sessionStorage.removeItem(ABORT_VISIBILITY_CHANGE);
      if (controlPublish) {
        await (!isScreenPublished
          ? rtcEngine.publishScreenStream(MediaType.VIDEO)
          : rtcEngine.unpublishScreenStream(MediaType.VIDEO));
      }
      await (!isScreenPublished
        ? deviceManager.startScreenCapture()
        : deviceManager.stopScreenCapture());
      dispatch(
        updateLocalUser({
          publishScreen: !isScreenPublished,
        })
      );
    } catch {
      console.warn('Not Authorized.');
    }
    sessionStorage.removeItem(ABORT_VISIBILITY_CHANGE);
    return false;
  };

  return {
    isAudioPublished,
    isVideoPublished,
    isScreenPublished,
    switchMic,
    switchCamera,
    switchScreenCapture,
  };
};

export const useGetDevicePermission = () => {
  const [permission, setPermission] = useState<{
    audio: boolean;
  }>();

  const dispatch = useDispatch();

  useEffect(() => {
    (async () => {
      const permission = await deviceManager.checkPermission();
      dispatch(setDevicePermissions(permission));
      setPermission(permission);
    })();
  }, [dispatch]);
  return permission;
};

export const useJoin = (): [
  boolean,
  () => Promise<void | boolean>
] => {
  const devicePermissions = useSelector((state: RootState) => state.device.devicePermissions);
  const room = useSelector((state: RootState) => state.room);
  const rtcConfig = room.rtcConfigMap[room.scene];

  const dispatch = useDispatch();

  const { id } = useScene();
  const { switchMic } = useDeviceState();
  const [joining, setJoining] = useState(false);
  const listeners = useRtcListeners();

  const handleAIGCModeStart = async () => {
    if (room.isAIGCEnable) {
      await agentChannel.stopAgent(id);
      dispatch(clearCurrentMsg());
      await agentChannel.startAgent(id);
    } else {
      await agentChannel.startAgent(id);
    }
    dispatch(updateAIGCState({ isAIGCEnable: true }));
  };

  async function disPatchJoin(): Promise<boolean | undefined> {
    if (joining) {
      return;
    }

    if (isE2EMode()) {
      // P7 E2E：跳过 VERTC/引擎/设备，直接置为已进房 + 启用 AI 代理
      dispatch(
        localJoinRoom({
          roomId: E2E_RTC_CONFIG.RoomId,
          user: { username: E2E_USER_ID, userId: E2E_USER_ID },
        })
      );
      dispatch(updateAIGCState({ isAIGCEnable: true }));
      setJoining(false);
      return;
    }

    const isSupported = await VERTC.isSupported();
    if (!isSupported) {
      Modal.error({
        title: '不支持 RTC',
        content: '您的浏览器可能不支持 RTC 功能，请尝试更换浏览器或升级浏览器后再重试。',
      });
      return;
    }

    if (!rtcConfig) {
      throw new Error('RTC configuration is not initialized');
    }
    setJoining(true);
    try {
      // Redux listener uses a dynamic import to keep RTC out of the home bundle.
      // Configure synchronously here as the authoritative precondition for joining,
      // otherwise auto-join can race the listener and read an undefined basicInfo.
      rtcEngine.configure(rtcConfig);

      /** 1. Create RTC Engine */
      await rtcEngine.createEngine();

      /** 2.1 Set events callbacks */
      rtcEngine.addEventListeners(listeners);

      /** 2.2 RTC starting to join room */
      await rtcEngine.joinRoom();
      /** 3. Set users' devices info */
      const mediaDevices = await deviceManager.getDevices({
        audio: true,
        video: false,
      });

      dispatch(
        localJoinRoom({
          roomId: rtcEngine.basicInfo.room_id,
          user: {
            username: rtcEngine.basicInfo.user_id,
            userId: rtcEngine.basicInfo.user_id,
          },
        })
      );
      dispatch(
        updateSelectedDevice({
          selectedMicrophone: mediaDevices.audioInputs[0]?.deviceId,
          selectedCamera: mediaDevices.videoInputs[0]?.deviceId,
        })
      );
      dispatch(updateMediaInputs(mediaDevices));

      if (devicePermissions.audio) {
        try {
          await switchMic();
        } catch (e) {
          logger.debug('No permission for mic');
        }
      }

      handleAIGCModeStart();
    } finally {
      setJoining(false);
    }
  }

  return [joining, disPatchJoin];
};

export const useLeave = () => {
  const dispatch = useDispatch();
  const { id } = useScene();
  const idRef = useRef(id);
  idRef.current = id;

  return async function () {
    if (isE2EMode()) {
      // P7 E2E：无 RTC 资源可释放，仅复位本地状态
      dispatch(clearHistoryMsg());
      dispatch(clearCurrentMsg());
      dispatch(localLeaveRoom());
      dispatch(updateAIGCState({ isAIGCEnable: false }));
      return;
    }
    if (!rtcEngine.engine) {
      // 引擎未创建（未进房或进房失败）：无 RTC 资源可释放，仅复位本地状态
      dispatch(clearHistoryMsg());
      dispatch(clearCurrentMsg());
      dispatch(localLeaveRoom());
      dispatch(updateAIGCState({ isAIGCEnable: false }));
      return;
    }
    await Promise.all([
      deviceManager.stopAudioCapture(),
      deviceManager.stopScreenCapture(),
      deviceManager.stopVideoCapture(),
    ]);
    await agentChannel.stopAgent(idRef.current);
    await rtcEngine.leaveRoom();
    dispatch(clearHistoryMsg());
    dispatch(clearCurrentMsg());
    dispatch(localLeaveRoom());
    dispatch(updateAIGCState({ isAIGCEnable: false }));
  };
};

export const useInitScenes = (
  sessionId: string,
  onError?: (e: unknown) => void
): { reinit: () => Promise<void> } => {
  const dispatch = useDispatch();
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const reinit = useCallback(async () => {
    if (isE2EMode()) {
      // P7 E2E：不请求后端，直接注入固定场景配置
      dispatch(updateScene(E2E_SCENE_ID));
      dispatch(updateSceneConfig({ [E2E_SCENE_ID]: E2E_SCENE_CONFIG }));
      dispatch(updateRTCConfig({ [E2E_SCENE_ID]: E2E_RTC_CONFIG }));
      return;
    }
    await Apis.Basic.getScenes({ SessionId: sessionId })
      .then(({ scenes }: { scenes: { rtc: RTCConfig; scene: SceneConfig }[] }) => {
        if (!scenes.length) return;
        dispatch(updateScene(scenes[0].scene.id));
        dispatch(
          updateSceneConfig(
            scenes.reduce<Record<string, SceneConfig>>((prev, cur) => {
              prev[cur.scene.id] = cur.scene;
              return prev;
            }, {})
          )
        );
        dispatch(
          updateRTCConfig(
            scenes.reduce<Record<string, RTCConfig>>((prev, cur) => {
              prev[cur.scene.id] = cur.rtc;
              return prev;
            }, {})
          )
        );
      })
      .catch((e: unknown) => {
        logger.debug('getScenes failed');
        onErrorRef.current?.(e);
      });
  }, [dispatch, sessionId]);
  useEffect(() => {
    reinit();
  }, [reinit]);
  return { reinit };
};
