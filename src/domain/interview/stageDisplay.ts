import type { StageId } from './types';

export interface StageMeta {
  label: string;
  hint: string;
}

// spec §2.3：阶段状态归属后端，本模块为纯展示工具
export const STAGE_META: Record<StageId, StageMeta> = {
  intro: { label: '自我介绍', hint: '1 分钟自我介绍' },
  deepdive: { label: '项目深挖', hint: '围绕简历项目追问' },
  technical: { label: '技术面', hint: 'Java 后端 + Agent 方向' },
  qa: { label: '反问环节', hint: '向面试官提问' },
  finish: { label: '面试结束', hint: '报告生成中' },
};

export function stageMeta(stage: string): StageMeta {
  return STAGE_META[stage as StageId] ?? { label: stage, hint: '' };
}
