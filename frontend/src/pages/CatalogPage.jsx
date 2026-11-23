import React, { useCallback, useEffect, useState } from 'react';
import httpClient, { resolveAssetUrl } from '../api/httpClient';

const CAMELOT_KEYS = [
  '1A', '2A', '3A', '4A', '5A', '6A', '7A', '8A', '9A', '10A', '11A', '12A',
  '1B', '2B', '3B', '4B', '5B', '6B', '7B', '8B', '9B', '10B', '11B', '12B',
];

export default function CatalogPage() {
  const [tracks, setTracks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [filterKey, setFilterKey] = useState('');
  const [minBpm, setMinBpm] = useState('');
  const [maxBpm, setMaxBpm] = useState('');

  const [selectedProfile, setSelectedProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState(null);

  const fetchTracks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (filterKey) {
        params.set('camelotKey', filterKey);
      }
      if (minBpm.trim() !== '') {
        params.set('minBpm', minBpm.trim());
      }
      if (maxBpm.trim() !== '') {
        params.set('maxBpm', maxBpm.trim());
      }
      const query = params.toString();
      const path = query ? `/api/v1/tracks?${query}` : '/api/v1/tracks';
      const data = await httpClient.get(path);
      setTracks(Array.isArray(data?.tracks) ? data.tracks : []);
    } catch (err) {
      setError(err.message || 'Failed to load track catalog.');
      setTracks([]);
    } finally {
      setLoading(false);
    }
  }, [filterKey, minBpm, maxBpm]);

  useEffect(() => {
    fetchTracks();
  }, [fetchTracks]);

  const handleOpenAcousticProfile = async (trackId) => {
    setProfileLoading(true);
    setProfileError(null);
    try {
      const profile = await httpClient.get(`/api/v1/tracks/${trackId}/acoustic-profile`);
      setSelectedProfile(profile);
    } catch (err) {
      setProfileError(err.message || 'Failed to fetch track acoustic profile.');
    } finally {
      setProfileLoading(false);
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
          alignItems: 'flex-end',
          justifyContent: 'space-between',
          gap: '1rem',
        }}
      >
        <div>
          <h2 style={{ margin: '0 0 0.25rem 0' }}>
            Royalty-Free Track Catalog &amp; Acoustic Vectors
          </h2>
          <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '0.88rem' }}>
            Browse profiled tracks with BPM, 24-key Camelot wheel codes, energy/valence vectors, WAV previews, and RAG timelines.
          </p>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', alignItems: 'flex-end' }}>
          <div>
            <label
              htmlFor="catalog-camelot-filter"
              style={{ display: 'block', fontSize: '0.76rem', color: 'var(--text-secondary)' }}
            >
              Camelot Key
            </label>
            <select
              id="catalog-camelot-filter"
              className="form-select"
              value={filterKey}
              onChange={(e) => setFilterKey(e.target.value)}
            >
              <option value="">All 24 Keys</option>
              {CAMELOT_KEYS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label
              htmlFor="catalog-min-bpm"
              style={{ display: 'block', fontSize: '0.76rem', color: 'var(--text-secondary)' }}
            >
              Min BPM
            </label>
            <input
              id="catalog-min-bpm"
              className="form-input"
              type="number"
              min="1"
              max="400"
              style={{ width: '95px' }}
              value={minBpm}
              onChange={(e) => setMinBpm(e.target.value)}
              placeholder="e.g. 90"
            />
          </div>

          <div>
            <label
              htmlFor="catalog-max-bpm"
              style={{ display: 'block', fontSize: '0.76rem', color: 'var(--text-secondary)' }}
            >
              Max BPM
            </label>
            <input
              id="catalog-max-bpm"
              className="form-input"
              type="number"
              min="1"
              max="400"
              style={{ width: '95px' }}
              value={maxBpm}
              onChange={(e) => setMaxBpm(e.target.value)}
              placeholder="e.g. 132"
            />
          </div>

          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              setFilterKey('');
              setMinBpm('');
              setMaxBpm('');
            }}
          >
            Reset Filters
          </button>
        </div>
      </div>

      {error && (
        <div className="alert-banner alert-error" role="alert">
          {error}
        </div>
      )}

      {profileError && (
        <div className="alert-banner alert-error" role="alert">
          {profileError}
        </div>
      )}

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '2.5rem' }}>
          Loading track catalog...
        </div>
      ) : tracks.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '2.5rem' }}>
          No tracks matched your current filter criteria.
        </div>
      ) : (
        <div className="card-grid">
          {tracks.map((track) => {
            const energyPct = Math.round((track.acoustic?.energy || 0) * 100);
            const valencePct = Math.round((track.acoustic?.valence || 0) * 100);
            const audioUrl = resolveAssetUrl(track.audioAssetUrl);

            return (
              <div key={track.trackId} className="card">
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
                        fontSize: '0.74rem',
                        color: 'var(--text-secondary)',
                        fontFamily: 'monospace',
                      }}
                    >
                      {track.trackId} • {track.durationSeconds}s
                    </div>
                    <h3 style={{ margin: '0.15rem 0', fontSize: '1.08rem' }}>
                      {track.title}
                    </h3>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.88rem' }}>
                      {track.artist}
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: '0.4rem' }}>
                    <span className="pill" style={{ color: 'var(--accent-emerald)' }}>
                      {track.acoustic?.bpm} BPM
                    </span>
                    <span className="pill" style={{ color: 'var(--accent-cyan)' }}>
                      Key {track.acoustic?.camelotKey}
                    </span>
                  </div>
                </div>

                <div style={{ marginTop: '0.9rem' }}>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      fontSize: '0.78rem',
                      marginBottom: '0.2rem',
                    }}
                  >
                    <span>Energy</span>
                    <span>{track.acoustic?.energy}</span>
                  </div>
                  <div className="metric-bar-track">
                    <div
                      className="metric-bar-fill"
                      style={{
                        width: `${energyPct}%`,
                        background: 'var(--accent-emerald)',
                      }}
                    />
                  </div>
                </div>

                <div style={{ marginTop: '0.6rem' }}>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      fontSize: '0.78rem',
                      marginBottom: '0.2rem',
                    }}
                  >
                    <span>Valence (Positivity)</span>
                    <span>{track.acoustic?.valence}</span>
                  </div>
                  <div className="metric-bar-track">
                    <div
                      className="metric-bar-fill"
                      style={{
                        width: `${valencePct}%`,
                        background: 'var(--accent-cyan)',
                      }}
                    />
                  </div>
                </div>

                <div style={{ marginTop: '0.95rem' }}>
                  <audio
                    controls
                    preload="none"
                    src={audioUrl}
                    style={{ width: '100%', height: '34px' }}
                  >
                    Your browser does not support HTML5 audio preview.
                  </audio>
                </div>

                <div
                  style={{
                    marginTop: '0.9rem',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                    {Array.isArray(track.timeline) ? track.timeline.length : 0} RAG timeline event(s)
                  </span>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    style={{ fontSize: '0.8rem', padding: '0.4rem 0.75rem' }}
                    onClick={() => handleOpenAcousticProfile(track.trackId)}
                    disabled={profileLoading}
                  >
                    Inspect RAG Timeline
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {selectedProfile && (
        <div
          className="modal-backdrop"
          onClick={() => setSelectedProfile(null)}
          role="dialog"
          aria-modal="true"
        >
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'flex-start',
                marginBottom: '1rem',
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: '0.78rem',
                    color: 'var(--accent-cyan)',
                    fontFamily: 'monospace',
                  }}
                >
                  PRD Section 7.3.2 Acoustic Profile • {selectedProfile.trackId}
                </div>
                <h3 style={{ margin: '0.2rem 0' }}>
                  {selectedProfile.title} — {selectedProfile.artist}
                </h3>
              </div>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setSelectedProfile(null)}
              >
                Close
              </button>
            </div>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, 1fr)',
                gap: '0.65rem',
                marginBottom: '1.25rem',
              }}
            >
              <div className="card" style={{ padding: '0.75rem', textAlign: 'center' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>BPM</div>
                <div style={{ fontSize: '1.15rem', fontWeight: 700 }}>
                  {selectedProfile.acoustic?.bpm}
                </div>
              </div>
              <div className="card" style={{ padding: '0.75rem', textAlign: 'center' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                  Camelot Key
                </div>
                <div style={{ fontSize: '1.15rem', fontWeight: 700 }}>
                  {selectedProfile.acoustic?.camelotKey}
                </div>
              </div>
              <div className="card" style={{ padding: '0.75rem', textAlign: 'center' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Energy</div>
                <div style={{ fontSize: '1.15rem', fontWeight: 700 }}>
                  {selectedProfile.acoustic?.energy}
                </div>
              </div>
              <div className="card" style={{ padding: '0.75rem', textAlign: 'center' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Valence</div>
                <div style={{ fontSize: '1.15rem', fontWeight: 700 }}>
                  {selectedProfile.acoustic?.valence}
                </div>
              </div>
            </div>

            <h4 style={{ marginBottom: '0.6rem' }}>
              RAG Metadata Timeline ({selectedProfile.timeline?.length || 0} events)
            </h4>
            {Array.isArray(selectedProfile.timeline) && selectedProfile.timeline.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
                {selectedProfile.timeline.map((evt, idx) => (
                  <div
                    key={`${evt.ts}-${evt.type}-${idx}`}
                    style={{
                      padding: '0.75rem',
                      borderRadius: '8px',
                      background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-subtle)',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        marginBottom: '0.3rem',
                      }}
                    >
                      <span className="pill" style={{ color: 'var(--accent-emerald)' }}>
                        {evt.type}
                      </span>
                      <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                        Timestamp: {evt.ts}s
                      </span>
                    </div>
                    <div style={{ fontSize: '0.9rem' }}>{evt.text}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ color: 'var(--text-secondary)' }}>
                No timeline events recorded for this track.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
