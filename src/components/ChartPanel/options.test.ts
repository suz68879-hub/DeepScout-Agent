import { describe, expect, it } from 'vitest';
import { analyticsOption, radarOption, roundBarOption } from './options';

describe('chart options', () => {
  it('radarOption 覆盖四维度与得分', () => {
    const option = radarOption({ 技术深度: 8, 项目理解: 7, 表达沟通: 9, 临场表现: 6 }) as any;
    expect(option.radar.indicator.map((i: any) => i.name)).toEqual([
      '技术深度', '项目理解', '表达沟通', '临场表现',
    ]);
    expect(option.series[0].data[0].value).toEqual([8, 7, 9, 6]);
  });

  it('roundBarOption 输出 Q 序号与得分', () => {
    const option = roundBarOption([
      { round_no: 1, overall_score: 6.5 },
      { round_no: 2, overall_score: 8 },
    ]) as any;
    expect(option.xAxis.data).toEqual(['Q1', 'Q2']);
    expect(option.series[0].data).toEqual([6.5, 8]);
  });

  it('analyticsOption 按图表类型生成对应系列', () => {
    const rows = [
      { date: '08-01', score: 7 },
      { date: '08-02', score: 8.5 },
    ];
    expect((analyticsOption('line', rows) as any).series[0].type).toBe('line');
    expect((analyticsOption('bar', rows) as any).series[0].type).toBe('bar');
    expect((analyticsOption('pie', rows) as any).series[0].type).toBe('pie');
    expect((analyticsOption('radar', rows) as any).series[0].type).toBe('radar');
  });
});
