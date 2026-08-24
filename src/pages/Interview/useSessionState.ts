import { useEffect, useState } from 'react';
import { ApiError, getJson } from '@/api/rest';
import type { InterviewStateResponse } from '@/domain/interview/types';

const POLL_MS = 3000;

// 轮询后端下发的面试状态（spec §2.3：阶段归属后端，前端仅渲染；评分浮层数据源）
export function useSessionState(sessionId: string) {
  const [state, setState] = useState<InterviewStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await getJson<InterviewStateResponse>(
          `/api/interview/state?session_id=${encodeURIComponent(sessionId)}`
        );
        if (!cancelled) setState(next);
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : '状态获取失败');
      }
    };
    tick();
    const timer = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sessionId]);

  return { state, error };
}
