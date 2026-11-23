import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  apiRequest,
  clearAuthSession,
  getAccessToken,
  getRefreshToken,
  setAuthFailureHandler,
  setAuthSession,
} from './httpClient';

describe('httpClient JWT & Atomic Refresh Interceptor (Module 5)', () => {
  beforeEach(() => {
    clearAuthSession({ notify: false });
    setAuthFailureHandler(null);
  });

  it('attaches Bearer access token to authenticated requests', async () => {
    setAuthSession({
      accessToken: 'access_tok_123',
      refreshToken: 'refresh_tok_123',
    });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ tracks: [], total: 0 }),
    });

    const result = await apiRequest('/api/v1/tracks', { method: 'GET' }, mockFetch);
    expect(result).toEqual({ tracks: [], total: 0 });
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [, calledOpts] = mockFetch.mock.calls[0];
    expect(calledOpts.headers.Authorization).toBe('Bearer access_tok_123');
  });

  it('automatically calls POST /api/v1/auth/refresh on eligible 401 and retries the original request', async () => {
    setAuthSession({
      accessToken: 'expired_access_token',
      refreshToken: 'valid_refresh_token_1',
    });

    const mockFetch = vi
      .fn()
      // 1. First request returns 401
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () =>
          JSON.stringify({ error: { code: 'TOKEN_EXPIRED', message: 'Expired' } }),
      })
      // 2. Refresh call returns rotated tokens
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({
            accessToken: 'new_access_token_2',
            refreshToken: 'new_refresh_token_2',
            expiresIn: 3600,
          }),
      })
      // 3. Retried request succeeds with 200
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ id: 'u_501', role: 'host' }),
      });

    const profile = await apiRequest('/api/v1/auth/me', { method: 'GET' }, mockFetch);
    expect(profile).toEqual({ id: 'u_501', role: 'host' });
    expect(mockFetch).toHaveBeenCalledTimes(3);
    expect(getAccessToken()).toBe('new_access_token_2');
    expect(getRefreshToken()).toBe('new_refresh_token_2');
  });

  it('coalesces concurrent 401 requests into a single refresh request', async () => {
    setAuthSession({
      accessToken: 'expired_access_token',
      refreshToken: 'single_use_refresh_token',
    });

    let refreshCallCount = 0;
    const mockFetch = vi.fn(async (url, opts) => {
      if (url.endsWith('/api/v1/auth/refresh')) {
        refreshCallCount += 1;
        await new Promise((r) => setTimeout(r, 15));
        return {
          ok: true,
          status: 200,
          text: async () =>
            JSON.stringify({
              accessToken: 'rotated_access_token',
              refreshToken: 'rotated_refresh_token',
              expiresIn: 3600,
            }),
        };
      }

      if (opts.headers.Authorization === 'Bearer expired_access_token') {
        return {
          ok: false,
          status: 401,
          text: async () => JSON.stringify({ error: { code: 'UNAUTHORIZED' } }),
        };
      }

      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ ok: true, auth: opts.headers.Authorization }),
      };
    });

    const [res1, res2, res3] = await Promise.all([
      apiRequest('/api/v1/tracks', { method: 'GET' }, mockFetch),
      apiRequest('/api/v1/lounges', { method: 'GET' }, mockFetch),
      apiRequest('/api/v1/auth/me', { method: 'GET' }, mockFetch),
    ]);

    expect(refreshCallCount).toBe(1);
    expect(res1.auth).toBe('Bearer rotated_access_token');
    expect(res2.auth).toBe('Bearer rotated_access_token');
    expect(res3.auth).toBe('Bearer rotated_access_token');
  });

  it('prevents infinite refresh loops and clears session when refresh fails', async () => {
    setAuthSession({
      accessToken: 'expired_access_token',
      refreshToken: 'revoked_refresh_token',
    });

    const onFailure = vi.fn();
    setAuthFailureHandler(onFailure);

    const mockFetch = vi
      .fn()
      // 1. Original call -> 401
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () => JSON.stringify({ error: { code: 'UNAUTHORIZED' } }),
      })
      // 2. Refresh call -> 401
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () =>
          JSON.stringify({
            error: { code: 'REFRESH_TOKEN_REUSED', message: 'Revoked' },
          }),
      });

    await expect(
      apiRequest('/api/v1/tracks', { method: 'GET' }, mockFetch)
    ).rejects.toThrow();

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(onFailure).toHaveBeenCalledTimes(1);
  });
});
