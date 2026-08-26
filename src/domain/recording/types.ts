// 录音分析类型（对齐后端 api/recording.py 与 recording 行，spec §12）
export type RecordingStatus = 'processing' | 'done' | 'failed';

export interface RecordingUploadResponse {
  recording_id: string;
  job_id: string;
  status: 'processing';
}

export interface RecordingStatusResponse {
  recording_id: string;
  status: RecordingStatus;
  report_id: string | null;
  error: string | null;
}

export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024; // 与后端 MAX_UPLOAD_BYTES 一致
