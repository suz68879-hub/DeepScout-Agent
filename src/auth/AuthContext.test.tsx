// @vitest-environment jsdom
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider, useAuth } from './AuthContext';

function Probe() {
  const { loading, user } = useAuth();
  if (loading) return <div>loading</div>;
  return <div>{user?.username ?? 'anonymous'}</div>;
}

describe('AuthProvider', () => {
  afterEach(() => vi.restoreAllMocks());

  it('restores the current user from the HttpOnly session', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 'u1', username: 'alice' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByText('alice')).toBeTruthy());
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/auth/me'),
      expect.objectContaining({ credentials: 'include' })
    );
  });

  it('treats a 401 session lookup as anonymous', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'authentication required' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByText('anonymous')).toBeTruthy());
  });
});
