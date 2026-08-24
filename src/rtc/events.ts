/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 *
 * RTC 事件 → 领域事件映射（官方 listenerHooks 逻辑提炼为纯函数）
 */

import {
  MediaType,
  NetworkQuality,
  onUserJoinedEvent,
  RemoteAudioPropertiesInfo,
  StreamIndex,
} from '@volcengine/rtc';

export interface JoinPayload {
  userId: string;
  username: string;
}

export interface StreamStatePayload {
  publishAudio?: boolean;
  publishVideo?: boolean;
}

export interface AudioPropertiesPayload {
  userId: string;
  audioPropertiesInfo: RemoteAudioPropertiesInfo['audioPropertiesInfo'];
}

export function mapUserJoined(e: onUserJoinedEvent): JoinPayload {
  const extraInfo = JSON.parse(e.userInfo.extraInfo || '{}');
  return {
    userId: extraInfo.user_id || e.userInfo.userId,
    username: extraInfo.user_name || e.userInfo.userId,
  };
}

export function mapPublishStreamState(mediaType: MediaType): StreamStatePayload {
  const payload: StreamStatePayload = {};
  if (mediaType === MediaType.AUDIO) {
    payload.publishAudio = true;
  } else if (mediaType === MediaType.VIDEO) {
    payload.publishVideo = true;
  } else if (mediaType === MediaType.AUDIO_AND_VIDEO) {
    payload.publishAudio = true;
    payload.publishVideo = true;
  }
  return payload;
}

export function mapUnpublishStreamState(mediaType: MediaType): StreamStatePayload {
  const payload: StreamStatePayload = {};
  if (mediaType === MediaType.AUDIO) {
    payload.publishAudio = false;
  }
  if (mediaType === MediaType.AUDIO_AND_VIDEO) {
    payload.publishAudio = false;
  }
  // 注意：官方逻辑 VIDEO/AUDIO_AND_VIDEO 均不置 publishVideo=false，保持协议行为一致
  return payload;
}

export function mapRemoteAudioProperties(
  reports: RemoteAudioPropertiesInfo[]
): AudioPropertiesPayload[] {
  return reports
    .filter((audioInfo) => audioInfo.streamKey.streamIndex === StreamIndex.STREAM_INDEX_MAIN)
    .map((audioInfo) => ({
      userId: audioInfo.streamKey.userId,
      audioPropertiesInfo: audioInfo.audioPropertiesInfo,
    }));
}

export function mapNetworkQuality(
  uplink: NetworkQuality,
  downlink: NetworkQuality
): NetworkQuality {
  return Math.floor((uplink + downlink) / 2) as NetworkQuality;
}
