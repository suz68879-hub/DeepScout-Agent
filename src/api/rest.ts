/**
 * P3 后端 REST API 客户端（spec §4.1 api/ 层）。
 * 官方 app/base.ts 的 Action= 协议仅服务 RTC 保留接口；P4/P5 页面统一走这里。
 */
import { AIGC_PROXY_HOST } from '@/config';

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
