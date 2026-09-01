import { useEffect, useState } from 'react';
import { Button, Message, Skeleton } from '@arco-design/web-react';
import { useParams } from 'react-router-dom';
import { downloadText, getJson, getText } from '@/api/rest';
import ChartPanel from '@/components/ChartPanel';
import { radarOption, roundBarOption } from '@/components/ChartPanel/options';
import type { ReportRow } from '@/domain/report/types';
import { overallFromReport, parseFeedback, parseScores } from '@/domain/report/types';
import { formatLocalDateTime } from '@/lib/datetime';
import styles from './index.module.less';

function parseSuggestions(json: string | null): string[] {
  if (!json) return [];
  try {
    return JSON.parse(json) as string[];
  } catch {
    return [];
  }
}

export default function ReportPage() {
  const { reportId } = useParams();
  const [report, setReport] = useState<ReportRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    getJson<ReportRow>(`/api/reports/${reportId}`)
      .then(setReport)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '报告加载失败'));
  }, [reportId]);

  const exportMd = async () => {
    setExporting(true);
    try {
      const md = await getText(`/api/reports/${reportId}/export.md`);
      downloadText(`interview-report-${reportId}.md`, md);
    } catch (e) {
      Message.error(e instanceof Error ? e.message : '导出失败');
    } finally {
      setExporting(false);
    }
  };

  if (error) {
    return (
      <div className={styles.error}>
        <span className={styles.errorCode}>ERR_NOT_FOUND</span>
        {error}
      </div>
    );
  }
  if (!report) {
    return (
      <div className={styles.loading}>
        <Skeleton />
      </div>
    );
  }

  const dims = parseScores(report.scores_json);
  const feedback = parseFeedback(report.feedback_json);
  const suggestions = parseSuggestions(report.suggestions_json);
  const bars = feedback.round_details.map((r, i) => ({
    round_no: r.round_no,
    overall_score: feedback.round_scores[i] ?? 0,
  }));

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>
            <span className={styles.prompt}>$</span> 面试报告 — {report.position ?? '未知岗位'}
          </h1>
          <div className={styles.meta}>{formatLocalDateTime(report.created_at)}</div>
        </div>
        <Button className={styles.exportBtn} loading={exporting} onClick={exportMd}>
          导出 MD
        </Button>
      </header>

      <section className={styles.overallCard}>
        <div className={styles.overallLabel}>总评分</div>
        <div className={styles.overallValue}>
          {overallFromReport(report).toFixed(1)}
          <span className={styles.overallUnit}>/10</span>
        </div>
        <div className={styles.dimSummary}>
          {Object.entries(dims).map(([dim, v]) => (
            <span key={dim} className={styles.dimChip}>
              {dim} {v}
            </span>
          ))}
        </div>
      </section>

      <section className={styles.charts}>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>分维度雷达</div>
          <ChartPanel option={radarOption(dims)} />
        </div>
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>逐题得分</div>
          {bars.length ? (
            <ChartPanel option={roundBarOption(bars)} />
          ) : (
            <div className={styles.empty}>暂无逐题数据</div>
          )}
        </div>
      </section>

      <section className={styles.card}>
        <div className={styles.cardTitle}>总体评价</div>
        <p className={styles.summary}>{feedback.summary}</p>
      </section>

      <section className={styles.card}>
        <div className={styles.cardTitle}>逐题记录</div>
        {feedback.round_details.map((r, i) => (
          <div key={r.round_no} className={styles.roundItem}>
            <div className={styles.roundHead}>
              <span className={styles.roundNo}>Q{r.round_no}</span>
              <span className={styles.roundQuestion}>{r.question}</span>
              <span className={styles.roundScore}>
                {feedback.round_scores[i] !== undefined ? feedback.round_scores[i] : ''}
              </span>
            </div>
            <div className={styles.roundBody}>
              <div className={styles.roundKey}>回答要点</div>
              <div>{r.answer_summary}</div>
              <div className={styles.roundKey}>点评</div>
              <div>{r.comment}</div>
            </div>
          </div>
        ))}
      </section>

      {feedback.transcript && feedback.transcript.length ? (
        <section className={styles.card}>
          <details className={styles.transcriptDetails}>
            <summary>
              转写全文（{feedback.transcript.length} 段）
            </summary>
            {feedback.speaker_assignment &&
            feedback.speaker_assignment.confidence === '低' ? (
              <div className={styles.transcriptNote}>
                角色判定备注（{feedback.speaker_assignment.confidence}）：
                {feedback.speaker_assignment.reason}
              </div>
            ) : null}
            <div className={styles.transcript}>
              {feedback.transcript.map((s, i) => (
                <div key={i} className={styles.transcriptLine}>
                  <span className={styles.transcriptSpeaker}>
                    [{s.role || `说话人${s.speaker}`}]
                  </span>
                  <span>{s.text}</span>
                </div>
              ))}
            </div>
          </details>
        </section>
      ) : null}

      <section className={styles.card}>
        <div className={styles.cardTitle}>改进建议（按优先级）</div>
        <ol className={styles.suggestList}>
          {feedback.improvements.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
      </section>

      {suggestions.length ? (
        <section className={styles.card}>
          <div className={styles.cardTitle}>下次练习建议</div>
          <ul className={styles.suggestList}>
            {suggestions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
