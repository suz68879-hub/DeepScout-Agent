/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 *
 * AI 会话控制（原官方单例拆分产物，协议级代码逐行移植）
 */

import Apis from '@/app/index';
import { string2tlv } from './codec/tlv';
import { COMMAND, INTERRUPT_PRIORITY } from './codec/messages';
import { rtcEngine } from './RtcEngine';

export class AgentChannel {
  audioBotEnabled = false;

  audioBotStartTime = 0;

  /**
   * @brief 启用 AIGC
   */
  startAgent = async (scene: string): Promise<void> => {
    if (this.audioBotEnabled) {
      await this.stopAgent(scene);
    }
    await Apis.VoiceChat.StartVoiceChat({
      SceneID: scene,
      SessionId: rtcEngine.basicInfo.session_id,
    });
    this.audioBotEnabled = true;
    this.audioBotStartTime = Date.now();
  };

  /**
   * @brief 关闭 AIGC
   */
  stopAgent = async (scene: string): Promise<void> => {
    if (this.audioBotEnabled || sessionStorage.getItem('audioBotEnabled')) {
      await Apis.VoiceChat.StopVoiceChat({
        SceneID: scene,
        SessionId: rtcEngine.basicInfo.session_id,
      });
      this.audioBotStartTime = 0;
      sessionStorage.removeItem('audioBotEnabled');
    }
    this.audioBotEnabled = false;
  };

  /**
   * @brief 命令 AIGC
   */
  commandAgent = ({
    command,
    agentName,
    interruptMode = INTERRUPT_PRIORITY.NONE,
    message = '',
  }: {
    command: COMMAND;
    agentName: string;
    interruptMode?: INTERRUPT_PRIORITY;
    message?: string;
  }): void => {
    if (this.audioBotEnabled) {
      this.sendBinaryMessage(
        agentName,
        string2tlv(
          JSON.stringify({
            Command: command,
            InterruptMode: interruptMode,
            Message: message,
          }),
          'ctrl'
        )
      );
      return;
    }
    console.warn('Interrupt failed, bot not enabled.');
  };

  /**
   * @brief 更新 AIGC 配置
   */
  updateAgent = async (scene: string): Promise<void> => {
    if (this.audioBotEnabled) {
      await this.stopAgent(scene);
      await this.startAgent(scene);
    } else {
      await this.startAgent(scene);
    }
  };

  /**
   * @brief 获取当前 AI 是否启用
   */
  getAgentEnabled = (): boolean => {
    return this.audioBotEnabled;
  };

  /**
   * @brief 发送二进制消息（FUNCTION_CALL 应答等原始路径，无启用校验——与官方行为一致）
   */
  sendBinaryMessage = (agentName: string, buffer: ArrayBuffer): void => {
    rtcEngine.engine.sendUserBinaryMessage(agentName, buffer);
  };
}

export const agentChannel = new AgentChannel();
