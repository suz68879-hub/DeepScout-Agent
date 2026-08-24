import type { EChartsOption } from 'echarts';
import type { ChartType } from '@/domain/analytics/types';
import { DIMENSIONS } from '@/domain/report/types';

export const CHART_COLORS = {
  accent: '#2563eb',
  grid: '#e2e8f0',
  text: '#64748b',
  series: ['#2563eb', '#06b6d4', '#16a34a', '#f59e0b'],
};

// spec §2.4：报告雷达图（分维度）
export function radarOption(scores: Record<string, number>): EChartsOption {
  return {
    radar: {
      indicator: DIMENSIONS.map((name) => ({ name, max: 10 })),
      axisName: { color: CHART_COLORS.text },
      splitLine: { lineStyle: { color: [CHART_COLORS.grid] } },
      splitArea: { areaStyle: { color: ['rgba(37, 99, 235, 0.05)'] } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            name: '分维度得分',
            value: DIMENSIONS.map((d) => scores[d] ?? 0),
            areaStyle: { color: 'rgba(37, 99, 235, 0.20)' },
            itemStyle: { color: CHART_COLORS.accent },
            lineStyle: { color: CHART_COLORS.accent },
          },
        ],
      },
    ],
  };
}

// spec §2.4：逐题得分柱状图
export function roundBarOption(rounds: { round_no: number; overall_score: number }[]): EChartsOption {
  return {
    grid: { left: 40, right: 16, top: 32, bottom: 24 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: rounds.map((r) => `Q${r.round_no}`),
      axisLine: { lineStyle: { color: CHART_COLORS.grid } },
      axisLabel: { color: CHART_COLORS.text },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 10,
      splitLine: { lineStyle: { color: CHART_COLORS.grid } },
      axisLabel: { color: CHART_COLORS.text },
    },
    series: [
      {
        type: 'bar',
        data: rounds.map((r) => r.overall_score),
        itemStyle: { color: CHART_COLORS.accent },
        barMaxWidth: 40,
      },
    ],
  };
}

// 自由查询通用渲染：取前两列作 x/y（后端 rows 为扁平表，spec §5.8 可视化）
export function analyticsOption(
  chartType: Exclude<ChartType, 'table'>,
  rows: Record<string, unknown>[]
): EChartsOption {
  const keys = Object.keys(rows[0] ?? {});
  const labels = rows.map((r) => String(r[keys[0]] ?? ''));
  const values = rows.map((r) => Number(r[keys[1]] ?? 0));
  const base = {
    tooltip: { trigger: 'axis' as const },
    xAxis: {
      type: 'category' as const,
      data: labels,
      axisLine: { lineStyle: { color: CHART_COLORS.grid } },
      axisLabel: { color: CHART_COLORS.text },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: CHART_COLORS.grid } },
      axisLabel: { color: CHART_COLORS.text },
    },
  };
  if (chartType === 'line') {
    return {
      ...base,
      series: [{ type: 'line', data: values, itemStyle: { color: CHART_COLORS.accent } }],
    };
  }
  if (chartType === 'bar') {
    return {
      ...base,
      series: [{ type: 'bar', data: values, itemStyle: { color: CHART_COLORS.accent } }],
    };
  }
  if (chartType === 'pie') {
    return {
      tooltip: { trigger: 'item' },
      series: [
        {
          type: 'pie',
          radius: '60%',
          data: rows.map((r, i) => ({ name: labels[i], value: values[i] })),
        },
      ],
    };
  }
  // radar：标签为 indicator，数值取 y 列
  return {
    radar: {
      indicator: labels.map((name) => ({ name, max: Math.max(...values, 10) })),
      axisName: { color: CHART_COLORS.text },
      splitLine: { lineStyle: { color: [CHART_COLORS.grid] } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            name: '查询结果',
            value: values,
            areaStyle: { color: 'rgba(37, 99, 235, 0.20)' },
            itemStyle: { color: CHART_COLORS.accent },
          },
        ],
      },
    ],
  };
}
