import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  getJson,
  getText,
  jobErrorMessage,
  pollJob,
  postFile,
  postForm,
  postJson,
  abandonSession,
} from './rest';
import { AIGC_PROXY_HOST } from '@/config';

const okJson = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const errJson = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), { status, headers: { 'Content-Type': 'application/json' } });

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('rest client', () => {
  it('sends cookies with requests', async () => {
    const fn = vi.fn().mockResolvedValue(okJson({ id: 'u1' }));
    vi.stubGlobal('fetch', fn);
    await getJson('/api/auth/me');
    expect(fn).toHaveBeenCalledWith(
      `${AIGC_PROXY_HOST}/api/auth/me`,
      expect.objectContaining({ credentials: 'include' })
    );
  });

  it('getJson 请求正确 URL 并解析 JSON', async () => {
    const fn = vi.fn().mockResolvedValue(okJson({ id: 'r1' }));
    vi.stubGlobal('fetch', fn);
    await expect(getJson('/api/reports/r1')).resolves.toEqual({ id: 'r1' });
    expect(fn).toHaveBeenCalledWith(
      `${AIGC_PROXY_HOST}/api/reports/r1`,
      { credentials: 'include' }
    );
  });

  it('postJson 发送 JSON body', async () => {
    const fn = vi.fn().mockResolvedValue(okJson({ ok: true }));
    vi.stubGlobal('fetch', fn);
    await postJson('/api/interview/start', { position: 'Java后端' });
    const [, init] = fn.mock.calls[0];
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ position: 'Java后端' });
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(init.headers['Idempotency-Key']).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
    );
  });

  it('abandonSession 以 keepalive POST 回收会话', async () => {
    const fn = vi.fn().mockResolvedValue(okJson({ status: 'abandoned' }));
    vi.stubGlobal('fetch', fn);
    abandonSession('s1');
    await vi.waitFor(() => expect(fn).toHaveBeenCalled());
    const [url, init] = fn.mock.calls[0];
    expect(url).toBe(`${AIGC_PROXY_HOST}/api/interview/abandon`);
    expect(init.method).toBe('POST');
    expect(init.keepalive).toBe(true);
    expect(init.credentials).toBe('include');
    expect(init.headers['Content-Type']).toBe('application/x-www-form-urlencoded');
    expect(init.body).toBe('session_id=s1');
  });

  it('postForm 以 FormData 发送字段', async () => {
    const fn = vi.fn().mockResolvedValue(okJson({ id: 'r2' }));
    vi.stubGlobal('fetch', fn);
    await postForm('/api/resume/upload', { content: 'md 文本', source: 'md' });
    const [, init] = fn.mock.calls[0];
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.body.get('content')).toBe('md 文本');
    expect(init.body.get('source')).toBe('md');
  });

  it('postFile 以 file 字段发送文件', async () => {
    const fn = vi.fn().mockResolvedValue(okJson({ id: 'r3' }));
    vi.stubGlobal('fetch', fn);
    const file = new File(['x'], 'resume.pdf');
    await postFile('/api/resume/upload_pdf', file);
    const [, init] = fn.mock.calls[0];
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.body.get('file')).toBe(file);
  });

  it('getText 返回纯文本', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('# md', { status: 200 })));
    await expect(getText('/api/reports/r1/export.md')).resolves.toBe('# md');
  });

  it('非 2xx 时抛 ApiError 并携带 FastAPI detail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(errJson(422, '简历解析失败：文件格式不受支持')));
    const err = await getJson('/api/resume').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).message).toBe('简历解析失败：文件格式不受支持');
  });

  it('非 JSON 错误响应给出通用文案', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Bad Gateway', { status: 502 })));
    const err = await getJson('/api/reports').catch((e: unknown) => e);
    expect((err as ApiError).message).toContain('502');
  });

  it('以 2 秒起步并指数退避到最多 10 秒，终态后停止轮询', async () => {
    vi.useFakeTimers();
    const statuses = ['pending', 'running', 'running', 'running', 'succeeded'];
    const fn = vi.fn().mockImplementation(() => Promise.resolve(okJson({
      job_id: 'job-1',
      type: 'interview_finish',
      status: statuses.shift(),
      attempt: 1,
      created_at: '2026-08-26T10:00:00+00:00',
      started_at: null,
      finished_at: null,
      result_ref: { report_id: 'report-1' },
      error_code: null,
    })));
    vi.stubGlobal('fetch', fn);

    const result = pollJob('job-1');
    await [2000, 4000, 8000, 10000, 10000].reduce(
      (previous, delay) => previous.then(
        () => vi.advanceTimersByTimeAsync(delay).then(() => undefined)
      ),
      Promise.resolve()
    );

    await expect(result).resolves.toMatchObject({ status: 'succeeded' });
    expect(fn).toHaveBeenCalledTimes(5);
    vi.useRealTimers();
  });

  it('拒绝结构不合法的任务响应，且错误码只映射为安全文案', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson({ status: 'succeeded' })));
    const result = pollJob('job-1');
    const rejected = expect(result).rejects.toThrow('任务响应格式无效');
    await vi.advanceTimersByTimeAsync(2000);
    await rejected;
    expect(jobErrorMessage('MODEL_PROVIDER_SECRET_DETAIL')).toBe('任务处理失败，请稍后重试');
    vi.useRealTimers();
  });

  it('轮询超过 120 秒仍未终态时抛出超时，避免浮层挂死', async () => {
    vi.useFakeTimers();
    const fn = vi.fn().mockImplementation(() => Promise.resolve(okJson({
      job_id: 'job-1',
      type: 'interview_finish',
      status: 'pending',
      attempt: 1,
      created_at: '2026-08-26T10:00:00+00:00',
      started_at: null,
      finished_at: null,
      result_ref: null,
      error_code: null,
    })));
    vi.stubGlobal('fetch', fn);

    const result = pollJob('job-1');
    const rejected = expect(result).rejects.toMatchObject({
      name: 'ApiError',
      status: 408,
      message: '报告生成超时，请稍后在历史记录中查看',
    });
    await vi.advanceTimersByTimeAsync(120_000);
    await rejected;
    vi.useRealTimers();
  });
});
