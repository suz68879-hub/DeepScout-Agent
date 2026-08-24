import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MediaType } from '@volcengine/rtc';
import { deviceManager } from './DeviceManager';

const mocks = vi.hoisted(() => {
  const engine = {
    setAudioCaptureDevice: vi.fn(),
    setVideoCaptureDevice: vi.fn(),
    setCaptureVolume: vi.fn(),
    startAudioCapture: vi.fn(),
    startVideoCapture: vi.fn(),
  };
  return {
    engine,
    enableDevices: vi.fn().mockResolvedValue({ audio: true, video: true }),
    enumerateAudioCaptureDevices: vi.fn(),
    enumerateAudioPlaybackDevices: vi.fn(),
    enumerateVideoCaptureDevices: vi.fn(),
    messageError: vi.fn(),
  };
});

vi.mock('@volcengine/rtc', () => ({
  default: {
    enableDevices: mocks.enableDevices,
    enumerateAudioCaptureDevices: mocks.enumerateAudioCaptureDevices,
    enumerateAudioPlaybackDevices: mocks.enumerateAudioPlaybackDevices,
    enumerateVideoCaptureDevices: mocks.enumerateVideoCaptureDevices,
  },
  MediaType: { AUDIO: 0, VIDEO: 1, AUDIO_AND_VIDEO: 2 },
  MirrorType: { MIRROR_TYPE_RENDER: 0 },
  StreamIndex: { STREAM_INDEX_MAIN: 0, STREAM_INDEX_SCREEN: 1 },
}));

vi.mock('@arco-design/web-react', () => ({
  Message: { error: mocks.messageError },
}));

vi.mock('./RtcEngine', () => ({
  rtcEngine: { engine: mocks.engine },
}));

const mic = (id: string) => ({ deviceId: id, kind: 'audioinput' } as MediaDeviceInfo);
const speaker = (id: string) => ({ deviceId: id, kind: 'audiooutput' } as MediaDeviceInfo);
const cam = (id: string) => ({ deviceId: id, kind: 'videoinput' } as MediaDeviceInfo);

describe('DeviceManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.enumerateAudioCaptureDevices.mockResolvedValue([mic('mic-1'), { kind: 'audioinput' }]);
    mocks.enumerateAudioPlaybackDevices.mockResolvedValue([speaker('spk-1')]);
    mocks.enumerateVideoCaptureDevices.mockResolvedValue([cam('cam-1')]);
  });

  it('getDevices 过滤无效设备并缓存默认采集设备', async () => {
    const devices = await deviceManager.getDevices({ audio: true, video: true });
    expect(devices.audioInputs.map((d) => d.deviceId)).toEqual(['mic-1']);
    expect(devices.videoInputs.map((d) => d.deviceId)).toEqual(['cam-1']);

    await deviceManager.startAudioCapture();
    expect(mocks.engine.startAudioCapture).toHaveBeenCalledWith('mic-1');
  });

  it('getDevices 无麦克风设备时给出错误提示', async () => {
    mocks.enumerateAudioCaptureDevices.mockResolvedValue([]);
    mocks.enumerateAudioPlaybackDevices.mockResolvedValue([]);
    await deviceManager.getDevices({ audio: true });
    expect(mocks.messageError).toHaveBeenCalledWith('无麦克风设备, 请先确认设备情况。');
  });

  it('switchDevice 切换音频设备', () => {
    deviceManager.switchDevice(MediaType.AUDIO, 'mic-2');
    expect(mocks.engine.setAudioCaptureDevice).toHaveBeenCalledWith('mic-2');
  });

  it('switchDevice 音视频同时切换时更新两个设备', () => {
    deviceManager.switchDevice(MediaType.AUDIO_AND_VIDEO, 'dev-x');
    expect(mocks.engine.setAudioCaptureDevice).toHaveBeenCalledWith('dev-x');
    expect(mocks.engine.setVideoCaptureDevice).toHaveBeenCalledWith('dev-x');
  });

  it('setAudioVolume 同时设置主路与屏幕流采集音量', () => {
    deviceManager.setAudioVolume(80);
    expect(mocks.engine.setCaptureVolume).toHaveBeenCalledWith(0, 80);
    expect(mocks.engine.setCaptureVolume).toHaveBeenCalledWith(1, 80);
  });
});
