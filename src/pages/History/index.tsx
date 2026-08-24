import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getJson } from '@/api/rest';
import type { ReportRow } from '@/domain/report/types';
import { overallFromReport } from '@/domain/report/types';
import styles from './index.module.less';

export default function HistoryPage() {
  const navigate = useNavigate();
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getJson<ReportRow[]>('/api/reports')
      .then(setReports)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '历史记录加载失败'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className={styles.loading}>加载中…</div>;
  }
  if (error) {
    return <div className={styles.error}>{error}</div>;
  }

  return (
    <div className={styles.page}>
      <h1 className={styles.title}>
        <span className={styles.prompt}>$</span> 面试历史
      </h1>
      {reports.length === 0 ? (
        <div className={styles.empty}>还没有面试记录，去完成第一场面试吧</div>
      ) : (
        <ul className={styles.list}>
          {reports.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                className={styles.row}
                onClick={() => navigate(`/report/${r.id}`)}
              >
                <span className={styles.date}>{r.created_at.slice(0, 16).replace('T', ' ')}</span>
                <span className={styles.position}>{r.position ?? '未知岗位'}</span>
                <span className={styles.score}>{overallFromReport(r).toFixed(1)} / 10</span>
                <span className={styles.arrow}>&gt;</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
