import React, { useState } from 'react';
import httpClient from '../../api/httpClient';

export default function HostModerationBar({
  lounge,
  currentUser,
  onLoungeUpdated,
}) {
  const [selectedUserId, setSelectedUserId] = useState('');
  const [newRole, setNewRole] = useState('co_dj');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [error, setError] = useState(null);

  if (!lounge || !currentUser) {
    return null;
  }

  const isHostOrAdmin =
    currentUser.role === 'admin' ||
    (currentUser.role === 'host' && lounge.hostUserId === currentUser.id) ||
    lounge.moderationState?.hostUserId === currentUser.id;

  if (!isHostOrAdmin) {
    return null;
  }

  const nonHostParticipants = (lounge.participants || []).filter(
    (p) => p.userId !== lounge.hostUserId
  );

  const runModeration = async (payload, successMsg) => {
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await httpClient.post(`/api/v1/lounges/${lounge.loungeId}/moderation`, payload);
      setFeedback(successMsg);
      if (onLoungeUpdated) {
        await onLoungeUpdated();
      }
    } catch (err) {
      setError(err.message || 'Moderation command failed.');
    } finally {
      setBusy(false);
    }
  };

  const handleStartSession = async () => {
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await httpClient.post(`/api/v1/lounges/${lounge.loungeId}/start`);
      setFeedback('DJ Lounge is now LIVE.');
      if (onLoungeUpdated) {
        await onLoungeUpdated();
      }
    } catch (err) {
      setError(err.message || 'Failed to start DJ Lounge.');
    } finally {
      setBusy(false);
    }
  };

  const handleEndSession = async () => {
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await httpClient.post(`/api/v1/lounges/${lounge.loungeId}/end`);
      setFeedback('DJ Lounge session ended and summary persisted.');
      if (onLoungeUpdated) {
        await onLoungeUpdated();
      }
    } catch (err) {
      setError(err.message || 'Failed to end DJ Lounge.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="card"
      style={{
        marginBottom: '1.25rem',
        border: '1px solid rgba(29, 185, 84, 0.4)',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.75rem',
          marginBottom: '0.85rem',
        }}
      >
        <div>
          <h4 style={{ margin: 0, color: 'var(--accent-emerald)' }}>
            Host Lifecycle &amp; Moderation Controls
          </h4>
          <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            Authoritative server-enforced Host controls (REQ-015, REQ-025)
          </span>
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {lounge.status === 'scheduled' && (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={handleStartSession}
            >
              Start Live Session
            </button>
          )}

          {lounge.status !== 'ended' && (
            <>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  runModeration(
                    { action: lounge.isLocked ? 'unlock' : 'lock' },
                    lounge.isLocked ? 'Room unlocked.' : 'Room locked against new joins.'
                  )
                }
              >
                {lounge.isLocked ? 'Unlock Room' : 'Lock Room'}
              </button>

              <button
                type="button"
                className="btn btn-danger"
                disabled={busy}
                onClick={handleEndSession}
              >
                End Session
              </button>
            </>
          )}
        </div>
      </div>

      {error && (
        <div className="alert-banner alert-error" role="alert">
          {error}
        </div>
      )}
      {feedback && (
        <div className="alert-banner alert-success" role="status">
          {feedback}
        </div>
      )}

      {lounge.status !== 'ended' && (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.6rem',
            alignItems: 'center',
            paddingTop: '0.75rem',
            borderTop: '1px solid var(--border-subtle)',
          }}
        >
          <select
            className="form-select"
            value={selectedUserId}
            onChange={(e) => setSelectedUserId(e.target.value)}
            aria-label="Select participant to moderate"
          >
            <option value="">Select Participant...</option>
            {nonHostParticipants.map((p) => (
              <option key={p.userId} value={p.userId}>
                {p.displayName} ({p.userId} • {p.role}
                {p.isMuted ? ' • muted' : ''})
              </option>
            ))}
          </select>

          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy || !selectedUserId}
            onClick={() =>
              runModeration(
                { action: 'mute', targetUserId: selectedUserId },
                `Muted participant ${selectedUserId}.`
              )
            }
          >
            Mute
          </button>

          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy || !selectedUserId}
            onClick={() =>
              runModeration(
                { action: 'unmute', targetUserId: selectedUserId },
                `Unmuted participant ${selectedUserId}.`
              )
            }
          >
            Unmute
          </button>

          <button
            type="button"
            className="btn btn-danger"
            disabled={busy || !selectedUserId}
            onClick={() =>
              runModeration(
                { action: 'remove', targetUserId: selectedUserId },
                `Removed participant ${selectedUserId} from lounge.`
              )
            }
          >
            Remove / Kick
          </button>

          <select
            className="form-select"
            value={newRole}
            onChange={(e) => setNewRole(e.target.value)}
            aria-label="Target role for participant"
          >
            <option value="listener">Role: Listener</option>
            <option value="co_dj">Role: Co-DJ</option>
            <option value="host">Transfer Host Role</option>
          </select>

          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy || !selectedUserId}
            onClick={() =>
              runModeration(
                {
                  action: 'set_role',
                  targetUserId: selectedUserId,
                  newRole,
                },
                `Updated ${selectedUserId} role to '${newRole}'.`
              )
            }
          >
            Set Role
          </button>
        </div>
      )}
    </div>
  );
}
