// 数据分析类型（对齐 api/analytics.py 返回结构）
export type ChartType = 'line' | 'bar' | 'radar' | 'pie' | 'table';

export interface AnalyticsResult {
  sql: string;
  explanation: string;
  chart_type: ChartType;
  rows: Record<string, unknown>[];
}

// spec §5.8 兜底模板五类：前端按钮直接发送这些自然语言问题
// （问题措辞与后端 TEMPLATE_QUERIES 的关键词匹配表对齐）
export const TEMPLATE_QUERIES: { label: string; question: string }[] = [
  { label: '近 5 次总评分趋势', question: '最近 5 次面试的总评分趋势' },
  { label: '各维度雷达对比', question: '统计各维度的平均分对比' },
  { label: '题目类型分布', question: '统计面试题目的阶段类型分布' },
  { label: '高频薄弱点', question: '统计最常见的改进建议关键词' },
  { label: '面试频次与平均分', question: '按日期统计面试次数与平均分' },
];
