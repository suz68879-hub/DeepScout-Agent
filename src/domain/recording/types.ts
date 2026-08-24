// 录音分析类型（对齐后端 api/recording.py 与 recording 行，spec §12）
export type RecordingStatus = 'processing' | 'done' | 'failed';

export interface RecordingStatusResponse {
  recording_id: string;
  status: RecordingStatus;
  report_id: string | null;
  error: string | null;
}

export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024; // 与后端 MAX_UPLOAD_BYTES 一致

export const POLL_INTERVAL_MS = 3000; // 状态轮询间隔（测试经 vi.mock 缩短）
