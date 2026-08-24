import { describe, expect, it } from 'vitest';
import { overallFromDims, overallFromReport, parseFeedback, parseScores } from './types';

describe('report types', () => {
  it('parseScores 解析维度评分 JSON', () => {
    expect(parseScores('{"技术深度": 8, "项目理解": 7}')).toEqual({ 技术深度: 8, 项目理解: 7 });
  });

  it('parseScores 对 null/坏 JSON 返回空对象', () => {
    expect(parseScores(null)).toEqual({});
    expect(parseScores('{oops')).toEqual({});
  });

  it('parseFeedback 解析逐题记录与建议', () => {
    const f = parseFeedback(
      '{"summary": "s", "round_scores": [7.5], "round_details": [{"round_no": 1, "question": "q", "answer_summary": "a", "comment": "c"}], "strengths": ["x"], "improvements": ["y"]}'
    );
    expect(f.summary).toBe('s');
    expect(f.round_scores).toEqual([7.5]);
    expect(f.round_details[0].question).toBe('q');
    expect(f.improvements).toEqual(['y']);
  });

  it('parseFeedback 对 null/坏 JSON 返回空结构', () => {
    expect(parseFeedback(null).round_details).toEqual([]);
    expect(parseFeedback('bad').round_scores).toEqual([]);
  });

  it('overallFromDims 取维度均值并保留一位小数', () => {
    expect(overallFromDims({ 技术深度: 8, 项目理解: 7, 表达沟通: 9, 临场表现: 6 })).toBe(7.5);
    expect(overallFromDims({})).toBe(0);
  });

  it('overallFromReport 从 scores_json 计算总评分', () => {
    expect(overallFromReport({ scores_json: '{"技术深度": 8, "项目理解": 6}' } as never)).toBe(7);
  });
});
