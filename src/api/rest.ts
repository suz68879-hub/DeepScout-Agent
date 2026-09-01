/**
 * P3 后端 REST API 客户端（spec §4.1 api/ 层）。
 * 官方 app/base.ts 的 Action= 协议仅服务 RTC 保留接口；P4/P5 页面统一走这里。
 */
import { AIGC_PROXY_HOST } from '@/config';
import type { JobResponse, JobStatus } from '@/domain/jobs/types';
import { isTerminalJob } from '@/domain/jobs/types';

export class ApiError extends Error {
  status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
  }
}

const BASE = AIGC_PROXY_HOST;

async function parseDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (body && typeof body.detail === 'string') {
      return body.detail;
    }
  } catch {
    // 非 JSON 响应（如网关 502 HTML）走通用文案
  }
  return `请求失败（HTTP ${res.status}）`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { ...init, credentials: 'include' });
  if (!res.ok) {
    if (res.status === 401 && path !== '/api/auth/me') {
      window.dispatchEvent(new Event('auth:unauthorized'));
    }
    throw new ApiError(res.status, await parseDetail(res));
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

const JOB_STATUSES: JobStatus[] = ['pending', 'running', 'succeeded', 'failed', 'cancelled'];
const JOB_POLL_INITIAL_MS = 2000;
const JOB_POLL_MAX_MS = 10000;

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function parseJobResponse(value: unknown): JobResponse {
  if (!value || typeof value !== 'object') throw new Error('任务响应格式无效');
  const job = value as Record<string, unknown>;
  if (
    typeof job.job_id !== 'string'
    || typeof job.type !== 'string'
    || typeof job.status !== 'string'
    || !JOB_STATUSES.includes(job.status as JobStatus)
    || typeof job.attempt !== 'number'
    || typeof job.created_at !== 'string'
    || !isNullableString(job.started_at)
    || !isNullableString(job.finished_at)
    || !(job.result_ref === null || (typeof job.result_ref === 'object' && !Array.isArray(job.result_ref)))
    || !isNullableString(job.error_code)
  ) {
    throw new Error('任务响应格式无效');
  }
  return job as unknown as JobResponse;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    let timer: ReturnType<typeof globalThis.setTimeout>;
    const onAbort = () => {
      globalThis.clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    timer = globalThis.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

export async function getJob(jobId: string, signal?: AbortSignal): Promise<JobResponse> {
  return parseJobResponse(await request<unknown>(`/api/jobs/${encodeURIComponent(jobId)}`, { signal }));
}

async function pollJobAfter(
  jobId: string,
  delay: number,
  signal?: AbortSignal
): Promise<JobResponse> {
  await wait(delay, signal);
  const job = await getJob(jobId, signal);
  if (isTerminalJob(job.status)) return job;
  return pollJobAfter(jobId, Math.min(delay * 2, JOB_POLL_MAX_MS), signal);
}

export function pollJob(jobId: string, signal?: AbortSignal): Promise<JobResponse> {
  return pollJobAfter(jobId, JOB_POLL_INITIAL_MS, signal);
}

const JOB_ERROR_MESSAGES: Record<string, string> = {
  INVALID_INPUT: '任务参数无效，请重新提交',
  PERMISSION_DENIED: '无权处理该任务',
  SECURITY_ERROR: '任务未通过安全校验',
  NETWORK_ERROR: '外部服务暂时不可用，请稍后重试',
  RATE_LIMITED: '服务繁忙，请稍后重试',
  PROVIDER_ERROR: 'AI 服务暂时不可用，请稍后重试',
  WORKER_LOST: '任务执行中断，请稍后重试',
  INTERNAL_ERROR: '任务处理失败，请稍后重试',
  MAX_ATTEMPTS_EXCEEDED: '任务多次重试后仍未完成',
};

export function jobErrorMessage(errorCode: string | null): string {
  return (errorCode && JOB_ERROR_MESSAGES[errorCode]) || '任务处理失败，请稍后重试';
}

export function getJson<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function abandonSession(sessionId: string): void {
  void fetch(`${BASE}/api/interview/abandon`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ session_id: sessionId }).toString(),
    keepalive: true,
  }).catch(() => undefined);
}

export async function postForm<T>(path: string, form: Record<string, string>): Promise<T> {
  const fd = new FormData();
  Object.entries(form).forEach(([k, v]) => fd.append(k, v));
  return request<T>(path, { method: 'POST', body: fd });
}

export async function postFile<T>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append('file', file);
  return request<T>(path, { method: 'POST', body: fd });
}

export async function postFileForm<T>(
  path: string,
  file: File,
  fields: Record<string, string>
): Promise<T> {
  const fd = new FormData();
  fd.append('file', file);
  Object.entries(fields).forEach(([k, v]) => fd.append(k, v));
  return request<T>(path, { method: 'POST', body: fd });
}

export async function getText(path: string): Promise<string> {
  const res = await fetch(`${BASE}${path}`, { credentials: 'include' });
  if (!res.ok) {
    throw new ApiError(res.status, await parseDetail(res));
  }
  return res.text();
}

export function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
