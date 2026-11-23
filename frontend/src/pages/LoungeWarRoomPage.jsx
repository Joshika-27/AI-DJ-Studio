import React, { useCallback, useEffect, useState } from 'react';
import httpClient, { resolveAssetUrl } from '../api/httpClient';
import HostModerationBar from '../components/lounge/HostModerationBar';
import { useAuth } from '../context/AuthContext';

export default function LoungeWarRoomPage({ loungeId, onBackToLobby }) {
  const { user } = useAuth();
  const [lounge, setLounge] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchLoungeDetail = useCallback(async () => {
    if (!loungeId) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const detail = await httpClient.get(`/api/v1/lounges/${loungeId}`);
      setLounge(detail);
    } catch (err) {
      setError(err.message || 'Failed to load DJ Lounge details.');
    } finally {
      setLoading(false);
    }
  }, [loungeId]);

  useEffect(() => {
    fetchLoungeDetail();
  }, [fetchLoungeDetail]);

  if (!loungeId) {
    return (
      <div className="card">
        <p>Select or join a DJ Lounge from the Lobby first.</p>
        <button type="button" className="btn btn-secondary" onClick={onBackToLobby}>
          Back to Lobby
        </button>
      </div>
    );
  }

  if (loading && !lounge) {
    return (
      <div className="card" style={{ textAlign: 'center', padding: '2.5rem' }}>
        Loading DJ Lounge War Room...
      </div>
    );
  }

  return (
    <div>
      <div
        className="card"
        style={{
          marginBottom: '1.25rem',
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '1rem',
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ padding: '0.3rem 0.65rem', fontSize: '0.8rem' }}
              onClick={onBackToLobby}
            >
              ← Lobby
            </button>
            <span
              style={{
                fontFamily: 'monospace',
                fontSize: '0.8rem',
                color: 'var(--text-secondary)',
              }}
            >
              {lounge?.loungeId}
            </span>
            <span className="pill" style={{ color: 'var(--accent-emerald)' }}>
              {lounge?.status?.toUpperCase()}
            </span>
            {lounge?.isLocked && (
              <span className="pill" style={{ color: 'var(--accent-amber)' }}>
                🔒 LOCKED
              </span>
            )}
          </div>
          <h2 style={{ margin: '0.35rem 0 0.15rem 0' }}>{lounge?.vibeProfile}</h2>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
            Host: <strong>{lounge?.hostDisplayName}</strong> ({lounge?.hostUserId}) • Invite Code:{' '}
            <strong style={{ color: 'var(--accent-cyan)', fontFamily: 'monospace' }}>
              {lounge?.inviteCode}
            </strong>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <div className="pill">
            Participants: {lounge?.participantCount} / {lounge?.maxParticipants} (Peak: {lounge?.peakParticipants})
          </div>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={fetchLoungeDetail}
          >
            Refresh State
          </button>
        </div>
      </div>

      {error && (
        <div className="alert-banner alert-error" role="alert">
          {error}
        </div>
      )}

      {lounge && (
        <>
          <HostModerationBar
            lounge={lounge}
            currentUser={user}
            onLoungeUpdated={fetchLoungeDetail}
          />

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
              gap: '1.25rem',
            }}
          >
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Now Playing Deck</h3>
              {lounge.nowPlaying ? (
                <div>
                  <div
                    style={{
                      fontSize: '0.76rem',
                      color: 'var(--text-secondary)',
                      fontFamily: 'monospace',
                    }}
                  >
                    {lounge.nowPlaying.trackId} • {lounge.nowPlaying.durationSeconds}s
                  </div>
                  <h4 style={{ margin: '0.2rem 0' }}>
                    {lounge.nowPlaying.title} — {lounge.nowPlaying.artist}
                  </h4>
                  <div style={{ display: 'flex', gap: '0.5rem', margin: '0.6rem 0' }}>
                    <span className="pill" style={{ color: 'var(--accent-emerald)' }}>
                      {lounge.nowPlaying.bpm} BPM
                    </span>
                    <span className="pill" style={{ color: 'var(--accent-cyan)' }}>
                      Key {lounge.nowPlaying.camelotKey}
                    </span>
                    <span className="pill">Energy {lounge.nowPlaying.energy}</span>
                    <span className="pill">Valence {lounge.nowPlaying.valence}</span>
                  </div>
                  <audio
                    controls
                    preload="none"
                    src={resolveAssetUrl(lounge.nowPlaying.audioAssetUrl)}
                    style={{ width: '100%', marginTop: '0.5rem' }}
                  />
                </div>
              ) : (
                <p style={{ color: 'var(--text-secondary)' }}>
                  No track currently assigned to Now Playing.
                </p>
              )}

              <div
                style={{
                  marginTop: '1.15rem',
                  paddingTop: '0.85rem',
                  borderTop: '1px solid var(--border-subtle)',
                  fontSize: '0.84rem',
                  color: 'var(--text-secondary)',
                }}
              >
                <div>BPM Tolerance Window: ±{lounge.settings?.bpmTolerancePct}%</div>
                <div>AI DJ Banter Verbosity: {lounge.settings?.aiVerbosity}</div>
                <div>
                  Soundclash Queue Battles:{' '}
                  {lounge.settings?.soundclashEnabled ? 'Enabled' : 'Disabled'}
                </div>
              </div>
            </div>

            <div className="card">
              <h3 style={{ marginTop: 0 }}>
                Room Participants ({lounge.participants?.length || 0})
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.55rem' }}>
                {(lounge.participants || []).map((p) => (
                  <div
                    key={p.userId}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '0.6rem 0.85rem',
                      borderRadius: '8px',
                      background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-subtle)',
                    }}
                  >
                    <div>
                      <strong>{p.displayName}</strong>{' '}
                      <span
                        style={{
                          fontSize: '0.78rem',
                          color: 'var(--text-secondary)',
                          fontFamily: 'monospace',
                        }}
                      >
                        ({p.userId})
                      </span>
                      {p.isMuted && (
                        <span
                          className="pill"
                          style={{
                            marginLeft: '0.5rem',
                            color: 'var(--accent-danger)',
                          }}
                        >
                          MUTED
                        </span>
                      )}
                    </div>
                    <span className={`role-badge role-${p.role}`}>{p.role}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
