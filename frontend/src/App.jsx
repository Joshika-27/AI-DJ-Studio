import React, { useState } from 'react';
import { AuthProvider, SEEDED_ROLE_ACCOUNTS, useAuth } from './context/AuthContext';
import AdminUploadPage from './pages/AdminUploadPage';
import CatalogPage from './pages/CatalogPage';
import LoginPage from './pages/LoginPage';
import LoungeLobbyPage from './pages/LoungeLobbyPage';
import LoungeWarRoomPage from './pages/LoungeWarRoomPage';

function AuthenticatedShell() {
  const { user, isAuthenticated, isLoading, logout, switchDemoAccount } = useAuth();
  const [activeTab, setActiveTab] = useState('catalog'); // 'catalog' | 'lobby' | 'warroom' | 'admin'
  const [activeLoungeId, setActiveLoungeId] = useState(null);
  const [demoSeedPassword, setDemoSeedPassword] = useState('');
  const [switcherMessage, setSwitcherMessage] = useState(null);

  if (isLoading) {
    return (
      <div className="app-shell">
        <div className="main-container">
          <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
            Initializing AI DJ Studio session...
          </div>
        </div>
      </div>
    );
  }

  if (!isAuthenticated || !user) {
    return (
      <div className="app-shell">
        <header className="top-navbar">
          <div className="brand-group">
            <div className="brand-logo">DJ</div>
            <div>
              <h1 className="brand-title">AI DJ Studio</h1>
              <div className="brand-subtitle">
                Collaborative Live Queue Curation &amp; Sentiment-Aware Audio Engine
              </div>
            </div>
          </div>
        </header>
        <main className="main-container">
          <LoginPage />
        </main>
      </div>
    );
  }

  const handleEnterLounge = (loungeId) => {
    setActiveLoungeId(loungeId);
    setActiveTab('warroom');
  };

  const handleQuickRoleSwitch = async (targetEmail) => {
    setSwitcherMessage(null);
    try {
      await switchDemoAccount(targetEmail, demoSeedPassword);
    } catch (err) {
      setSwitcherMessage(err.message || 'Demo account switch failed.');
    }
  };

  return (
    <div className="app-shell">
      <header className="top-navbar">
        <div className="brand-group">
          <div className="brand-logo">DJ</div>
          <div>
            <h1 className="brand-title">AI DJ Studio</h1>
            <div className="brand-subtitle">
              Track A Full-Stack • Server-Enforced 4-Role JWT RBAC
            </div>
          </div>
        </div>

        <nav className="nav-tabs" aria-label="Main navigation">
          <button
            type="button"
            className={`nav-tab ${activeTab === 'catalog' ? 'active' : ''}`}
            onClick={() => setActiveTab('catalog')}
          >
            Track Catalog
          </button>
          <button
            type="button"
            className={`nav-tab ${activeTab === 'lobby' ? 'active' : ''}`}
            onClick={() => setActiveTab('lobby')}
          >
            DJ Lounge Lobby
          </button>
          {activeLoungeId && (
            <button
              type="button"
              className={`nav-tab ${activeTab === 'warroom' ? 'active' : ''}`}
              onClick={() => setActiveTab('warroom')}
            >
              Active Lounge ({activeLoungeId})
            </button>
          )}
          {user.role === 'admin' && (
            <button
              type="button"
              className={`nav-tab ${activeTab === 'admin' ? 'active' : ''}`}
              onClick={() => setActiveTab('admin')}
            >
              Admin Acoustic Upload
            </button>
          )}
        </nav>

        <div className="user-status-bar">
          <div>
            <span style={{ fontWeight: 600, fontSize: '0.88rem' }}>
              {user.displayName}
            </span>{' '}
            <span className={`role-badge role-${user.role}`}>{user.role}</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <input
              type="password"
              className="form-input"
              placeholder="Seed PW for Role Switch"
              value={demoSeedPassword}
              onChange={(e) => setDemoSeedPassword(e.target.value)}
              style={{ width: '165px', padding: '0.3rem 0.5rem', fontSize: '0.76rem' }}
              aria-label="Seed password for real backend role switch"
            />
            <select
              className="form-select"
              value={user.email}
              onChange={(e) => handleQuickRoleSwitch(e.target.value)}
              style={{ padding: '0.3rem 0.5rem', fontSize: '0.76rem' }}
              aria-label="Authenticate as seeded role account"
            >
              {SEEDED_ROLE_ACCOUNTS.map((acct) => (
                <option key={acct.email} value={acct.email}>
                  Switch Auth: {acct.role.toUpperCase()}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            className="btn btn-secondary"
            onClick={logout}
          >
            Sign Out
          </button>
        </div>
      </header>

      <main className="main-container">
        {switcherMessage && (
          <div className="alert-banner alert-error" role="alert">
            {switcherMessage}
          </div>
        )}

        {activeTab === 'catalog' && <CatalogPage />}
        {activeTab === 'lobby' && (
          <LoungeLobbyPage onEnterLounge={handleEnterLounge} />
        )}
        {activeTab === 'warroom' && (
          <LoungeWarRoomPage
            loungeId={activeLoungeId}
            onBackToLobby={() => setActiveTab('lobby')}
          />
        )}
        {activeTab === 'admin' && (
          <AdminUploadPage onTrackCreated={() => setActiveTab('catalog')} />
        )}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AuthenticatedShell />
    </AuthProvider>
  );
}
