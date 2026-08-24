/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 *
 * 设备枚举/采集控制/热切换（原官方单例拆分产物，协议级代码逐行移植）
 */

import VERTC, { AudioProfileType, MediaType, MirrorType, StreamIndex } from '@volcengine/rtc';
import { Message } from '@arco-design/web-react';
import { rtcEngine } from './RtcEngine';

export class DeviceManager {
  private _audioCaptureDevice?: string;

  private _videoCaptureDevice?: string;

  checkPermission = (): Promise<{
    video: boolean;
    audio: boolean;
  }> => {
    return VERTC.enableDevices({
      video: false,
      audio: true,
    });
  };

  /**
   * @brief get the devices
   */
  getDevices = async (props?: {
    video?: boolean;
    audio?: boolean;
  }): Promise<{
    audioInputs: MediaDeviceInfo[];
    audioOutputs: MediaDeviceInfo[];
    videoInputs: MediaDeviceInfo[];
  }> => {
    const { video = false, audio = true } = props || {};
    let audioInputs: MediaDeviceInfo[] = [];
    let audioOutputs: MediaDeviceInfo[] = [];
    let videoInputs: MediaDeviceInfo[] = [];
    const { video: hasVideoPermission, audio: hasAudioPermission } = await VERTC.enableDevices({
      video,
      audio,
    });
    if (audio) {
      const inputs = await VERTC.enumerateAudioCaptureDevices();
      const outputs = await VERTC.enumerateAudioPlaybackDevices();
      audioInputs = inputs.filter((i) => i.deviceId && i.kind === 'audioinput');
      audioOutputs = outputs.filter((i) => i.deviceId && i.kind === 'audiooutput');
      this._audioCaptureDevice = audioInputs.filter((i) => i.deviceId)?.[0]?.deviceId;
      if (hasAudioPermission) {
        if (!audioInputs?.length) {
          Message.error('无麦克风设备, 请先确认设备情况。');
        }
        if (!audioOutputs?.length) {
          Message.error('无扬声器设备, 请先确认设备情况。');
        }
      } else {
        Message.error('暂无麦克风设备权限, 请先确认设备权限授予情况。');
      }
    }
    if (video) {
      videoInputs = await VERTC.enumerateVideoCaptureDevices();
      videoInputs = videoInputs.filter((i) => i.deviceId && i.kind === 'videoinput');
      this._videoCaptureDevice = videoInputs?.[0]?.deviceId;
      if (hasVideoPermission) {
        if (!videoInputs?.length) {
          Message.error('无摄像头设备, 请先确认设备情况。');
        }
      } else {
        Message.error('暂无摄像头设备权限, 请先确认设备权限授予情况。');
      }
    }

    return {
      audioInputs,
      audioOutputs,
      videoInputs,
    };
  };

  startVideoCapture = async (camera?: string): Promise<void> => {
    await rtcEngine.engine.startVideoCapture(camera || this._videoCaptureDevice);
  };

  stopVideoCapture = async (): Promise<void> => {
    rtcEngine.engine.setLocalVideoMirrorType(MirrorType.MIRROR_TYPE_RENDER);
    await rtcEngine.engine.stopVideoCapture();
  };

  startScreenCapture = async (enableAudio = false): Promise<void> => {
    await rtcEngine.engine.startScreenCapture({
      enableAudio,
    });
  };

  stopScreenCapture = async (): Promise<void> => {
    await rtcEngine.engine.stopScreenCapture();
  };

  startAudioCapture = async (mic?: string): Promise<void> => {
    await rtcEngine.engine.startAudioCapture(mic || this._audioCaptureDevice);
  };

  stopAudioCapture = async (): Promise<void> => {
    await rtcEngine.engine.stopAudioCapture();
  };

  setAudioVolume = (volume: number): void => {
    rtcEngine.engine.setCaptureVolume(StreamIndex.STREAM_INDEX_MAIN, volume);
    rtcEngine.engine.setCaptureVolume(StreamIndex.STREAM_INDEX_SCREEN, volume);
  };

  /**
   * @brief 设置音质档位
   */
  setAudioProfile = (profile: AudioProfileType): void => {
    rtcEngine.engine.setAudioProfile(profile);
  };

  /**
   * @brief 切换设备
   */
  switchDevice = (deviceType: MediaType, deviceId: string): void => {
    if (deviceType === MediaType.AUDIO) {
      this._audioCaptureDevice = deviceId;
      rtcEngine.engine.setAudioCaptureDevice(deviceId);
    }
    if (deviceType === MediaType.VIDEO) {
      this._videoCaptureDevice = deviceId;
      rtcEngine.engine.setVideoCaptureDevice(deviceId);
    }
    if (deviceType === MediaType.AUDIO_AND_VIDEO) {
      this._audioCaptureDevice = deviceId;
      this._videoCaptureDevice = deviceId;
      rtcEngine.engine.setVideoCaptureDevice(deviceId);
      rtcEngine.engine.setAudioCaptureDevice(deviceId);
    }
  };
}

export const deviceManager = new DeviceManager();
