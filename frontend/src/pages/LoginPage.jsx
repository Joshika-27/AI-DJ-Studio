import React, { useState } from 'react';
import { SEEDED_ROLE_ACCOUNTS, useAuth } from '../context/AuthContext';

export default function LoginPage() {
  const { login, register, authError, setAuthError } = useAuth();
  const [mode, setMode] = useState('login'); // 'login' | 'register'
  const [email, setEmail] = useState('host@spotify-intern.local');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [genresInput, setGenresInput] = useState('High-Energy EDM, Synthwave');
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLocalError(null);
    setAuthError(null);

    if (!email.trim() || !password) {
      setLocalError('Email and password are required.');
      return;
    }

    setSubmitting(true);
    try {
      if (mode === 'login') {
        await login(email, password);
      } else {
        if (!displayName.trim()) {
          setLocalError('Display name is required for registration.');
          setSubmitting(false);
          return;
        }
        const genreAffinity = genresInput
          .split(',')
          .map((g) => g.trim())
          .filter(Boolean);
        await register({
          email,
          password,
          displayName,
          genreAffinity,
        });
      }
    } catch (err) {
      setLocalError(err.message || 'Authentication failed.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleSelectSeededEmail = (seedEmail) => {
    setMode('login');
    setEmail(seedEmail);
    setLocalError(null);
  };

  return (
    <div style={{ maxWidth: '500px', margin: '2.5rem auto' }}>
      <div className="card">
        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.25rem' }}>
          <button
            type="button"
            className={`nav-tab ${mode === 'login' ? 'active' : ''}`}
            style={{ flex: 1 }}
            onClick={() => {
              setMode('login');
              setLocalError(null);
            }}
          >
            Sign In
          </button>
          <button
            type="button"
            className={`nav-tab ${mode === 'register' ? 'active' : ''}`}
            style={{ flex: 1 }}
            onClick={() => {
              setMode('register');
              setLocalError(null);
            }}
          >
            Create Account
          </button>
        </div>

        <h2 style={{ marginTop: 0, marginBottom: '0.35rem' }}>
          {mode === 'login' ? 'Welcome to AI DJ Studio' : 'Register Listener Account'}
        </h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.86rem', marginTop: 0 }}>
          {mode === 'login'
            ? 'Sign in with your account credentials. Server-side JWT RBAC enforces your authoritative role.'
            : 'Public registration creates a verified listener account (F-01 security policy).'}
        </p>

        {(localError || authError) && (
          <div className="alert-banner alert-error" role="alert">
            {localError || authError}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          {mode === 'register' && (
            <div className="form-group">
              <label htmlFor="auth-display-name">Display Name</label>
              <input
                id="auth-display-name"
                className="form-input"
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="DJ Nova"
                required
              />
            </div>
          )}

          <div className="form-group">
            <label htmlFor="auth-email">Email Address</label>
            <input
              id="auth-email"
              className="form-input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="host@spotify-intern.local"
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="auth-password">Password</label>
            <input
              id="auth-password"
              className="form-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password (min 8 chars)"
              required
            />
          </div>

          {mode === 'register' && (
            <div className="form-group">
              <label htmlFor="auth-genres">Genre Affinity (comma-separated)</label>
              <input
                id="auth-genres"
                className="form-input"
                type="text"
                value={genresInput}
                onChange={(e) => setGenresInput(e.target.value)}
                placeholder="High-Energy EDM, Synthwave"
              />
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary"
            style={{ width: '100%', marginTop: '0.5rem' }}
            disabled={submitting}
          >
            {submitting
              ? 'Authenticating...'
              : mode === 'login'
              ? 'Sign In'
              : 'Create Listener Account'}
          </button>
        </form>

        <div
          style={{
            marginTop: '1.5rem',
            paddingTop: '1rem',
            borderTop: '1px solid var(--border-subtle)',
          }}
        >
          <div
            style={{
              fontSize: '0.78rem',
              color: 'var(--text-secondary)',
              marginBottom: '0.55rem',
              fontWeight: 600,
            }}
          >
            Quick-Fill Seeded Role Email (Requires SEED_USER_PASSWORD to authenticate):
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.45rem' }}>
            {SEEDED_ROLE_ACCOUNTS.map((acct) => (
              <button
                key={acct.email}
                type="button"
                className="btn btn-secondary"
                style={{ fontSize: '0.76rem', padding: '0.35rem 0.65rem' }}
                onClick={() => handleSelectSeededEmail(acct.email)}
              >
                {acct.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
