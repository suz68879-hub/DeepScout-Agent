import { beforeEach, describe, expect, it, vi } from 'vitest';

import { agentChannel } from './AgentChannel';
import { tlv2String } from './codec/tlv';
import { COMMAND, INTERRUPT_PRIORITY } from './codec/messages';

const mocks = vi.hoisted(() => ({
  sendUserBinaryMessage: vi.fn(),
  StartVoiceChat: vi.fn().mockResolvedValue(undefined),
  StopVoiceChat: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('@/app/index', () => ({
  default: {
    VoiceChat: {
      StartVoiceChat: mocks.StartVoiceChat,
      StopVoiceChat: mocks.StopVoiceChat,
    },
  },
}));

vi.mock('./RtcEngine', () => ({
  rtcEngine: {
    engine: { sendUserBinaryMessage: mocks.sendUserBinaryMessage },
    basicInfo: { session_id: 'session-1' },
  },
}));

vi.stubGlobal('sessionStorage', {
  getItem: vi.fn(() => null),
  removeItem: vi.fn(),
  setItem: vi.fn(),
});

describe('AgentChannel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    agentChannel.audioBotEnabled = false;
    agentChannel.audioBotStartTime = 0;
  });

  it('startAgent 未启用时直接启动', async () => {
    await agentChannel.startAgent('Custom');
    expect(mocks.StartVoiceChat).toHaveBeenCalledWith({
      SceneID: 'Custom', SessionId: 'session-1',
    });
    expect(agentChannel.audioBotEnabled).toBe(true);
    expect(agentChannel.audioBotStartTime).toBeGreaterThan(0);
  });

  it('startAgent 已启用时先停止再启动', async () => {
    agentChannel.audioBotEnabled = true;
    await agentChannel.startAgent('Custom');
    expect(mocks.StopVoiceChat).toHaveBeenCalledWith({
      SceneID: 'Custom', SessionId: 'session-1',
    });
    expect(mocks.StartVoiceChat).toHaveBeenCalledWith({
      SceneID: 'Custom', SessionId: 'session-1',
    });
  });

  it('stopAgent 未启用且无 session 标记时不发请求', async () => {
    await agentChannel.stopAgent('Custom');
    expect(mocks.StopVoiceChat).not.toHaveBeenCalled();
  });

  it('stopAgent 启用中时发送停止请求并复位状态', async () => {
    agentChannel.audioBotEnabled = true;
    await agentChannel.stopAgent('Custom');
    expect(mocks.StopVoiceChat).toHaveBeenCalledWith({
      SceneID: 'Custom', SessionId: 'session-1',
    });
    expect(agentChannel.audioBotEnabled).toBe(false);
    expect(agentChannel.audioBotStartTime).toBe(0);
  });

  it('commandAgent 启用中发送 ctrl TLV 二进制消息', () => {
    agentChannel.audioBotEnabled = true;
    agentChannel.commandAgent({
      command: COMMAND.INTERRUPT,
      agentName: 'RobotMan_',
      interruptMode: INTERRUPT_PRIORITY.HIGH,
      message: '打断一下',
    });
    expect(mocks.sendUserBinaryMessage).toHaveBeenCalledTimes(1);
    const [agentName, buffer] = mocks.sendUserBinaryMessage.mock.calls[0];
    expect(agentName).toBe('RobotMan_');
    expect(tlv2String(buffer as ArrayBuffer)).toEqual({
      type: 'ctrl',
      value: JSON.stringify({ Command: 'interrupt', InterruptMode: 1, Message: '打断一下' }),
    });
  });

  it('commandAgent 未启用时仅告警不发送', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    agentChannel.commandAgent({ command: COMMAND.INTERRUPT, agentName: 'RobotMan_' });
    expect(mocks.sendUserBinaryMessage).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith('Interrupt failed, bot not enabled.');
    warn.mockRestore();
  });

  it('updateAgent 复用官方语义：已启用则重启，未启用则启动', async () => {
    agentChannel.audioBotEnabled = true;
    await agentChannel.updateAgent('Custom');
    expect(mocks.StopVoiceChat).toHaveBeenCalled();
    expect(mocks.StartVoiceChat).toHaveBeenCalled();
  });

  it('getAgentEnabled 反映当前状态', () => {
    expect(agentChannel.getAgentEnabled()).toBe(false);
    agentChannel.audioBotEnabled = true;
    expect(agentChannel.getAgentEnabled()).toBe(true);
  });
});
