/**
 * Centralized HTTP client for AI DJ Studio (Module 5).
 * - Uses configured VITE_API_BASE_URL (default: http://localhost:8000)
 * - Attaches Authorization: Bearer <accessToken>
 * - Automatically calls POST /api/v1/auth/refresh on eligible 401 responses
 * - Coalesces concurrent 401 refresh attempts into a single in-flight refresh promise
 * - Prevents infinite refresh loops via _isRetry guard and auth-route exclusion
 * - Clears authentication state and triggers onAuthFailure callback when refresh fails
 * - Never logs tokens, passwords, or sensitive credentials
 */

const DEFAULT_API_BASE_URL = 'http://localhost:8000';

export function getApiBaseUrl() {
  const envUrl =
    typeof import.meta !== 'undefined' &&
    import.meta.env &&
    import.meta.env.VITE_API_BASE_URL
      ? String(import.meta.env.VITE_API_BASE_URL).trim()
      : '';
  const raw = envUrl || DEFAULT_API_BASE_URL;
  return raw.endsWith('/') ? raw.slice(0, -1) : raw;
}

export function resolveAssetUrl(assetPath) {
  if (!assetPath) {
    return '';
  }
  const trimmed = String(assetPath).trim();
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
    return trimmed;
  }
  const normalizedPath = trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
  return `${getApiBaseUrl()}${normalizedPath}`;
}

const STORAGE_KEYS = {
  ACCESS_TOKEN: 'aidj_access_token',
  REFRESH_TOKEN: 'aidj_refresh_token',
  USER_PROFILE: 'aidj_user_profile',
};

let memoryTokens = {
  accessToken: null,
  refreshToken: null,
};

let authFailureCallback = null;
let activeRefreshPromise = null;

function getStorage() {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage;
    }
  } catch {
    // Ignore storage access errors in restricted environments
  }
  return null;
}

export function setAuthFailureHandler(handler) {
  authFailureCallback = typeof handler === 'function' ? handler : null;
}

export function getAccessToken() {
  if (memoryTokens.accessToken) {
    return memoryTokens.accessToken;
  }
  const storage = getStorage();
  if (storage) {
    const stored = storage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
    if (stored) {
      memoryTokens.accessToken = stored;
      return stored;
    }
  }
  return null;
}

export function getRefreshToken() {
  if (memoryTokens.refreshToken) {
    return memoryTokens.refreshToken;
  }
  const storage = getStorage();
  if (storage) {
    const stored = storage.getItem(STORAGE_KEYS.REFRESH_TOKEN);
    if (stored) {
      memoryTokens.refreshToken = stored;
      return stored;
    }
  }
  return null;
}

export function getStoredUserProfile() {
  const storage = getStorage();
  if (!storage) {
    return null;
  }
  try {
    const raw = storage.getItem(STORAGE_KEYS.USER_PROFILE);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setAuthSession({ accessToken, refreshToken, user }) {
  if (accessToken !== undefined) {
    memoryTokens.accessToken = accessToken || null;
  }
  if (refreshToken !== undefined) {
    memoryTokens.refreshToken = refreshToken || null;
  }

  const storage = getStorage();
  if (storage) {
    if (accessToken !== undefined) {
      if (accessToken) {
        storage.setItem(STORAGE_KEYS.ACCESS_TOKEN, accessToken);
      } else {
        storage.removeItem(STORAGE_KEYS.ACCESS_TOKEN);
      }
    }
    if (refreshToken !== undefined) {
      if (refreshToken) {
        storage.setItem(STORAGE_KEYS.REFRESH_TOKEN, refreshToken);
      } else {
        storage.removeItem(STORAGE_KEYS.REFRESH_TOKEN);
      }
    }
    if (user !== undefined) {
      if (user) {
        storage.setItem(STORAGE_KEYS.USER_PROFILE, JSON.stringify(user));
      } else {
        storage.removeItem(STORAGE_KEYS.USER_PROFILE);
      }
    }
  }
}

export function clearAuthSession({ notify = false } = {}) {
  memoryTokens.accessToken = null;
  memoryTokens.refreshToken = null;
  const storage = getStorage();
  if (storage) {
    storage.removeItem(STORAGE_KEYS.ACCESS_TOKEN);
    storage.removeItem(STORAGE_KEYS.REFRESH_TOKEN);
    storage.removeItem(STORAGE_KEYS.USER_PROFILE);
  }
  if (notify && authFailureCallback) {
    authFailureCallback();
  }
}

const NON_REFRESHABLE_PATHS = new Set([
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/refresh',
  '/api/v1/auth/logout',
]);

function isRefreshEligible(path, options) {
  if (options && options._isRetry) {
    return false;
  }
  if (options && options.skipAuthRefresh) {
    return false;
  }
  const cleanPath = String(path).split('?')[0];
  if (NON_REFRESHABLE_PATHS.has(cleanPath)) {
    return false;
  }
  return Boolean(getRefreshToken());
}

async function parseJsonSafe(response) {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

function buildApiError(status, data, fallbackMessage) {
  const errObj = data && data.error ? data.error : null;
  const message =
    (errObj && errObj.message) ||
    (data && data.message) ||
    fallbackMessage ||
    `Request failed with HTTP ${status}`;
  const error = new Error(message);
  error.status = status;
  error.code = (errObj && errObj.code) || 'HTTP_ERROR';
  error.details = (errObj && errObj.details) || null;
  error.correlationId = (data && data.correlationId) || null;
  return error;
}

/**
 * Execute a single coalesced POST /api/v1/auth/refresh call.
 * Concurrent callers awaiting token refresh share the same Promise so the single-use
 * refresh token is rotated exactly once.
 */
export async function refreshAccessToken(fetchImpl = fetch) {
  if (activeRefreshPromise) {
    return activeRefreshPromise;
  }

  const currentRefreshToken = getRefreshToken();
  if (!currentRefreshToken) {
    clearAuthSession({ notify: true });
    throw buildApiError(401, null, 'Session expired. Please sign in again.');
  }

  activeRefreshPromise = (async () => {
    try {
      const response = await fetchImpl(`${getApiBaseUrl()}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({ refreshToken: currentRefreshToken }),
      });

      const data = await parseJsonSafe(response);
      if (!response.ok || !data || !data.accessToken) {
        throw buildApiError(
          response.status || 401,
          data,
          'Refresh token expired or revoked. Please sign in again.'
        );
      }

      setAuthSession({
        accessToken: data.accessToken,
        refreshToken: data.refreshToken || currentRefreshToken,
      });
      return data.accessToken;
    } catch (err) {
      clearAuthSession({ notify: true });
      throw err;
    } finally {
      activeRefreshPromise = null;
    }
  })();

  return activeRefreshPromise;
}

export async function apiRequest(path, options = {}, fetchImpl = fetch) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const url = `${getApiBaseUrl()}${normalizedPath}`;
  const method = (options.method || 'GET').toUpperCase();

  const headers = {
    Accept: 'application/json',
    ...(options.headers || {}),
  };

  if (options.body !== undefined && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  const token = getAccessToken();
  if (token && !options.skipAuth) {
    headers.Authorization = `Bearer ${token}`;
  }

  const fetchOptions = {
    method,
    headers,
  };

  if (options.body !== undefined) {
    fetchOptions.body =
      typeof options.body === 'string'
        ? options.body
        : JSON.stringify(options.body);
  }

  const response = await fetchImpl(url, fetchOptions);

  if (response.status === 401 && isRefreshEligible(normalizedPath, options)) {
    const newAccessToken = await refreshAccessToken(fetchImpl);
    return apiRequest(
      normalizedPath,
      {
        ...options,
        _isRetry: true,
        headers: {
          ...(options.headers || {}),
          Authorization: `Bearer ${newAccessToken}`,
        },
      },
      fetchImpl
    );
  }

  const data = await parseJsonSafe(response);
  if (!response.ok) {
    throw buildApiError(response.status, data);
  }

  return data;
}

export const httpClient = {
  get: (path, options = {}) => apiRequest(path, { ...options, method: 'GET' }),
  post: (path, body, options = {}) =>
    apiRequest(path, { ...options, method: 'POST', body }),
  put: (path, body, options = {}) =>
    apiRequest(path, { ...options, method: 'PUT', body }),
  delete: (path, options = {}) =>
    apiRequest(path, { ...options, method: 'DELETE' }),
};

export default httpClient;
