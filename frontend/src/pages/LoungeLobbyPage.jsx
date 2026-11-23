import React, { useCallback, useEffect, useState } from 'react';
import httpClient from '../api/httpClient';
import { useAuth } from '../context/AuthContext';

export default function LoungeLobbyPage({ onEnterLounge }) {
  const { user } = useAuth();
  const [lounges, setLounges] = useState([]);
  const [tracks, setTracks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [inviteCodeInput, setInviteCodeInput] = useState('');
  const [joining, setJoining] = useState(false);

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [seedTrackId, setSeedTrackId] = useState('t_2001');
  const [vibeProfile, setVibeProfile] = useState('High-Energy EDM');
  const [maxParticipants, setMaxParticipants] = useState(40);
  const [bpmTolerancePct, setBpmTolerancePct] = useState(8);
  const [soundclashEnabled, setSoundclashEnabled] = useState(true);
  const [aiVerbosity, setAiVerbosity] = useState('normal');
  const [creating, setCreating] = useState(false);
  const [createResult, setCreateResult] = useState(null);

  const canCreateLounge = user?.role === 'host' || user?.role === 'admin';

  const loadLobbyData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [loungeData, trackData] = await Promise.all([
        httpClient.get('/api/v1/lounges'),
        httpClient.get('/api/v1/tracks'),
      ]);
      setLounges(Array.isArray(loungeData?.lounges) ? loungeData.lounges : []);
      const catalog = Array.isArray(trackData?.tracks) ? trackData.tracks : [];
      setTracks(catalog);
      if (catalog.length > 0 && !seedTrackId) {
        setSeedTrackId(catalog[0].trackId);
      }
    } catch (err) {
      setError(err.message || 'Failed to load DJ Lounge lobby.');
    } finally {
      setLoading(false);
    }
  }, [seedTrackId]);

  useEffect(() => {
    loadLobbyData();
  }, [loadLobbyData]);

  const handleJoinByCode = async (codeToJoin) => {
    const code = String(codeToJoin || inviteCodeInput).trim().toUpperCase();
    if (!code) {
      setError('Enter a valid invite code (e.g. SP-9X2L) to join.');
      return;
    }
    setJoining(true);
    setError(null);
    try {
      const joined = await httpClient.post(`/api/v1/lounges/join/${encodeURIComponent(code)}`);
      if (onEnterLounge) {
        onEnterLounge(joined.loungeId);
      }
    } catch (err) {
      setError(err.message || 'Unable to join lounge.');
    } finally {
      setJoining(false);
    }
  };

  const handleCreateLounge = async (e) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    setCreateResult(null);
    try {
      const created = await httpClient.post('/api/v1/lounges', {
        seedTrackId,
        vibeProfile: vibeProfile.trim(),
        maxParticipants: Number(maxParticipants),
        bpmTolerancePct: Number(bpmTolerancePct),
        soundclashEnabled: Boolean(soundclashEnabled),
        aiVerbosity,
      });
      setCreateResult(created);
      setShowCreateModal(false);
      await loadLobbyData();
      if (onEnterLounge) {
        onEnterLounge(created.loungeId);
      }
    } catch (err) {
      setError(err.message || 'Failed to create DJ Lounge.');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div>
      <div
        className="card"
        style={{
          marginBottom: '1.35rem',
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '1rem',
        }}
      >
        <div>
          <h2 style={{ margin: '0 0 0.25rem 0' }}>DJ Lounge Lobby</h2>
          <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '0.88rem' }}>
            Join an active collaborative listening room via invite code or launch a new AI-orchestrated DJ Lounge.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            className="form-input"
            type="text"
            placeholder="Invite Code (e.g. SP-9X2L)"
            value={inviteCodeInput}
            onChange={(e) => setInviteCodeInput(e.target.value.toUpperCase())}
            style={{ width: '210px', fontFamily: 'monospace' }}
            aria-label="Invite Code"
          />
          <button
            type="button"
            className="btn btn-primary"
            disabled={joining}
            onClick={() => handleJoinByCode(inviteCodeInput)}
          >
            {joining ? 'Joining...' : 'Join via Code'}
          </button>

          {canCreateLounge && (
            <button
              type="button"
              className="btn btn-secondary"
              style={{ borderColor: 'var(--accent-emerald)', color: 'var(--accent-emerald)' }}
              onClick={() => setShowCreateModal(true)}
            >
              + Create DJ Lounge
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="alert-banner alert-error" role="alert">
          {error}
        </div>
      )}

      {createResult && (
        <div className="alert-banner alert-success" role="status">
          Created Lounge <code>{createResult.loungeId}</code> • Invite Code:{' '}
          <strong>{createResult.inviteCode}</strong> • Status: {createResult.status}
        </div>
      )}

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '2.5rem' }}>
          Loading DJ Lounges...
        </div>
      ) : lounges.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '2.5rem' }}>
          <h3 style={{ marginTop: 0 }}>No DJ Lounges Created Yet</h3>
          <p style={{ color: 'var(--text-secondary)' }}>
            {canCreateLounge
              ? 'Click "+ Create DJ Lounge" above to start the first room.'
              : 'Ask a Host or Admin to create a DJ Lounge and share the SP-XXXX invite code.'}
          </p>
        </div>
      ) : (
        <div className="card-grid">
          {lounges.map((lg) => (
            <div key={lg.loungeId} className="card">
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'flex-start',
                  gap: '0.5rem',
                }}
              >
                <div>
                  <div
                    style={{
                      fontSize: '0.75rem',
                      color: 'var(--text-secondary)',
                      fontFamily: 'monospace',
                    }}
                  >
                    {lg.loungeId} • Host: {lg.hostDisplayName}
                  </div>
                  <h3 style={{ margin: '0.2rem 0' }}>{lg.vibeProfile}</h3>
                </div>

                <span
                  className="pill"
                  style={{
                    color:
                      lg.status === 'live'
                        ? 'var(--accent-emerald)'
                        : lg.status === 'scheduled'
                        ? 'var(--accent-cyan)'
                        : 'var(--text-secondary)',
                  }}
                >
                  {lg.status.toUpperCase()}
                </span>
              </div>

              <div
                style={{
                  marginTop: '0.75rem',
                  fontSize: '0.84rem',
                  color: 'var(--text-secondary)',
                }}
              >
                <div>
                  Invite Code: <strong style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{lg.inviteCode}</strong>
                </div>
                <div>
                  Participants: <strong>{lg.participantCount}</strong> / {lg.maxParticipants} (Peak: {lg.peakParticipants})
                </div>
                <div>
                  Seed / Active Track:{' '}
                  <strong>
                    {lg.nowPlayingTitle
                      ? `${lg.nowPlayingTitle} — ${lg.nowPlayingArtist}`
                      : lg.currentTrackId || 'None'}
                  </strong>
                </div>
                <div>
                  Rules: ±{lg.settings?.bpmTolerancePct}% BPM • AI Verbosity: {lg.settings?.aiVerbosity} •{' '}
                  {lg.isLocked ? '🔒 Locked' : '🔓 Open'}
                </div>
              </div>

              <div
                style={{
                  marginTop: '1rem',
                  display: 'flex',
                  gap: '0.5rem',
                  justifyContent: 'flex-end',
                }}
              >
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => onEnterLounge && onEnterLounge(lg.loungeId)}
                >
                  Inspect Room
                </button>
                {lg.status !== 'ended' && (
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={joining}
                    onClick={() => handleJoinByCode(lg.inviteCode)}
                  >
                    Join Room
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreateModal && (
        <div
          className="modal-backdrop"
          onClick={() => setShowCreateModal(false)}
          role="dialog"
          aria-modal="true"
        >
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>Create New DJ Lounge (PRD Section 7.3.1)</h3>
            <form onSubmit={handleCreateLounge}>
              <div className="form-group">
                <label htmlFor="lounge-seed-track">Seed Track *</label>
                <select
                  id="lounge-seed-track"
                  className="form-select"
                  value={seedTrackId}
                  onChange={(e) => setSeedTrackId(e.target.value)}
                  required
                >
                  {tracks.map((t) => (
                    <option key={t.trackId} value={t.trackId}>
                      {t.trackId} — {t.title} ({t.artist}) [{t.acoustic?.bpm} BPM • {t.acoustic?.camelotKey}]
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="lounge-vibe-profile">Vibe Profile *</label>
                <input
                  id="lounge-vibe-profile"
                  className="form-input"
                  type="text"
                  value={vibeProfile}
                  onChange={(e) => setVibeProfile(e.target.value)}
                  placeholder="High-Energy EDM"
                  required
                />
              </div>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr 1fr',
                  gap: '0.75rem',
                }}
              >
                <div className="form-group">
                  <label htmlFor="lounge-max-participants">Max Participants (2–50)</label>
                  <input
                    id="lounge-max-participants"
                    className="form-input"
                    type="number"
                    min="2"
                    max="50"
                    value={maxParticipants}
                    onChange={(e) => setMaxParticipants(e.target.value)}
                    required
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="lounge-bpm-tolerance">BPM Tolerance %</label>
                  <input
                    id="lounge-bpm-tolerance"
                    className="form-input"
                    type="number"
                    min="1"
                    max="50"
                    step="0.5"
                    value={bpmTolerancePct}
                    onChange={(e) => setBpmTolerancePct(e.target.value)}
                    required
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="lounge-ai-verbosity">AI DJ Verbosity</label>
                  <select
                    id="lounge-ai-verbosity"
                    className="form-select"
                    value={aiVerbosity}
                    onChange={(e) => setAiVerbosity(e.target.value)}
                  >
                    <option value="low">low</option>
                    <option value="normal">normal</option>
                    <option value="high">high</option>
                  </select>
                </div>
              </div>

              <div className="form-group" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.5rem' }}>
                <input
                  id="lounge-soundclash-enabled"
                  type="checkbox"
                  checked={soundclashEnabled}
                  onChange={(e) => setSoundclashEnabled(e.target.checked)}
                />
                <label htmlFor="lounge-soundclash-enabled" style={{ margin: 0 }}>
                  Enable Crowd Soundclash Queue Battles
                </label>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.6rem', marginTop: '1rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowCreateModal(false)}
                >
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={creating}>
                  {creating ? 'Creating Lounge...' : 'Create DJ Lounge'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
