import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useSelector } from 'react-redux';
import { Button, Message, Spin } from '@arco-design/web-react';
import AiAvatarCard from '@/components/AiAvatarCard';
import ScoreOverlay from '@/components/ScoreOverlay';
import StageIndicator from '@/components/StageIndicator';
import { ApiError, jobErrorMessage, pollJob, postJson } from '@/api/rest';
import type { FinishResponse } from '@/domain/interview/types';
import { stageMeta } from '@/domain/interview/stageDisplay';
import { isE2EMode, useE2ESessionDriver } from '@/lib/e2eMock';
import {
  useDeviceState,
  useInitScenes,
  useJoin,
  useLeave,
  useRTC,
} from '@/lib/useCommon';
import CameraArea from '@/pages/MainPage/MainArea/Room/CameraArea';
import Conversation from '@/pages/MainPage/MainArea/Room/Conversation';
import { RootState } from '@/store';
import CameraCloseIcon from '@/assets/img/CameraClose.svg';
import CameraOpenIcon from '@/assets/img/CameraOpen.svg';
import LeaveRoomIcon from '@/assets/img/LeaveRoom.svg';
import MicCloseIcon from '@/assets/img/MicClose.svg';
import MicOpenIcon from '@/assets/img/MicOpen.svg';
import { useSessionState } from './useSessionState';
import styles from './index.module.less';

export default function InterviewPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [joining, join] = useJoin();
  const leaveRoom = useLeave();
  const rtc = useRTC();
  const isJoined = useSelector((state: RootState) => state.room.isJoined);
  const [joinError, setJoinError] = useState<string | null>(null);
  const [hangingUp, setHangingUp] = useState(false);
  const jobStorageKey = `deepscout:interview-job:${sessionId ?? ''}`;
  const savedJobId = sessionId ? localStorage.getItem(jobStorageKey) : null;
  const [pendingJob, setPendingJob] = useState<{ jobId: string; revision: number } | null>(
    savedJobId ? { jobId: savedJobId, revision: 0 } : null
  );
  const { isAudioPublished, isVideoPublished, switchMic, switchCamera } = useDeviceState();
  const { state } = useSessionState(sessionId ?? '');

  const { reinit } = useInitScenes(
    sessionId ?? '',
    () => setJoinError('获取场景配置失败，请重试')
  );

  // P7 E2E：VITE_E2E 模式下的脚本化字幕驱动（非 E2E 构建为 no-op）
  useE2ESessionDriver(sessionId);

  // 场景配置就绪后自动进房（RTC 参数走保留接口 /getScenes，Plan 3 Ruling R3）
  useEffect(() => {
    if (!pendingJob && !isJoined && !joining && rtc.AppId) {
      join().catch((e: unknown) => {
        setJoinError(e instanceof Error ? e.message : '进房失败');
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingJob, isJoined, joining, rtc.AppId]);

  // 离开页面时退房（结束面试必须显式走 /finish，退房不结束会话）
  useEffect(() => () => {
    leaveRoom();
  }, []);

  const retryJoin = async () => {
    setJoinError(null);
    // AppId 未就绪说明场景配置尚未成功（或曾失败），重试须先重拉场景；
    // 成功后 rtc.AppId 变化由既有自动进房 effect 触发 join，避免双 join
    if (!rtc.AppId) {
      await reinit();
      return;
    }
    join().catch((e: unknown) => {
      setJoinError(e instanceof Error ? e.message : '进房失败');
    });
  };

  // 开关失败必须显式反馈（如麦克风被占用 NotReadableError），不能静默吞掉
  const toggleMic = async () => {
    try {
      await switchMic();
    } catch (e) {
      Message.error(e instanceof Error ? `麦克风操作失败：${e.message}` : '麦克风操作失败');
    }
  };

  const toggleCamera = async () => {
    try {
      await switchCamera();
    } catch (e) {
      Message.error(e instanceof Error ? `摄像头操作失败：${e.message}` : '摄像头操作失败');
    }
  };

  const monitorFinish = useCallback(async (jobId: string, signal: AbortSignal) => {
    setHangingUp(true);
    try {
      const job = await pollJob(jobId, signal);
      localStorage.removeItem(jobStorageKey);
      setPendingJob(null);
      const reportId = job.result_ref?.report_id;
      if (job.status === 'succeeded' && typeof reportId === 'string') {
        navigate(`/report/${reportId}`);
      } else {
        Message.error(jobErrorMessage(job.error_code));
        navigate('/');
      }
    } catch (e) {
      if (signal.aborted) return;
      if (e instanceof ApiError && e.status < 500) {
        localStorage.removeItem(jobStorageKey);
        setPendingJob(null);
        navigate('/');
      }
      Message.error('任务查询中断，请检查网络后重试');
    } finally {
      if (!signal.aborted) setHangingUp(false);
    }
  }, [jobStorageKey, navigate]);

  useEffect(() => {
    if (!pendingJob) return undefined;
    leaveRoom();
    const controller = new AbortController();
    void monitorFinish(pendingJob.jobId, controller.signal);
    return () => controller.abort();
  }, [pendingJob, monitorFinish]);

  const requestPolling = (jobId: string) => {
    setPendingJob((current) => ({ jobId, revision: (current?.revision ?? 0) + 1 }));
  };

  const hangup = async () => {
    const existing = localStorage.getItem(jobStorageKey);
    if (existing) {
      requestPolling(existing);
      return;
    }
    setHangingUp(true);
    leaveRoom();
    try {
      const res = await postJson<FinishResponse>('/api/interview/finish', {
        session_id: sessionId ?? '',
      });
      localStorage.setItem(jobStorageKey, res.job_id);
      requestPolling(res.job_id);
    } catch (e) {
      Message.error(e instanceof ApiError && e.status === 409
        ? '面试会话状态已变更，请刷新后重试'
        : '结束面试请求失败，请检查网络后重试');
      setHangingUp(false);
    }
  };

  const latestScore = state && state.scores.length > 0 ? state.scores[state.scores.length - 1] : null;
  const currentStage = stageMeta(state?.stage ?? 'intro');

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.brandMark}>AI</div>
        <div className={styles.titleGroup}>
          <h1>AI 技术面试</h1>
          <span>基于简历的实时模拟面试</span>
        </div>
        <div className={styles.sessionStatus} role="status">
          <span className={styles.statusDot} />
          {isJoined ? '面试进行中' : joining ? '正在进入面试间' : '等待连接'}
        </div>
      </header>

      <StageIndicator stage={state?.stage ?? null} />

      {joinError ? (
        <div className={styles.errorCard}>
          <div className={styles.errorTitle}>ERR_JOIN_FAILED</div>
          <div className={styles.errorDetail}>{joinError}</div>
          <Button onClick={retryJoin}>重试进房</Button>
        </div>
      ) : null}

      {hangingUp ? (
        <div className={styles.hangupMask}>
          <Spin />
          <div className={styles.hangupText}>正在生成面试报告，请稍候…</div>
        </div>
      ) : null}

      <main className={styles.workspace}>
        <section className={styles.interviewerPanel} aria-labelledby="interviewer-heading">
          <div className={styles.panelHeader}>
            <div>
              <span className={styles.eyebrow}>INTERVIEWER</span>
              <h2 id="interviewer-heading">面试官</h2>
            </div>
            <div className={styles.listeningStatus}>
              <span className={styles.statusDot} />
              实时聆听
            </div>
          </div>

          <div className={styles.avatarStage}>
            <div className={styles.avatarHalo}>
              <AiAvatarCard showUserTag={false} showStatus />
            </div>
            <div className={styles.stageCopy}>
              <strong>{currentStage.label}</strong>
              <span>{currentStage.hint}</span>
            </div>
            <div className={styles.selfView} aria-label="我的画面">
              <div className={styles.selfViewLabel}>你</div>
              {isE2EMode() ? (
                // P7 E2E：无 RTC 引擎，CameraArea 挂载会触发 removeLocalVideoPlayer 抛错，用占位替代
                <div className={styles.e2eSelfViewPlaceholder} data-testid="e2e-selfview">
                  摄像头预览
                </div>
              ) : (
                <CameraArea />
              )}
            </div>
          </div>
        </section>

        <aside className={styles.sidePanel}>
          <section className={styles.transcriptCard} aria-labelledby="transcript-heading">
            <div className={styles.cardHeader}>
              <div>
                <span className={styles.eyebrow}>TRANSCRIPT</span>
                <h2 id="transcript-heading">实时对话</h2>
              </div>
              <span className={styles.liveBadge}>实时</span>
            </div>
            <Conversation className={styles.transcript} showSubtitle standalone />
          </section>
          <ScoreOverlay score={latestScore} />
        </aside>
      </main>

      <footer className={styles.controlBar} aria-label="面试设备控制">
        <div className={styles.controlHint}>请保持环境安静，清晰、自然地作答</div>
        <div className={styles.toolbar}>
          <button
            type="button"
            className={`${styles.toolBtn} ${isAudioPublished ? styles.toolActive : ''}`}
            onClick={() => void toggleMic()}
            disabled={!isJoined}
          >
            <img src={isAudioPublished ? MicOpenIcon : MicCloseIcon} alt="" aria-hidden="true" />
            {isAudioPublished ? '麦克风开' : '麦克风关'}
          </button>
          <button
            type="button"
            className={`${styles.toolBtn} ${isVideoPublished ? styles.toolActive : ''}`}
            onClick={() => void toggleCamera()}
            disabled={!isJoined}
          >
            <img src={isVideoPublished ? CameraOpenIcon : CameraCloseIcon} alt="" aria-hidden="true" />
            {isVideoPublished ? '摄像头开' : '摄像头关'}
          </button>
          <button type="button" className={styles.hangupBtn} onClick={hangup} disabled={hangingUp}>
            <img src={LeaveRoomIcon} alt="" aria-hidden="true" />
            结束面试
          </button>
        </div>
        <div className={styles.controlSpacer} />
      </footer>
    </div>
  );
}
