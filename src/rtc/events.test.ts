import { describe, expect, it, vi } from 'vitest';

import { MediaType } from '@volcengine/rtc';
import {
  mapNetworkQuality,
  mapPublishStreamState,
  mapRemoteAudioProperties,
  mapUnpublishStreamState,
  mapUserJoined,
} from './events';

vi.mock('@volcengine/rtc', () => ({
  MediaType: { AUDIO: 0, VIDEO: 1, AUDIO_AND_VIDEO: 2 },
  StreamIndex: { STREAM_INDEX_MAIN: 0, STREAM_INDEX_SCREEN: 1 },
}));

describe('rtc events 映射', () => {
  it('mapUserJoined 解析 extraInfo', () => {
    const e = {
      userInfo: {
        userId: 'uid-1',
        extraInfo: JSON.stringify({ user_id: 'uid-x', user_name: '张三' }),
      },
    } as any;
    expect(mapUserJoined(e)).toEqual({ userId: 'uid-x', username: '张三' });
  });

  it('mapUserJoined extraInfo 为空时回退 userId', () => {
    const e = { userInfo: { userId: 'uid-1', extraInfo: '' } } as any;
    expect(mapUserJoined(e)).toEqual({ userId: 'uid-1', username: 'uid-1' });
  });

  it('mapPublishStreamState 映射三种媒体类型', () => {
    expect(mapPublishStreamState(MediaType.AUDIO)).toEqual({ publishAudio: true });
    expect(mapPublishStreamState(MediaType.VIDEO)).toEqual({ publishVideo: true });
    expect(mapPublishStreamState(MediaType.AUDIO_AND_VIDEO)).toEqual({
      publishAudio: true,
      publishVideo: true,
    });
  });

  it('mapUnpublishStreamState 保持官方行为（音视频仅置 audio=false）', () => {
    expect(mapUnpublishStreamState(MediaType.AUDIO)).toEqual({ publishAudio: false });
    expect(mapUnpublishStreamState(MediaType.AUDIO_AND_VIDEO)).toEqual({ publishAudio: false });
    expect(mapUnpublishStreamState(MediaType.VIDEO)).toEqual({});
  });

  it('mapRemoteAudioProperties 过滤主路并提取音量信息', () => {
    const main = {
      streamKey: { streamIndex: 0, userId: 'u1' },
      audioPropertiesInfo: { linearVolume: 100 },
    };
    const screen = {
      streamKey: { streamIndex: 1, userId: 'u1' },
      audioPropertiesInfo: { linearVolume: 50 },
    };
    expect(mapRemoteAudioProperties([main, screen] as any)).toEqual([
      { userId: 'u1', audioPropertiesInfo: { linearVolume: 100 } },
    ]);
  });

  it('mapNetworkQuality 取上下行平均并取整', () => {
    expect(mapNetworkQuality(4 as any, 3 as any)).toBe(3);
  });
});
