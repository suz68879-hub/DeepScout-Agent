import { useEffect, useRef, useState } from 'react';
import { Button, Message } from '@arco-design/web-react';
import { useNavigate } from 'react-router-dom';
import { getJson, postFileForm, postJson } from '@/api/rest';
import ResumePanel from '@/components/ResumePanel';
import type { StartResponse } from '@/domain/interview/types';
import type { RecordingStatusResponse } from '@/domain/recording/types';
import { MAX_UPLOAD_BYTES, POLL_INTERVAL_MS } from '@/domain/recording/types';
import type { ReportPage, ReportRow } from '@/domain/report/types';
import { overallFromReport } from '@/domain/report/types';
import type { ResumeRow } from '@/domain/resume/types';
import styles from './index.module.less';

const POSITION_PRESETS = ['Java后端', 'AI Agent 应用开发', '后端开发（Go）', '大数据开发'];

export default function HomePage() {
  const navigate = useNavigate();
  const [resume, setResume] = useState<ResumeRow | null>(null);
  const [position, setPosition] = useState(POSITION_PRESETS[0]);
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    getJson<ReportPage>('/api/reports?limit=3')
      .then((page) => setReports(page.items))
      .catch(() => {
        Message.error('获取报告失败，请稍后重试');
        setReports([]);
      });
  }, []);

  const startInterview = async () => {
    setStarting(true);
    try {
      const res = await postJson<StartResponse>('/api/interview/start', {
        position,
        resume_id: resume?.id ?? null,
      });
      navigate(`/interview/${res.session_id}`);
    } catch (e) {
      Message.error(e instanceof Error ? e.message : '创建面试会话失败');
      setStarting(false);
    }
  };

  type RecordingState =
    | { status: 'idle' }
    | { status: 'uploading' }
    | { status: 'processing'; id: string }
    | { status: 'failed'; error: string };

  const [recordingFile, setRecordingFile] = useState<File | null>(null);
  const [recording, setRecording] = useState<RecordingState>({ status: 'idle' });
  const pollTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (pollTimer.current !== null) window.clearInterval(pollTimer.current);
    },
    []
  );

  const stopPolling = () => {
    if (pollTimer.current !== null) window.clearInterval(pollTimer.current);
    pollTimer.current = null;
  };

  const pollRecording = async (id: string) => {
    try {
      const s = await getJson<RecordingStatusResponse>(`/api/recording/${id}`);
      if (s.status === 'done' && s.report_id) {
        stopPolling();
        navigate(`/report/${s.report_id}`);
      } else if (s.status === 'failed') {
        stopPolling();
        setRecording({ status: 'failed', error: s.error ?? '录音分析失败' });
      }
    } catch (e) {
      stopPolling();
      setRecording({ status: 'failed', error: e instanceof Error ? e.message : '录音状态查询失败' });
    }
  };

  const startRecordingAnalysis = async () => {
    if (!recordingFile) {
      Message.warning('请先选择录音文件');
      return;
    }
    if (recordingFile.size > MAX_UPLOAD_BYTES) {
      Message.error('录音超过 200MB 上限');
      return;
    }
    setRecording({ status: 'uploading' });
    try {
      const res = await postFileForm<{ recording_id: string; status: string }>(
        '/api/recording/upload',
        recordingFile,
        { position }
      );
      setRecording({ status: 'processing', id: res.recording_id });
      pollTimer.current = window.setInterval(() => {
        void pollRecording(res.recording_id);
      }, POLL_INTERVAL_MS);
    } catch (e) {
      setRecording({ status: 'failed', error: e instanceof Error ? e.message : '录音上传失败' });
    }
  };

  const recent = reports.slice(0, 3);

  return (
    <div className={styles.page}>
      <section className={styles.hero}>
        <h1 className={styles.title}>
          智能面试，成就未来
        </h1>
        <p className={styles.heroSubtitle}>通过 AI 数字人进行实时视频面试，获得专业的四维评分报告和个性化改进建议</p>
        <div className={styles.features}>
          <div className={styles.feature}><span className={styles.featureIcon}>AI</span><strong>数字人面试</strong><span>模拟真实面试场景与专业追问</span></div>
          <div className={styles.feature}><span className={styles.featureIcon}>4D</span><strong>四维评分报告</strong><span>全面评估技术、项目、表达与临场表现</span></div>
          <div className={styles.feature}><span className={styles.featureIcon}>↑</span><strong>个性化建议</strong><span>根据每轮表现生成针对性练习方案</span></div>
        </div>
      </section>

      <ResumePanel onResume={setResume} />

      <section className={styles.card}>
        <div className={styles.cardTitle}>&gt; 岗位方向</div>
        <div className={styles.chips}>
          {POSITION_PRESETS.map((p) => (
            <button
              key={p}
              type="button"
              className={position === p ? styles.chipActive : styles.chip}
              onClick={() => setPosition(p)}
            >
              {p}
            </button>
          ))}
        </div>
        <div className={styles.startRow}>
          <Button type="primary" size="large" loading={starting} onClick={startInterview}>
            开始面试
          </Button>
          <span className={styles.positionLabel}>目标岗位：{position}</span>
        </div>
      </section>

      <section className={styles.card}>
        <div className={styles.cardTitle}>&gt; 面试录音</div>
        <p className={styles.subtitle}>
          上传真实面试录音，自动转写并生成四维评分报告（mp3 / wav / ogg，≤200MB）
        </p>
        <div className={styles.recordingRow}>
          <input
            type="file"
            accept=".mp3,.wav,.ogg"
            aria-label="选择录音文件"
            className={styles.recordingInput}
            onChange={(e) => setRecordingFile(e.target.files?.[0] ?? null)}
          />
          <Button
            type="primary"
            loading={recording.status === 'uploading'}
            disabled={recording.status === 'processing'}
            onClick={startRecordingAnalysis}
          >
            {recording.status === 'processing' ? '分析中' : '上传并分析'}
          </Button>
        </div>
        {recording.status === 'processing' ? (
          <div className={styles.recordingHint}>转写分析中，完成后自动跳转报告（约 1-3 分钟）</div>
        ) : null}
        {recording.status === 'failed' ? (
          <div className={styles.recordingError}>{recording.error}，点击「上传并分析」重试</div>
        ) : null}
      </section>

      <section className={styles.card}>
        <div className={styles.cardTitle}>$ 最近报告</div>
        {recent.length === 0 ? (
          <div className={styles.empty}>暂无报告，完成第一场面试后在此查看</div>
        ) : (
          <ul className={styles.reportList}>
            {recent.map((r) => {
              return (
                <li key={r.id}>
                  <button
                    type="button"
                    className={styles.reportRow}
                    onClick={() => navigate(`/report/${r.id}`)}
                  >
                    <span className={styles.reportDate}>
                      {r.created_at.slice(0, 16).replace('T', ' ')}
                    </span>
                    <span className={styles.reportPosition}>{r.position ?? '未知岗位'}</span>
                    <span className={styles.reportScore}>{overallFromReport(r).toFixed(1)} / 10</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
