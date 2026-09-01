// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider, useAuth } from './AuthContext';

function Probe() {
  const { loading, user } = useAuth();
  if (loading) return <div>loading</div>;
  return <div>{user?.username ?? 'anonymous'}</div>;
}

function RegisterProbe() {
  const { loading, user, register } = useAuth();
  if (loading) return <div>loading</div>;
  return (
    <div>
      <div>{user?.username ?? 'anonymous'}</div>
      <button type="button" onClick={() => void register('alice', 'password-123', 'team-invite')}>
        register
      </button>
    </div>
  );
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

  it('sends the invite code when creating an account', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/api/auth/me')) {
        return new Response(JSON.stringify({ detail: 'authentication required' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({ id: 'u1', username: 'alice' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      });
    });
    render(
      <AuthProvider>
        <RegisterProbe />
      </AuthProvider>
    );
    fireEvent.click(await screen.findByRole('button', { name: 'register' }));
    await waitFor(() => expect(screen.getByText('alice')).toBeTruthy());
    const registerCall = fetchSpy.mock.calls.find(([url]) => String(url).includes('/api/auth/register'));
    expect(registerCall).toBeTruthy();
    expect(JSON.parse(String(registerCall?.[1]?.body))).toEqual({
      username: 'alice',
      password: 'password-123',
      invite_code: 'team-invite',
    });
  });
});
