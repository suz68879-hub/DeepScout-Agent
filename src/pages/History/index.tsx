import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getJson } from '@/api/rest';
import type { ReportPage, ReportRow } from '@/domain/report/types';
import { overallFromReport } from '@/domain/report/types';
import { formatLocalDateTime } from '@/lib/datetime';
import styles from './index.module.less';

export default function HistoryPage() {
  const navigate = useNavigate();
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);

  const loadReports = useCallback((cursor?: string) => {
    if (cursor) setLoadingMore(true);
    const query = new URLSearchParams({ limit: '20' });
    if (cursor) query.set('cursor', cursor);
    return getJson<ReportPage>(`/api/reports?${query.toString()}`)
      .then((page) => {
        setReports((current) => (cursor ? [...current, ...page.items] : page.items));
        setNextCursor(page.next_cursor);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '历史记录加载失败'))
      .finally(() => {
        setLoading(false);
        setLoadingMore(false);
      });
  }, []);

  useEffect(() => {
    void loadReports();
  }, [loadReports]);

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
                <span className={styles.date}>{formatLocalDateTime(r.created_at)}</span>
                <span className={styles.position}>{r.position ?? '未知岗位'}</span>
                <span className={styles.score}>{overallFromReport(r).toFixed(1)} / 10</span>
                <span className={styles.arrow}>&gt;</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {nextCursor && (
        <button type="button" disabled={loadingMore} onClick={() => void loadReports(nextCursor)}>
          {loadingMore ? '加载中…' : '加载更多'}
        </button>
      )}
    </div>
  );
}
