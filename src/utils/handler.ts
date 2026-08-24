/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useDispatch } from 'react-redux';
import logger from './logger';
import {
  setHistoryMsg,
  setInterruptMsg,
  updateAITalkState,
  updateAIThinkState,
} from '@/store/slices/room';
import { agentChannel } from '@/rtc/AgentChannel';
import { string2tlv } from '@/rtc/codec/tlv';
import {
  AGENT_BRIEF,
  MESSAGE_TYPE,
  briefStateToAction,
  extractFunctionCall,
  extractSubtitle,
  parseSemanticMessage,
  type BriefPayload,
  type FunctionCallPayload,
  type SemanticMessage,
  type SubtitlePayload,
} from '@/rtc/codec/messages';

// 兼容出口：原官方单例代码原从本文件引用指令常量，拆分为 messages.ts 后保留
export {
  AGENT_BRIEF,
  COMMAND,
  INTERRUPT_PRIORITY,
  MESSAGE_TYPE,
  MessageTypeCode,
} from '@/rtc/codec/messages';

export type AnyRecord = Record<string, any>;

/** 官方 FUNCTION_CALL 的硬编码应答表（协议行为保留） */
const FUNCTION_REPLY_MAP: Record<string, string> = {
  getcurrentweather: '今天下雪， 最低气温零下10度',
};

export const useMessageHandler = () => {
  const dispatch = useDispatch();

  const maps = {
    /**
     * @brief 接收状态变化信息
     * @note https://www.volcengine.com/docs/6348/1415216?s=g
     */
    [MESSAGE_TYPE.BRIEF]: (parsed: BriefPayload) => {
      const { Stage } = parsed || {};
      const { Code, Description } = Stage || {};
      logger.debug('[MESSAGE_TYPE.BRIEF]: ', Code, Description);
      switch (briefStateToAction(Code as AGENT_BRIEF)) {
        case 'thinking':
          dispatch(updateAIThinkState({ isAIThinking: true }));
          break;
        case 'speaking':
          dispatch(updateAITalkState({ isAITalking: true }));
          break;
        case 'finished':
          dispatch(updateAITalkState({ isAITalking: false }));
          break;
        case 'interrupted':
          dispatch(setInterruptMsg());
          break;
        default:
          break;
      }
    },
    /**
     * @brief 字幕
     * @note https://www.volcengine.com/docs/6348/1337284?s=g
     */
    [MESSAGE_TYPE.SUBTITLE]: (parsed: SubtitlePayload) => {
      const data = extractSubtitle(parsed);
      // 行为偏差标注：官方在 data 为空（无可解析字幕）时仍 dispatch 一条全 undefined 的消息条目；
      // 此处改为跳过（净等价——undefined 条目不渲染任何内容，且避免污染历史消息数组），测试已固化。
      if (data) {
        const { text: msg, definite, user, paragraph } = data;
        const isAudioEnable = agentChannel.getAgentEnabled();
        if ((window as any)._debug_mode) {
          logger.debug('handleRoomBinaryMessageReceived', data);
        }
        if (isAudioEnable) {
          dispatch(setHistoryMsg({ text: msg, user, paragraph, definite }));
        }
      }
    },
    /**
     * @brief Function calling
     * @note https://www.volcengine.com/docs/6348/1359441?s=g
     */
    [MESSAGE_TYPE.FUNCTION_CALL]: (parsed: FunctionCallPayload) => {
      const call = extractFunctionCall(parsed);
      console.log('[Function Call] - Called by sendUserBinaryMessage');
      const name = call?.name ?? '';
      // 行为偏差标注：官方代码假定 tool_calls[0] 存在，不存在时抛错（被旧整体 try/catch 吞掉）；
      // 此处改为发送空应答 TLV。理由：null 安全抽取，实际不可达。
      agentChannel.sendBinaryMessage(
        'RobotMan_',
        string2tlv(
          JSON.stringify({
            ToolCallID: call?.id,
            Content: FUNCTION_REPLY_MAP[name.toLocaleLowerCase().replaceAll('_', '')],
          }),
          'func'
        )
      );
    },
  };

  return {
    parser: (buffer: ArrayBuffer) => {
      // 行为偏差标注：官方代码在整体 try/catch 中解析+分发，畸形二进制会抛错并被吞掉；
      // 提取后解析为 null 安全（返回 null），分发移出 try/catch。理由：Task 4 纯函数抽取设计，实际不可达。
      const parsed = parseSemanticMessage(buffer);
      if (!parsed) {
        logger.debug('parse error');
        return;
      }
      const handler = maps[parsed.type] as (payload: SemanticMessage['payload']) => void;
      handler(parsed.payload);
    },
  };
};
