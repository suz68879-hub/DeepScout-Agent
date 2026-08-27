export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface JobResponse {
  job_id: string;
  type: string;
  status: JobStatus;
  attempt: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  result_ref: Record<string, unknown> | null;
  error_code: string | null;
}

export const isTerminalJob = (status: JobStatus) =>
  status === 'succeeded' || status === 'failed' || status === 'cancelled';
