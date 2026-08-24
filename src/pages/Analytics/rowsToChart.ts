import { overallFromDims, parseFeedback, parseScores } from '@/domain/report/types';

export type LabelValue = {
  label: string;
  value: number;
}

// 后端模板查询返回原始 JSON 列（sqlite 无法解析 JSON），应用层解析（后端 explanation 契约）
export function overallTrend(rows: Record<string, unknown>[]): LabelValue[] {
  return rows.map((r) => ({
    label: String(r.created_at ?? '').slice(0, 10),
    value: overallFromDims(parseScores(String(r.scores_json ?? ''))),
  }));
}

export function dimensionRadar(rows: Record<string, unknown>[]): Record<string, number> {
  const last = rows[rows.length - 1];
  if (!last) return {};
  return parseScores(String(last.scores_json ?? ''));
}

export function weaknessBars(rows: Record<string, unknown>[]): LabelValue[] {
  const counts = new Map<string, number>();
  rows.forEach((r) => {
    parseFeedback(String(r.feedback_json ?? '')).improvements.forEach((s) => {
      counts.set(s, (counts.get(s) ?? 0) + 1);
    });
  });
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([label, value]) => ({ label, value }));
}

const STAGE_KEYWORDS: [string, string[]][] = [
  ['自我介绍', ['自我介绍', 'introduce']],
  ['项目深挖', ['项目', '难点', '架构']],
  ['技术题', ['Java', 'GC', '线程', 'Redis', 'MySQL', 'Spring', '设计模式', 'Agent', 'RAG']],
  ['反问', ['反问', '问题']],
];

export function questionTypePie(rows: Record<string, unknown>[]): LabelValue[] {
  const counts = new Map<string, number>();
  rows.forEach((r) => {
    parseFeedback(String(r.feedback_json ?? '')).round_details.forEach((d) => {
      const hit = STAGE_KEYWORDS.find(([, kws]) => kws.some((k) => d.question.includes(k)));
      const type = hit ? hit[0] : '其他';
      counts.set(type, (counts.get(type) ?? 0) + 1);
    });
  });
  return Array.from(counts.entries()).map(([label, value]) => ({ label, value }));
}
