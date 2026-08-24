import { describe, expect, it } from 'vitest';
import { dimensionRadar, overallTrend, questionTypePie, weaknessBars } from './rowsToChart';

const rows = [
  {
    created_at: '2026-08-10T10:00:00',
    scores_json: '{"技术深度": 8, "项目理解": 6, "表达沟通": 8, "临场表现": 6}',
    feedback_json: JSON.stringify({
      summary: '', round_scores: [], strengths: [],
      improvements: ['表达更简洁'],
      round_details: [{ round_no: 1, question: '讲讲你的项目难点', answer_summary: '', comment: '' }],
    }),
  },
  {
    created_at: '2026-08-17T10:00:00',
    scores_json: '{"技术深度": 6, "项目理解": 6, "表达沟通": 6, "临场表现": 6}',
    feedback_json: JSON.stringify({
      summary: '', round_scores: [], strengths: [],
      improvements: ['表达更简洁'],
      round_details: [{ round_no: 1, question: 'Java GC 原理', answer_summary: '', comment: '' }],
    }),
  },
];

describe('rowsToChart', () => {
  it('overallTrend 从 scores_json 计算趋势', () => {
    expect(overallTrend(rows)).toEqual([
      { label: '2026-08-10', value: 7 },
      { label: '2026-08-17', value: 6 },
    ]);
  });

  it('dimensionRadar 取最近一份维度评分', () => {
    expect(dimensionRadar(rows)).toEqual({ 技术深度: 6, 项目理解: 6, 表达沟通: 6, 临场表现: 6 });
  });

  it('weaknessBars 统计改进点频次', () => {
    expect(weaknessBars(rows)).toEqual([{ label: '表达更简洁', value: 2 }]);
  });

  it('questionTypePie 按关键词归类题目', () => {
    expect(questionTypePie(rows)).toEqual([
      { label: '项目深挖', value: 1 },
      { label: '技术题', value: 1 },
    ]);
  });
});
