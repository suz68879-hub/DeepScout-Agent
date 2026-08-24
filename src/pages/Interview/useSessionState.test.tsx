// @vitest-environment jsdom
import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSessionState } from './useSessionState';

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => vi.unstubAllGlobals());

describe('useSessionState', () => {
  it('轮询会话状态并更新阶段', async () => {
    const fn = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ session_id: 's1', stage: 'intro', round_no: 0, current_question: null, scores: [] }))
      .mockResolvedValueOnce(jsonResponse({ session_id: 's1', stage: 'technical', round_no: 3, current_question: null, scores: [] }));
    vi.stubGlobal('fetch', fn);
    vi.useFakeTimers();
    const { result } = renderHook(() => useSessionState('s1'));
    await vi.waitFor(() => expect(result.current.state?.stage).toBe('intro'));
    await vi.advanceTimersByTimeAsync(3000);
    await vi.waitFor(() => expect(result.current.state?.stage).toBe('technical'));
    vi.useRealTimers();
  });
});
