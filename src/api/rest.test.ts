import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, getJson, getText, postFile, postForm, postJson } from './rest';
import { AIGC_PROXY_HOST } from '@/config';

const okJson = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const errJson = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), { status, headers: { 'Content-Type': 'application/json' } });

afterEach(() => vi.unstubAllGlobals());

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
});
