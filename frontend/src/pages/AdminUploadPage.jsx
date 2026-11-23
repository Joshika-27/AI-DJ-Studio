import React, { useState } from 'react';
import httpClient from '../api/httpClient';
import { useAuth } from '../context/AuthContext';

const CAMELOT_KEYS = [
  '1A', '2A', '3A', '4A', '5A', '6A', '7A', '8A', '9A', '10A', '11A', '12A',
  '1B', '2B', '3B', '4B', '5B', '6B', '7B', '8B', '9B', '10B', '11B', '12B',
];

const TIMELINE_TYPES = ['sample_credit', 'trivia', 'glossary'];

export default function AdminUploadPage({ onTrackCreated }) {
  const { user } = useAuth();

  const [trackId, setTrackId] = useState('');
  const [title, setTitle] = useState('');
  const [artist, setArtist] = useState('');
  const [durationSeconds, setDurationSeconds] = useState(195);
  const [audioAssetUrl, setAudioAssetUrl] = useState('/assets/audio/t_2001.wav');
  const [bpm, setBpm] = useState(126);
  const [camelotKey, setCamelotKey] = useState('8A');
  const [energy, setEnergy] = useState(0.82);
  const [valence, setValence] = useState(0.68);
  const [timeline, setTimeline] = useState([
    {
      ts: 15,
      type: 'sample_credit',
      text: 'Analog synth lead sampled from a 1983 studio tape.',
    },
  ]);

  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);
  const [createdTrack, setCreatedTrack] = useState(null);

  if (!user || user.role !== 'admin') {
    return (
      <div className="card">
        <h3>Administrator Access Required</h3>
        <p style={{ color: 'var(--text-secondary)' }}>
          Track acoustic profile management is restricted to the <code>admin</code> role and enforced server-side via <code>require_admin</code>.
        </p>
      </div>
    );
  }

  const handleAddTimelineRow = () => {
    setTimeline((prev) => [
      ...prev,
      { ts: 30, type: 'trivia', text: '' },
    ]);
  };

  const handleRemoveTimelineRow = (idx) => {
    setTimeline((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleTimelineChange = (idx, field, value) => {
    setTimeline((prev) =>
      prev.map((item, i) => (i === idx ? { ...item, [field]: value } : item))
    );
  };

  const validateClientSide = () => {
    if (!title.trim() || !artist.trim()) {
      return 'Title and artist are required.';
    }
    const numBpm = Number(bpm);
    if (!Number.isFinite(numBpm) || numBpm <= 0 || numBpm > 400) {
      return 'BPM must be greater than 0 and at most 400.';
    }
    if (!CAMELOT_KEYS.includes(camelotKey)) {
      return 'Camelot key must be one of the 24 valid codes (1A-12A, 1B-12B).';
    }
    const numEnergy = Number(energy);
    if (!Number.isFinite(numEnergy) || numEnergy < 0 || numEnergy > 1) {
      return 'Energy must be between 0.0 and 1.0.';
    }
    const numValence = Number(valence);
    if (!Number.isFinite(numValence) || numValence < 0 || numValence > 1) {
      return 'Valence must be between 0.0 and 1.0.';
    }
    const numDuration = Number(durationSeconds);
    if (!Number.isInteger(numDuration) || numDuration <= 0 || numDuration > 7200) {
      return 'Duration must be a positive integer up to 7200 seconds.';
    }
    for (let i = 0; i < timeline.length; i += 1) {
      const evt = timeline[i];
      const tsNum = Number(evt.ts);
      if (!Number.isInteger(tsNum) || tsNum < 0 || tsNum > numDuration) {
        return `Timeline event #${i + 1} timestamp (${evt.ts}s) must be between 0 and durationSeconds (${numDuration}s).`;
      }
      if (!TIMELINE_TYPES.includes(evt.type)) {
        return `Timeline event #${i + 1} has an invalid type.`;
      }
      if (!String(evt.text || '').trim() || String(evt.text).trim().length < 2) {
        return `Timeline event #${i + 1} text must contain at least 2 characters.`;
      }
    }
    return null;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setErrorMessage(null);
    setCreatedTrack(null);

    const validationError = validateClientSide();
    if (validationError) {
      setErrorMessage(validationError);
      return;
    }

    setSubmitting(true);
    try {
      const payload = {
        title: title.trim(),
        artist: artist.trim(),
        durationSeconds: Number(durationSeconds),
        audioAssetUrl: audioAssetUrl.trim(),
        acoustic: {
          bpm: Number(bpm),
          camelotKey,
          energy: Number(energy),
          valence: Number(valence),
        },
        timeline: timeline.map((evt) => ({
          ts: Number(evt.ts),
          type: evt.type,
          text: String(evt.text).trim(),
        })),
      };
      if (trackId.trim()) {
        payload.trackId = trackId.trim();
      }

      const created = await httpClient.post('/api/v1/admin/tracks', payload);
      setCreatedTrack(created);
      if (onTrackCreated) {
        onTrackCreated(created);
      }
    } catch (err) {
      setErrorMessage(err.message || 'Failed to upload track acoustic profile.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: '820px', margin: '0 auto' }}>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Admin Acoustic Profile &amp; RAG Timeline Upload</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.88rem', marginTop: 0 }}>
          Upload and validate a track&apos;s acoustic vector (BPM &gt; 0, 24 Camelot keys, energy/valence 0.0–1.0) and timestamped RAG metadata timeline via <code>POST /api/v1/admin/tracks</code>.
        </p>

        {errorMessage && (
          <div className="alert-banner alert-error" role="alert">
            {errorMessage}
          </div>
        )}

        {createdTrack && (
          <div className="alert-banner alert-success" role="status">
            Uploaded track <strong>{createdTrack.title}</strong> (<code>{createdTrack.trackId}</code>) — {createdTrack.acoustic?.bpm} BPM, Key {createdTrack.acoustic?.camelotKey}.
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
              gap: '0.85rem',
            }}
          >
            <div className="form-group">
              <label htmlFor="upload-track-id">Track ID (Optional)</label>
              <input
                id="upload-track-id"
                className="form-input"
                type="text"
                value={trackId}
                onChange={(e) => setTrackId(e.target.value)}
                placeholder="e.g. t_3010"
              />
            </div>

            <div className="form-group">
              <label htmlFor="upload-title">Track Title *</label>
              <input
                id="upload-title"
                className="form-input"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Neon Odyssey"
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="upload-artist">Artist *</label>
              <input
                id="upload-artist"
                className="form-input"
                type="text"
                value={artist}
                onChange={(e) => setArtist(e.target.value)}
                placeholder="Nova Circuit"
                required
              />
            </div>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(175px, 1fr))',
              gap: '0.85rem',
            }}
          >
            <div className="form-group">
              <label htmlFor="upload-bpm">BPM (&gt; 0) *</label>
              <input
                id="upload-bpm"
                className="form-input"
                type="number"
                step="0.1"
                min="1"
                max="400"
                value={bpm}
                onChange={(e) => setBpm(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="upload-camelot">Camelot Key (1A–12B) *</label>
              <select
                id="upload-camelot"
                className="form-select"
                value={camelotKey}
                onChange={(e) => setCamelotKey(e.target.value)}
              >
                {CAMELOT_KEYS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label htmlFor="upload-energy">Energy (0.0–1.0) *</label>
              <input
                id="upload-energy"
                className="form-input"
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={energy}
                onChange={(e) => setEnergy(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="upload-valence">Valence (0.0–1.0) *</label>
              <input
                id="upload-valence"
                className="form-input"
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={valence}
                onChange={(e) => setValence(e.target.value)}
                required
              />
            </div>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '180px 1fr',
              gap: '0.85rem',
            }}
          >
            <div className="form-group">
              <label htmlFor="upload-duration">Duration (seconds) *</label>
              <input
                id="upload-duration"
                className="form-input"
                type="number"
                min="1"
                max="7200"
                value={durationSeconds}
                onChange={(e) => setDurationSeconds(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="upload-audio-url">Audio Asset URL (.wav, .mp3, .ogg) *</label>
              <input
                id="upload-audio-url"
                className="form-input"
                type="text"
                value={audioAssetUrl}
                onChange={(e) => setAudioAssetUrl(e.target.value)}
                placeholder="/assets/audio/t_2001.wav"
                required
              />
            </div>
          </div>

          <div
            style={{
              marginTop: '1rem',
              paddingTop: '1rem',
              borderTop: '1px solid var(--border-subtle)',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '0.75rem',
              }}
            >
              <h4 style={{ margin: 0 }}>RAG Metadata Timeline Events</h4>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleAddTimelineRow}
              >
                + Add Timeline Event
              </button>
            </div>

            {timeline.map((evt, idx) => (
              <div
                key={idx}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '100px 160px 1fr auto',
                  gap: '0.6rem',
                  alignItems: 'center',
                  marginBottom: '0.65rem',
                }}
              >
                <input
                  className="form-input"
                  type="number"
                  min="0"
                  placeholder="ts (s)"
                  value={evt.ts}
                  onChange={(e) => handleTimelineChange(idx, 'ts', e.target.value)}
                  aria-label={`Event ${idx + 1} timestamp`}
                />
                <select
                  className="form-select"
                  value={evt.type}
                  onChange={(e) => handleTimelineChange(idx, 'type', e.target.value)}
                  aria-label={`Event ${idx + 1} type`}
                >
                  {TIMELINE_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                <input
                  className="form-input"
                  type="text"
                  placeholder="Grounded track trivia, sample credit, or glossary note..."
                  value={evt.text}
                  onChange={(e) => handleTimelineChange(idx, 'text', e.target.value)}
                  aria-label={`Event ${idx + 1} text`}
                />
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={() => handleRemoveTimelineRow(idx)}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            style={{ marginTop: '1rem', width: '100%' }}
            disabled={submitting}
          >
            {submitting ? 'Uploading & Validating...' : 'Upload Track Acoustic Profile'}
          </button>
        </form>
      </div>
    </div>
  );
}
