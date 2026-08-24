import { useState } from 'react';
import { Button, Input, Message } from '@arco-design/web-react';
import type { EChartsOption } from 'echarts';
import { postJson } from '@/api/rest';
import ChartPanel from '@/components/ChartPanel';
import { analyticsOption, radarOption } from '@/components/ChartPanel/options';
import type { AnalyticsResult } from '@/domain/analytics/types';
import { TEMPLATE_QUERIES } from '@/domain/analytics/types';
import { dimensionRadar, overallTrend, questionTypePie, weaknessBars } from './rowsToChart';
import styles from './index.module.less';

function ResultView({ result }: { result: AnalyticsResult }) {
  const { chart_type: chartType, rows } = result;
  if (rows.length === 0) {
    return <div className={styles.empty}>查询无结果</div>;
  }
  const hasScores = rows.some((r) => r.scores_json !== undefined);
  const hasFeedback = rows.some((r) => r.feedback_json !== undefined);

  let option: EChartsOption | null = null;
  if (hasScores && chartType === 'line') option = analyticsOption('line', overallTrend(rows));
  else if (hasScores && chartType === 'radar') option = radarOption(dimensionRadar(rows));
  else if (hasFeedback && chartType === 'bar') option = analyticsOption('bar', weaknessBars(rows));
  else if (hasFeedback && chartType === 'pie') option = analyticsOption('pie', questionTypePie(rows));
  else if (chartType !== 'table') option = analyticsOption(chartType, rows);

  if (option) {
    return <ChartPanel option={option} />;
  }

  const keys = Object.keys(rows[0]);
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          {keys.map((k) => (
            <th key={k}>{k}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {keys.map((k) => (
              <td key={k}>{String(row[k])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function AnalyticsPage() {
  const [question, setQuestion] = useState('');
  const [querying, setQuerying] = useState(false);
  const [result, setResult] = useState<AnalyticsResult | null>(null);

  const runQuery = async (q: string) => {
    if (!q.trim()) {
      Message.warning('请输入查询问题');
      return;
    }
    setQuerying(true);
    try {
      const res = await postJson<AnalyticsResult>('/api/analytics/query', { question: q });
      setResult(res);
    } catch (e) {
      Message.error(e instanceof Error ? e.message : '查询失败');
    } finally {
      setQuerying(false);
    }
  };

  return (
    <div className={styles.page}>
      <h1 className={styles.title}>
        <span className={styles.prompt}>$</span> 数据分析
      </h1>
      <div className={styles.queryRow}>
        <Input
          className={styles.queryInput}
          placeholder="用自然语言描述想分析的问题，如：最近 5 次面试的总评分趋势"
          value={question}
          onChange={(v) => setQuestion(v)}
          onPressEnter={() => runQuery(question)}
        />
        <Button type="primary" loading={querying} onClick={() => runQuery(question)}>
          查询
        </Button>
      </div>
      <div className={styles.templates}>
        {TEMPLATE_QUERIES.map((t) => (
          <button
            key={t.label}
            type="button"
            className={styles.templateBtn}
            onClick={() => runQuery(t.question)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {result ? (
        <div className={styles.result}>
          <div className={styles.sqlBlock}>
            <div className={styles.sqlLabel}>&gt; SQL</div>
            <code>{result.sql}</code>
          </div>
          <div className={styles.explain}>{result.explanation}</div>
          <ResultView result={result} />
        </div>
      ) : null}
    </div>
  );
}
