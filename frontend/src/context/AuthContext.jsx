import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import httpClient, {
  clearAuthSession,
  getAccessToken,
  getRefreshToken,
  getStoredUserProfile,
  setAuthFailureHandler,
  setAuthSession,
} from '../api/httpClient';

const AuthContext = createContext(null);

export const SEEDED_ROLE_ACCOUNTS = [
  { role: 'listener', label: 'Listener (Alex)', email: 'listener@spotify-intern.local' },
  { role: 'co_dj', label: 'Co-DJ (Pulse)', email: 'codj@spotify-intern.local' },
  { role: 'host', label: 'Host (DJ Nova)', email: 'host@spotify-intern.local' },
  { role: 'admin', label: 'Admin (Studio Admin)', email: 'admin@spotify-intern.local' },
];

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => getStoredUserProfile());
  const [isLoading, setIsLoading] = useState(true);
  const [authError, setAuthError] = useState(null);

  const handleForcedLogout = useCallback(() => {
    setUser(null);
    setAuthError('Your session expired. Please sign in again.');
  }, []);

  useEffect(() => {
    setAuthFailureHandler(handleForcedLogout);
    return () => setAuthFailureHandler(null);
  }, [handleForcedLogout]);

  const refreshProfile = useCallback(async () => {
    const token = getAccessToken();
    if (!token) {
      setUser(null);
      setIsLoading(false);
      return null;
    }
    try {
      const profile = await httpClient.get('/api/v1/auth/me');
      setUser(profile);
      setAuthSession({ user: profile });
      return profile;
    } catch {
      clearAuthSession({ notify: false });
      setUser(null);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshProfile();
  }, [refreshProfile]);

  const login = useCallback(async (email, password) => {
    setAuthError(null);
    const data = await httpClient.post(
      '/api/v1/auth/login',
      { email: String(email).trim(), password },
      { skipAuth: true, skipAuthRefresh: true }
    );
    setAuthSession({
      accessToken: data.accessToken,
      refreshToken: data.refreshToken,
      user: data.user,
    });
    // Load authoritative profile from GET /api/v1/auth/me
    const authoritativeProfile = await httpClient.get('/api/v1/auth/me');
    setUser(authoritativeProfile);
    setAuthSession({ user: authoritativeProfile });
    return authoritativeProfile;
  }, []);

  const register = useCallback(async ({ email, password, displayName, genreAffinity = [] }) => {
    setAuthError(null);
    const data = await httpClient.post(
      '/api/v1/auth/register',
      {
        email: String(email).trim(),
        password,
        displayName: String(displayName).trim(),
        preferences: { genreAffinity },
      },
      { skipAuth: true, skipAuthRefresh: true }
    );
    setAuthSession({
      accessToken: data.accessToken,
      refreshToken: data.refreshToken,
    });
    const authoritativeProfile = await httpClient.get('/api/v1/auth/me');
    setUser(authoritativeProfile);
    setAuthSession({ user: authoritativeProfile });
    return authoritativeProfile;
  }, []);

  const logout = useCallback(async () => {
    const refreshToken = getRefreshToken();
    try {
      if (getAccessToken() && refreshToken) {
        await httpClient.post(
          '/api/v1/auth/logout',
          { refreshToken },
          { skipAuthRefresh: true }
        );
      }
    } catch {
      // Proceed with local session teardown even if network or token already expired
    } finally {
      clearAuthSession({ notify: false });
      setUser(null);
    }
  }, []);

  /**
   * Demo Role Switcher: Authenticates against the real backend POST /api/v1/auth/login
   * using the selected seeded account and explicitly supplied seed password.
   * Never grants client-side privileges or bypasses server-side RBAC.
   */
  const switchDemoAccount = useCallback(
    async (targetEmail, seedPassword) => {
      if (!seedPassword || !String(seedPassword).trim()) {
        throw new Error(
          'Enter the configured SEED_USER_PASSWORD to authenticate as a seeded demo account.'
        );
      }
      return login(targetEmail, seedPassword);
    },
    [login]
  );

  const value = {
    user,
    isAuthenticated: Boolean(user),
    isLoading,
    authError,
    setAuthError,
    login,
    register,
    logout,
    refreshProfile,
    switchDemoAccount,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used inside an AuthProvider');
  }
  return ctx;
}
