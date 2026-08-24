/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 *
 * SUBTITLE/BRIEF/FUNCTION_CALL 语义消息解析（从官方 handler.ts 提炼为纯函数）
 */

import { tlv2String } from './tlv';

export enum MESSAGE_TYPE {
  BRIEF = 'conv',
  SUBTITLE = 'subv',
  FUNCTION_CALL = 'tool',
}

export enum AGENT_BRIEF {
  UNKNOWN,
  LISTENING,
  THINKING,
  SPEAKING,
  INTERRUPTED,
  FINISHED,
}

/**
 * @brief 指令类型
 */
export enum COMMAND {
  /** @brief 打断指令 */
  INTERRUPT = 'interrupt',
  /** @brief 发送外部文本驱动 TTS */
  EXTERNAL_TEXT_TO_SPEECH = 'ExternalTextToSpeech',
  /** @brief 发送外部文本驱动 LLM */
  EXTERNAL_TEXT_TO_LLM = 'ExternalTextToLLM',
}

/**
 * @brief 打断的类型
 */
export enum INTERRUPT_PRIORITY {
  /** @brief 占位 */
  NONE,
  /** @brief 高优先级。传入信息直接打断交互，进行处理。 */
  HIGH,
  /** @brief 中优先级。等待当前交互结束后，进行处理。 */
  MEDIUM,
  /** @brief 低优先级。如当前正在发生交互，直接丢弃 Message 传入的信息。 */
  LOW,
}

export const MessageTypeCode = {
  [MESSAGE_TYPE.SUBTITLE]: 1,
  [MESSAGE_TYPE.FUNCTION_CALL]: 2,
  [MESSAGE_TYPE.BRIEF]: 3,
};

export interface BriefPayload {
  Stage?: { Code?: number; Description?: string };
}

export interface SubtitlePayload {
  data?: Array<{
    text?: string;
    definite?: boolean;
    userId?: string;
    paragraph?: boolean;
  }>;
}

export interface FunctionCallPayload {
  tool_calls?: Array<{ id?: string; function?: { name?: string } }>;
}

export type SemanticMessage =
  | { type: MESSAGE_TYPE.BRIEF; payload: BriefPayload }
  | { type: MESSAGE_TYPE.SUBTITLE; payload: SubtitlePayload }
  | { type: MESSAGE_TYPE.FUNCTION_CALL; payload: FunctionCallPayload };

export function parseSemanticMessage(buffer: ArrayBuffer): SemanticMessage | null {
  try {
    const { type, value } = tlv2String(buffer);
    if (
      type !== MESSAGE_TYPE.BRIEF &&
      type !== MESSAGE_TYPE.SUBTITLE &&
      type !== MESSAGE_TYPE.FUNCTION_CALL
    ) {
      return null;
    }
    return { type, payload: JSON.parse(value) } as SemanticMessage;
  } catch {
    return null;
  }
}

export function briefStateToAction(
  code: AGENT_BRIEF
): 'thinking' | 'speaking' | 'finished' | 'interrupted' | null {
  switch (code) {
    case AGENT_BRIEF.THINKING:
      return 'thinking';
    case AGENT_BRIEF.SPEAKING:
      return 'speaking';
    case AGENT_BRIEF.FINISHED:
      return 'finished';
    case AGENT_BRIEF.INTERRUPTED:
      return 'interrupted';
    default:
      return null;
  }
}

export function extractSubtitle(
  payload: SubtitlePayload
): { text?: string; user?: string; paragraph?: boolean; definite?: boolean } | null {
  const data = payload.data?.[0];
  if (!data) {
    return null;
  }
  const { text, definite, userId: user, paragraph } = data;
  return { text, user, paragraph, definite };
}

export function extractFunctionCall(
  payload: FunctionCallPayload
): { id?: string; name?: string } | null {
  const call = payload.tool_calls?.[0];
  if (!call) {
    return null;
  }
  return { id: call.id, name: call.function?.name };
}
