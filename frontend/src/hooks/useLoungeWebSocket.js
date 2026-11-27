/**
 * Real-Time WebSocket Hook for AI DJ Studio (Module 8.6A — REQ-060..REQ-068, REQ-078).
 *
 * Provides a managed WebSocket connection to a DJ Lounge:
 * - Connects to `/ws/lounges/{loungeId}?token={token}` using ws:// or wss://
 * - Fetches fresh tokens on every connection/reconnect via getAccessToken()
 * - Tracks connection lifecycle with exponential backoff within a 15-second grace window
 * - Inbound event handling:
 *     - lounge.state.snapshot (immediate state synchronization)
 *     - lounge.queue.update (version-guarded monotonic queue updates)
 *     - lounge.presence.update (participant joins/leaves)
 *     - lounge.chat.message (real-time chat stream)
 *     - lounge.chat.typing (user typing indicators with auto-expiry)
 *     - lounge.chat.reaction (message emoji reactions)
 *     - lounge.host.status (host disconnect, mutation freeze, Co-DJ promotion)
 *     - lounge.sync.pong (client-server clock and roundtrip latency calculation)
 *     - lounge.error (structured domain/permission error notifications)
 * - Outbound action methods:
 *     - voteTrack(trackId)
 *     - sendChatMessage(body)
 *     - sendTyping(isTyping)
 *     - sendReaction(messageId, emoji, action)
 *     - sendPing()
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { getAccessToken, getApiBaseUrl } from '../api/httpClient';

const RECONNECT_INITIAL_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 4000;
const RECONNECT_GRACE_WINDOW_MS = 15000;
const PING_INTERVAL_MS = 10000;
const TYPING_TIMEOUT_MS = 4000;

export const TERMINAL_CLOSE_CODES = new Set([
  4001, // Unauthorized / Expired token
  4003, // Forbidden / Kicked / Locked non-member
  4004, // Lounge not found
  4009, // Lounge ended / Capacity exceeded
]);

/**
 * Constructs the canonical WebSocket URL for a given lounge and access token.
 */
export function getWebSocketUrl(loungeId, token) {
  if (!loungeId) {
    return '';
  }
  const baseUrl = getApiBaseUrl();
  const wsProtocol = baseUrl.startsWith('https://') ? 'wss://' : 'ws://';
  const hostAndPath = baseUrl.replace(/^https?:\/\//, '');
  const encodedToken = encodeURIComponent(token || '');
  return `${wsProtocol}${hostAndPath}/ws/lounges/${loungeId}?token=${encodedToken}`;
}

/**
 * Constructs the canonical WebSocket envelope dictionary (REQ-060).
 */
export function makeWsEnvelope(event, loungeId, payload = {}) {
  return JSON.stringify({
    event,
    loungeId,
    payload,
    ts: Date.now(),
  });
}

export function useLoungeWebSocket(loungeId, options = {}) {
  const {
    autoConnect = true,
    pingIntervalMs = PING_INTERVAL_MS,
    reconnectGraceMs = RECONNECT_GRACE_WINDOW_MS,
    tokenProvider = getAccessToken,
    WebSocketClass = typeof window !== 'undefined' ? window.WebSocket : null,
  } = options;

  // Stable references for options to prevent unnecessary socket re-creations
  const tokenProviderRef = useRef(tokenProvider);
  tokenProviderRef.current = tokenProvider;

  const webSocketClassRef = useRef(WebSocketClass);
  webSocketClassRef.current = WebSocketClass;

  const pingIntervalMsRef = useRef(pingIntervalMs);
  pingIntervalMsRef.current = pingIntervalMs;

  const reconnectGraceMsRef = useRef(reconnectGraceMs);
  reconnectGraceMsRef.current = reconnectGraceMs;

  // Connection and latency states
  const [connectionStatus, setConnectionStatus] = useState('idle'); // 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected' | 'error'
  const [latencyMs, setLatencyMs] = useState(null);
  const [lastError, setLastError] = useState(null);

  // Authoritative room states
  const [nowPlaying, setNowPlaying] = useState(null);
  const [queue, setQueue] = useState([]);
  const [queueVersion, setQueueVersion] = useState(0);
  const [participants, setParticipants] = useState([]);
  const [chatMessages, setChatMessages] = useState([]);
  const [typingUsers, setTypingUsers] = useState([]);
  const [soundclash, setSoundclash] = useState({
    active: false,
    battleId: null,
    candidates: [],
    votes: {},
    closesAt: null,
  });
  const [sentiment, setSentiment] = useState({
    rollingScore: 0.5,
    windowSeconds: 60,
    sampleSize: 0,
  });
  const [hostStatus, setHostStatus] = useState({
    status: 'connected',
    activeHostUserId: null,
    mutationsFrozen: false,
  });

  // Internal refs to preserve identity across renders
  const socketRef = useRef(null);
  const isUnmountedRef = useRef(false);
  const reconnectTimeoutRef = useRef(null);
  const pingIntervalRef = useRef(null);
  const reconnectStartTimeRef = useRef(null);
  const reconnectDelayRef = useRef(RECONNECT_INITIAL_DELAY_MS);
  const typingTimeoutsRef = useRef(new Map());
  const queueVersionRef = useRef(0);

  // Keep queueVersionRef in sync
  useEffect(() => {
    queueVersionRef.current = queueVersion;
  }, [queueVersion]);

  // Clear typing users on expiry
  const addTypingUser = useCallback((user) => {
    if (!user || !user.userId) return;

    if (typingTimeoutsRef.current.has(user.userId)) {
      clearTimeout(typingTimeoutsRef.current.get(user.userId));
    }

    setTypingUsers((prev) => {
      const filtered = prev.filter((u) => u.userId !== user.userId);
      return [...filtered, { userId: user.userId, displayName: user.displayName || user.userId }];
    });

    const timeoutId = setTimeout(() => {
      setTypingUsers((prev) => prev.filter((u) => u.userId !== user.userId));
      typingTimeoutsRef.current.delete(user.userId);
    }, TYPING_TIMEOUT_MS);

    typingTimeoutsRef.current.set(user.userId, timeoutId);
  }, []);

  const removeTypingUser = useCallback((userId) => {
    if (!userId) return;
    if (typingTimeoutsRef.current.has(userId)) {
      clearTimeout(typingTimeoutsRef.current.get(userId));
      typingTimeoutsRef.current.delete(userId);
    }
    setTypingUsers((prev) => prev.filter((u) => u.userId !== userId));
  }, []);

  // --------------------------------------------------------------------------
  // Outbound Message Sender
  // --------------------------------------------------------------------------
  const sendEnvelope = useCallback(
    (event, payload = {}) => {
      if (!socketRef.current || socketRef.current.readyState !== 1 /* OPEN */) {
        return false;
      }
      try {
        const raw = makeWsEnvelope(event, loungeId, payload);
        socketRef.current.send(raw);
        return true;
      } catch (err) {
        setLastError({ code: 'SEND_FAILED', message: err.message || 'Failed to send event' });
        return false;
      }
    },
    [loungeId]
  );

  // Public action methods
  const voteTrack = useCallback(
    (trackId) => {
      if (hostStatus.mutationsFrozen) {
        setLastError({ code: 'MUTATION_FROZEN', message: 'Queue mutations are currently frozen' });
        return false;
      }
      return sendEnvelope('lounge.queue.vote', { trackId });
    },
    [hostStatus.mutationsFrozen, sendEnvelope]
  );

  const sendChatMessage = useCallback(
    (body) => {
      if (typeof body !== 'string' || !body.trim()) {
        return false;
      }
      return sendEnvelope('lounge.chat.message', { body: body.trim() });
    },
    [sendEnvelope]
  );

  const sendTyping = useCallback(
    (isTyping = true) => {
      return sendEnvelope('lounge.chat.typing', { isTyping: Boolean(isTyping) });
    },
    [sendEnvelope]
  );

  const sendReaction = useCallback(
    (messageId, emoji, action = 'toggle') => {
      if (!messageId || !emoji) {
        return false;
      }
      return sendEnvelope('lounge.chat.reaction', { messageId, emoji, action });
    },
    [sendEnvelope]
  );

  const sendPing = useCallback(() => {
    return sendEnvelope('lounge.sync.ping', { clientSentAt: Date.now() });
  }, [sendEnvelope]);

  // --------------------------------------------------------------------------
  // Inbound Message Processor
  // --------------------------------------------------------------------------
  const handleIncomingMessage = useCallback(
    (eventData) => {
      let parsed;
      try {
        parsed = typeof eventData === 'string' ? JSON.parse(eventData) : eventData;
      } catch {
        return;
      }

      if (!parsed || !parsed.event) {
        return;
      }

      const { event, payload = {} } = parsed;

      switch (event) {
        case 'lounge.state.snapshot': {
          // Authoritative initial snapshot (REQ-028)
          if (payload.nowPlaying !== undefined) {
            setNowPlaying(payload.nowPlaying);
          }
          if (Array.isArray(payload.queue)) {
            setQueue(payload.queue);
          }
          if (payload.queueVersion !== undefined) {
            setQueueVersion(payload.queueVersion);
          }
          if (Array.isArray(payload.participants)) {
            setParticipants(payload.participants);
          }
          if (Array.isArray(payload.chatHistory)) {
            // Normalize chat history messages
            const formatted = payload.chatHistory.map((m) => ({
              id: m.id || m.messageId,
              messageId: m.id || m.messageId,
              userId: m.userId,
              displayName: m.displayName || m.userId,
              body: m.body,
              sentimentBucket: m.sentimentBucket || 'neutral',
              createdAt: m.createdAt,
              reactions: m.reactions || {},
            }));
            setChatMessages(formatted);
          }
          if (payload.soundclash) {
            setSoundclash(payload.soundclash);
          }
          if (payload.sentiment) {
            setSentiment(payload.sentiment);
          }
          if (payload.hostStatus) {
            setHostStatus((prev) => ({
              ...prev,
              status: payload.hostStatus,
              mutationsFrozen: payload.hostStatus === 'host_disconnected',
            }));
          }
          break;
        }

        case 'lounge.queue.update': {
          // Guard against stale out-of-order queue updates
          const incomingVersion = payload.version ?? 0;
          if (incomingVersion >= queueVersionRef.current) {
            setQueueVersion(incomingVersion);
            if (Array.isArray(payload.queue)) {
              setQueue(payload.queue);
            }
            if (payload.nowPlaying !== undefined) {
              setNowPlaying(payload.nowPlaying);
            }
          }
          break;
        }

        case 'lounge.presence.update': {
          const { participantId, action, participantCount, displayName } = payload;
          setParticipants((prev) => {
            if (action === 'left') {
              return prev.filter((p) => p.userId !== participantId);
            }
            if (action === 'joined') {
              const existingIdx = prev.findIndex((p) => p.userId === participantId);
              if (existingIdx >= 0) {
                return prev;
              }
              return [
                ...prev,
                {
                  userId: participantId,
                  displayName: displayName || participantId,
                  role: payload.role || 'listener',
                  isMuted: Boolean(payload.isMuted),
                  joinedAt: new Date().toISOString(),
                },
              ];
            }
            return prev;
          });
          break;
        }

        case 'lounge.chat.message': {
          const newMsg = {
            id: payload.messageId || `msg_${Date.now()}`,
            messageId: payload.messageId,
            userId: payload.userId,
            displayName: payload.displayName || payload.userId,
            body: payload.body,
            sentimentBucket: payload.sentimentBucket || 'neutral',
            tsSeconds: payload.tsSeconds,
            createdAt: payload.createdAt || new Date().toISOString(),
            reactions: {},
          };
          setChatMessages((prev) => {
            // Deduplicate by messageId
            if (prev.some((m) => m.messageId === newMsg.messageId)) {
              return prev;
            }
            return [...prev, newMsg];
          });
          // Remove from typing if this user sent a message
          removeTypingUser(payload.userId);
          break;
        }

        case 'lounge.chat.typing': {
          const { userId, displayName, isTyping, action } = payload;
          if (isTyping === true || action === 'started') {
            addTypingUser({ userId, displayName });
          } else {
            removeTypingUser(userId);
          }
          break;
        }

        case 'lounge.chat.reaction': {
          const { messageId, userId, emoji, action } = payload;
          setChatMessages((prev) =>
            prev.map((msg) => {
              if (msg.messageId !== messageId) {
                return msg;
              }
              const currentReactions = { ...(msg.reactions || {}) };
              const userList = Array.isArray(currentReactions[emoji])
                ? [...currentReactions[emoji]]
                : [];

              if (action === 'add') {
                if (!userList.includes(userId)) {
                  userList.push(userId);
                }
              } else if (action === 'remove') {
                const idx = userList.indexOf(userId);
                if (idx >= 0) {
                  userList.splice(idx, 1);
                }
              }

              if (userList.length === 0) {
                delete currentReactions[emoji];
              } else {
                currentReactions[emoji] = userList;
              }

              return { ...msg, reactions: currentReactions };
            })
          );
          break;
        }

        case 'lounge.host.status': {
          const { status, activeHostUserId, mutationsFrozen } = payload;
          setHostStatus({
            status: status || 'connected',
            activeHostUserId: activeHostUserId || null,
            mutationsFrozen: Boolean(mutationsFrozen),
          });
          break;
        }

        case 'lounge.sync.pong': {
          if (payload.clientSentAt) {
            const calculatedLatency = Math.max(0, Date.now() - payload.clientSentAt);
            setLatencyMs(calculatedLatency);
          }
          break;
        }

        case 'lounge.error': {
          setLastError({
            code: payload.code || 'UNKNOWN_ERROR',
            message: payload.message || 'An error occurred on the WebSocket gateway',
          });
          break;
        }

        default:
          break;
      }
    },
    [addTypingUser, removeTypingUser]
  );

  // --------------------------------------------------------------------------
  // Connection Lifecycle Manager
  // --------------------------------------------------------------------------
  const connect = useCallback(() => {
    if (!loungeId || isUnmountedRef.current) {
      return;
    }

    const WSClass = webSocketClassRef.current;
    if (!WSClass) {
      return;
    }

    const token = tokenProviderRef.current ? tokenProviderRef.current() : null;
    if (!token) {
      setConnectionStatus('error');
      setLastError({ code: 'AUTH_REQUIRED', message: 'No access token available for WebSocket connection' });
      return;
    }

    const url = getWebSocketUrl(loungeId, token);
    setConnectionStatus((prev) => (prev === 'idle' ? 'connecting' : 'reconnecting'));

    try {
      const ws = new WSClass(url);
      socketRef.current = ws;

      ws.onopen = () => {
        if (isUnmountedRef.current) {
          ws.close(1000, 'Unmounted');
          return;
        }
        setConnectionStatus('connected');
        setLastError(null);
        reconnectStartTimeRef.current = null;
        reconnectDelayRef.current = RECONNECT_INITIAL_DELAY_MS;

        // Start periodic sync ping
        if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
        const interval = pingIntervalMsRef.current || PING_INTERVAL_MS;
        pingIntervalRef.current = setInterval(() => {
          if (ws.readyState === 1 /* OPEN */) {
            ws.send(makeWsEnvelope('lounge.sync.ping', loungeId, { clientSentAt: Date.now() }));
          }
        }, interval);
      };

      ws.onmessage = (evt) => {
        if (!isUnmountedRef.current) {
          handleIncomingMessage(evt.data);
        }
      };

      ws.onerror = (err) => {
        if (!isUnmountedRef.current) {
          setLastError({ code: 'SOCKET_ERROR', message: err?.message || 'WebSocket error encountered' });
        }
      };

      ws.onclose = (evt) => {
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current);
          pingIntervalRef.current = null;
        }

        if (isUnmountedRef.current) {
          return;
        }

        // Terminal close codes: do NOT retry
        if (TERMINAL_CLOSE_CODES.has(evt.code)) {
          setConnectionStatus('error');
          setLastError({
            code: `TERMINAL_CLOSE_${evt.code}`,
            message: evt.reason || `Connection terminated by server (code ${evt.code})`,
          });
          return;
        }

        // Normal voluntary closure
        if (evt.code === 1000) {
          setConnectionStatus('disconnected');
          return;
        }

        // Handle exponential backoff within grace window (REQ-078)
        if (!reconnectStartTimeRef.current) {
          reconnectStartTimeRef.current = Date.now();
        }

        const graceWindow = reconnectGraceMsRef.current || RECONNECT_GRACE_WINDOW_MS;
        const elapsedMs = Date.now() - reconnectStartTimeRef.current;
        if (elapsedMs < graceWindow) {
          setConnectionStatus('reconnecting');
          const delay = reconnectDelayRef.current;
          reconnectDelayRef.current = Math.min(reconnectDelayRef.current * 2, RECONNECT_MAX_DELAY_MS);

          reconnectTimeoutRef.current = setTimeout(() => {
            if (!isUnmountedRef.current) {
              const currentElapsed = Date.now() - (reconnectStartTimeRef.current || Date.now());
              if (currentElapsed >= graceWindow) {
                setConnectionStatus('disconnected');
                setLastError({
                  code: 'RECONNECT_TIMEOUT',
                  message: `Connection lost. Reconnect grace period (${Math.round(graceWindow / 1000)}s) exceeded.`,
                });
                return;
              }
              connect();
            }
          }, delay);
        } else {
          setConnectionStatus('disconnected');
          setLastError({
            code: 'RECONNECT_TIMEOUT',
            message: `Connection lost. Reconnect grace period (${Math.round(graceWindow / 1000)}s) exceeded.`,
          });
        }
      };
    } catch (err) {
      setConnectionStatus('error');
      setLastError({ code: 'CONNECTION_FAILED', message: err.message || 'Failed to initialize WebSocket' });
    }
  }, [loungeId, handleIncomingMessage]);

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    if (socketRef.current) {
      socketRef.current.close(1000, 'User disconnect');
      socketRef.current = null;
    }
    setConnectionStatus('disconnected');
  }, []);

  // Initialize connection on mount or loungeId change
  useEffect(() => {
    isUnmountedRef.current = false;

    if (autoConnect && loungeId) {
      connect();
    }

    return () => {
      isUnmountedRef.current = true;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = null;
      }
      typingTimeoutsRef.current.forEach((timeoutId) => clearTimeout(timeoutId));
      typingTimeoutsRef.current.clear();
      if (socketRef.current) {
        socketRef.current.close(1000, 'Component unmount');
        socketRef.current = null;
      }
    };
  }, [autoConnect, loungeId, connect]);

  return {
    connectionStatus,
    latencyMs,
    lastError,
    nowPlaying,
    queue,
    queueVersion,
    participants,
    chatMessages,
    typingUsers,
    soundclash,
    sentiment,
    hostStatus,
    voteTrack,
    sendChatMessage,
    sendTyping,
    sendReaction,
    sendPing,
    connect,
    disconnect,
  };
}

export default useLoungeWebSocket;
