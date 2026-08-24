/**
 * E2E 模式（P7）：VITE_E2E=1（.env.e2e + vite --mode e2e）时，
 * 短路 useCommon 四个 RTC hook 的 SDK 调用，并以脚本化字幕驱动面试间，
 * 让 Playwright 无需真实 RTC/后端即可跑通 首页→面试间→报告 主流程。
 * 非 E2E 构建下这些代码不可达（import.meta.env 编译期常量）。
 */
import { useEffect } from 'react';
import { useDispatch } from 'react-redux';
import {
  setHistoryMsg,
  updateAITalkState,
  updateAIThinkState,
} from '@/store/slices/room';
import type { RTCConfig, SceneConfig } from '@/store/slices/room';

export function isE2EMode(): boolean {
  return import.meta.env.VITE_E2E === '1';
}

export const E2E_SCENE_ID = 'e2e-mock';

export const E2E_SCENE_CONFIG: SceneConfig = {
  id: E2E_SCENE_ID,
  botName: '懂小智',
  isVision: false,
  isScreenMode: false,
  isInterruptMode: false,
  isAvatarScene: false,
  avatarBgUrl: '',
};

export const E2E_RTC_CONFIG: RTCConfig = {
  AppId: 'e2e-mock',
  RoomId: 'e2e-room',
  UserId: 'e2e-user',
  Token: 'e2e-token',
  SessionId: 'e2e-session',
};

export const E2E_USER_ID = 'e2e-user';

/** 脚本化字幕：主动开场 → 技术基础 → 项目深挖（对应正式面试流程） */
export const E2E_SCRIPT: { user: string; text: string }[] = [
  { user: '懂小智', text: '你好，我是懂小智，欢迎参加今天的面试' },
  { user: '懂小智', text: '请先做一下自我介绍' },
  { user: E2E_USER_ID, text: '大家好，我是一名 Java 后端开发，做过秒杀系统' },
  { user: '懂小智', text: '你简历里写到熟悉 Redis，先讲讲它常见的数据结构和适用场景。' },
  { user: E2E_USER_ID, text: '我主要用过字符串、哈希和有序集合。' },
  { user: '懂小智', text: '你简历里的秒杀系统，库存是怎么扣的？' },
  { user: E2E_USER_ID, text: '我们用的是数据库行锁加 Redis 预扣' },
  { user: '懂小智', text: '明白了，那为什么不用 Redis 预扣，行锁的瓶颈在哪里？' },
  { user: E2E_USER_ID, text: '热点商品锁竞争激烈，需要分片' },
  { user: '懂小智', text: '如果让你重新设计这个库存系统，你会怎么做？' },
];

/**
 * E2E 会话驱动：确定性注入脚本化字幕并切换 AI 说话状态。
 * 仅 isE2EMode() 时生效，否则为 no-op。
 */
export function useE2ESessionDriver(sessionId: string | undefined): void {
  const dispatch = useDispatch();
  useEffect(() => {
    if (!isE2EMode()) {
      return undefined;
    }
    dispatch(updateAITalkState({ isAITalking: false }));
    E2E_SCRIPT.forEach(line => {
      dispatch(updateAIThinkState({ isAIThinking: line.user !== E2E_USER_ID }));
      dispatch(
        setHistoryMsg({
          text: line.text,
          time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
          user: line.user,
          paragraph: true,
          definite: true,
        })
      );
      dispatch(updateAITalkState({ isAITalking: line.user !== E2E_USER_ID }));
    });
    return undefined;
    // sessionId 仅用于让驱动随会话变化重启，不需要作为依赖项的值参与
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dispatch, sessionId]);
}
