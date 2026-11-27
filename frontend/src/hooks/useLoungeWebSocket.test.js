import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// ----------------------------------------------------------------------------
// React Dispatcher Mock for Node Environment
// ----------------------------------------------------------------------------
let activeDispatcher = null;

vi.mock('react', async () => {
  const actual = await vi.importActual('react');
  return {
    ...actual,
    useState: (init) => {
      if (!activeDispatcher) {
        throw new Error('No active dispatcher: useState called outside hook harness');
      }
      return activeDispatcher.useState(init);
    },
    useRef: (init) => {
      if (!activeDispatcher) {
        throw new Error('No active dispatcher: useRef called outside hook harness');
      }
      return activeDispatcher.useRef(init);
    },
    useCallback: (fn, deps) => {
      if (!activeDispatcher) {
        throw new Error('No active dispatcher: useCallback called outside hook harness');
      }
      return activeDispatcher.useCallback(fn, deps);
    },
    useEffect: (effect, deps) => {
      if (!activeDispatcher) {
        throw new Error('No active dispatcher: useEffect called outside hook harness');
      }
      return activeDispatcher.useEffect(effect, deps);
    },
  };
});

import {
  getWebSocketUrl,
  makeWsEnvelope,
  TERMINAL_CLOSE_CODES,
  useLoungeWebSocket,
} from './useLoungeWebSocket';

// ----------------------------------------------------------------------------
// Mock WebSocket Implementation for Node Environment
// ----------------------------------------------------------------------------
class MockWebSocket {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = 0; // CONNECTING
    this.sentMessages = [];
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.onclose = null;
    MockWebSocket.instances.push(this);
  }

  simulateOpen() {
    this.readyState = 1; // OPEN
    if (this.onopen) {
      this.onopen();
    }
  }

  simulateMessage(eventData) {
    if (this.onmessage) {
      const data = typeof eventData === 'string' ? eventData : JSON.stringify(eventData);
      this.onmessage({ data });
    }
  }

  simulateError(err = new Error('Socket failure')) {
    if (this.onerror) {
      this.onerror(err);
    }
  }

  simulateClose(code = 1000, reason = 'Normal Closure') {
    this.readyState = 3; // CLOSED
    if (this.onclose) {
      this.onclose({ code, reason });
    }
  }

  send(data) {
    if (this.readyState !== 1) {
      throw new Error('WebSocket is not open: readyState ' + this.readyState);
    }
    this.sentMessages.push(data);
  }

  close(code = 1000, reason = '') {
    this.readyState = 3;
    if (this.onclose) {
      this.onclose({ code, reason });
    }
  }
}

// ----------------------------------------------------------------------------
// Faithful Pure-Node React Hook Test Harness
// ----------------------------------------------------------------------------
function createHookHarness(hookFn) {
  let stateSlots = [];
  let stateIndex = 0;
  let refSlots = [];
  let refIndex = 0;
  let callbackSlots = [];
  let callbackIndex = 0;
  let effectSlots = [];
  let effectIndex = 0;
  let cleanups = [];
  let latestResult = null;
  let isMounted = false;
  let isRendering = false;
  let isFlushingEffects = false;
  let pendingUpdate = false;

  const dispatcher = {
    useState(initialValue) {
      const idx = stateIndex++;
      if (idx >= stateSlots.length) {
        stateSlots[idx] = typeof initialValue === 'function' ? initialValue() : initialValue;
      }
      const setState = (action) => {
        const prev = stateSlots[idx];
        const nextValue = typeof action === 'function' ? action(prev) : action;
        if (Object.is(nextValue, prev)) {
          return;
        }
        stateSlots[idx] = nextValue;
        if (isMounted && !isRendering && !isFlushingEffects) {
          render();
        } else {
          pendingUpdate = true;
        }
      };
      return [stateSlots[idx], setState];
    },

    useRef(initialValue) {
      const idx = refIndex++;
      if (idx >= refSlots.length) {
        refSlots[idx] = { current: initialValue };
      }
      return refSlots[idx];
    },

    useCallback(fn, deps) {
      const idx = callbackIndex++;
      const prev = callbackSlots[idx];
      const hasChanged = !prev || !deps || deps.some((d, i) => !Object.is(d, prev.deps[i]));
      if (hasChanged) {
        callbackSlots[idx] = { fn, deps };
        return fn;
      }
      return prev.fn;
    },

    useEffect(effect, deps) {
      const idx = effectIndex++;
      const prev = effectSlots[idx];
      const hasChanged = !prev || !deps || deps.some((d, i) => !Object.is(d, prev.deps[i]));

      if (hasChanged) {
        effectSlots[idx] = { effect, deps, pending: true };
      }
    },
  };

  function flushEffects() {
    isFlushingEffects = true;
    try {
      for (let i = 0; i < effectSlots.length; i++) {
        const item = effectSlots[i];
        if (item && item.pending) {
          if (cleanups[i]) {
            try {
              cleanups[i]();
            } catch {}
            cleanups[i] = null;
          }
          item.pending = false;
          const cleanup = item.effect();
          if (typeof cleanup === 'function') {
            cleanups[i] = cleanup;
          }
        }
      }
    } finally {
      isFlushingEffects = false;
    }

    if (pendingUpdate && isMounted) {
      pendingUpdate = false;
      render();
    }
  }

  function render(props) {
    stateIndex = 0;
    refIndex = 0;
    callbackIndex = 0;
    effectIndex = 0;
    activeDispatcher = dispatcher;
    isRendering = true;
    try {
      latestResult = hookFn(props);
    } finally {
      isRendering = false;
      activeDispatcher = null;
    }
    flushEffects();
    return latestResult;
  }

  function unmount() {
    isMounted = false;
    for (let i = 0; i < cleanups.length; i++) {
      if (cleanups[i]) {
        try {
          cleanups[i]();
        } catch {}
        cleanups[i] = null;
      }
    }
  }

  isMounted = true;
  return {
    render,
    unmount,
    get result() {
      return latestResult;
    },
  };
}

describe('useLoungeWebSocket (Module 8.6A)', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  // --------------------------------------------------------------------------
  // URL & Envelope Helper Tests
  // --------------------------------------------------------------------------
  describe('Helper Functions', () => {
    it('constructs canonical ws:// and wss:// URLs correctly', () => {
      const url1 = getWebSocketUrl('lounge_alpha', 'token_123');
      expect(url1).toBe('ws://localhost:8000/ws/lounges/lounge_alpha?token=token_123');

      // Test with encoded characters in token
      const url2 = getWebSocketUrl('lounge_beta', 'tok/en+test==');
      expect(url2).toBe(
        'ws://localhost:8000/ws/lounges/lounge_beta?token=tok%2Fen%2Btest%3D%3D'
      );

      // Empty loungeId returns empty string
      expect(getWebSocketUrl('', 'token_123')).toBe('');
      expect(getWebSocketUrl(null, 'token_123')).toBe('');
    });

    it('creates canonical envelopes conforming to REQ-060', () => {
      const raw = makeWsEnvelope('lounge.queue.vote', 'lg_1', { trackId: 't_42' });
      const parsed = JSON.parse(raw);
      expect(parsed.event).toBe('lounge.queue.vote');
      expect(parsed.loungeId).toBe('lg_1');
      expect(parsed.payload).toEqual({ trackId: 't_42' });
      expect(typeof parsed.ts).toBe('number');
      expect(parsed.ts).toBeGreaterThan(0);
    });

    it('identifies terminal WebSocket close codes', () => {
      expect(TERMINAL_CLOSE_CODES.has(4001)).toBe(true);
      expect(TERMINAL_CLOSE_CODES.has(4003)).toBe(true);
      expect(TERMINAL_CLOSE_CODES.has(4004)).toBe(true);
      expect(TERMINAL_CLOSE_CODES.has(4009)).toBe(true);
      expect(TERMINAL_CLOSE_CODES.has(1000)).toBe(false);
      expect(TERMINAL_CLOSE_CODES.has(1006)).toBe(false);
    });
  });

  // --------------------------------------------------------------------------
  // Connection Lifecycle & Authentication Tests
  // --------------------------------------------------------------------------
  describe('Connection Lifecycle', () => {
    it('sets error status when no access token is available', () => {
      const harness = createHookHarness((props) =>
        useLoungeWebSocket('lounge_alpha', {
          WebSocketClass: MockWebSocket,
          tokenProvider: () => null,
          ...props,
        })
      );

      harness.render();
      expect(harness.result.connectionStatus).toBe('error');
      expect(harness.result.lastError.code).toBe('AUTH_REQUIRED');
      expect(MockWebSocket.instances.length).toBe(0);
      harness.unmount();
    });

    it('initiates WebSocket connection with query token and transitions to connected on open', () => {
      const harness = createHookHarness((props) =>
        useLoungeWebSocket('lounge_alpha', {
          WebSocketClass: MockWebSocket,
          tokenProvider: () => 'valid_access_jwt',
          ...props,
        })
      );

      harness.render();
      expect(harness.result.connectionStatus).toBe('connecting');
      expect(MockWebSocket.instances.length).toBe(1);

      const ws = MockWebSocket.instances[0];
      expect(ws.url).toContain('/ws/lounges/lounge_alpha?token=valid_access_jwt');

      // Simulate connection open
      ws.simulateOpen();
      expect(harness.result.connectionStatus).toBe('connected');
      expect(harness.result.lastError).toBeNull();

      harness.unmount();
    });

    it('handles terminal close code 4001 without reconnecting', () => {
      const harness = createHookHarness((props) =>
        useLoungeWebSocket('lounge_alpha', {
          WebSocketClass: MockWebSocket,
          tokenProvider: () => 'expired_jwt',
          ...props,
        })
      );

      harness.render();
      const ws = MockWebSocket.instances[0];
      ws.simulateOpen();

      // Server terminates with 4001 Unauthorized
      ws.simulateClose(4001, 'Unauthorized');
      expect(harness.result.connectionStatus).toBe('error');
      expect(harness.result.lastError.code).toBe('TERMINAL_CLOSE_4001');

      // Fast forward time: no reconnect should be scheduled
      vi.advanceTimersByTime(5000);
      expect(MockWebSocket.instances.length).toBe(1);

      harness.unmount();
    });

    it('handles recoverable close with exponential reconnect and fresh token retrieval', () => {
      let tokenCallCount = 0;
      const harness = createHookHarness((props) =>
        useLoungeWebSocket('lounge_alpha', {
          WebSocketClass: MockWebSocket,
          tokenProvider: () => {
            tokenCallCount += 1;
            return `token_attempt_${tokenCallCount}`;
          },
          reconnectGraceMs: 15000,
          ...props,
        })
      );

      harness.render();
      expect(tokenCallCount).toBe(1);
      const ws1 = MockWebSocket.instances[0];
      ws1.simulateOpen();

      // Abnormal drop (e.g. 1006)
      ws1.simulateClose(1006, 'Abnormal Closure');
      expect(harness.result.connectionStatus).toBe('reconnecting');

      // First reconnect delay (1000ms)
      vi.advanceTimersByTime(1000);
      expect(MockWebSocket.instances.length).toBe(2);
      expect(tokenCallCount).toBe(2);
      const ws2 = MockWebSocket.instances[1];
      expect(ws2.url).toContain('token=token_attempt_2');

      ws2.simulateOpen();
      expect(harness.result.connectionStatus).toBe('connected');

      harness.unmount();
    });

    it('stops reconnecting when the 15-second grace window is exceeded', () => {
      const harness = createHookHarness((props) =>
        useLoungeWebSocket('lounge_alpha', {
          WebSocketClass: MockWebSocket,
          tokenProvider: () => 'test_token',
          reconnectGraceMs: 5000,
          ...props,
        })
      );

      harness.render();
      const ws1 = MockWebSocket.instances[0];
      ws1.simulateOpen();

      // Drop connection on ws1
      ws1.simulateClose(1006, 'Network down');
      expect(harness.result.connectionStatus).toBe('reconnecting');

      // First retry at +1000ms -> ws2 fails as well
      vi.advanceTimersByTime(1000);
      expect(MockWebSocket.instances.length).toBe(2);
      const ws2 = MockWebSocket.instances[1];
      ws2.simulateClose(1006, 'Still down');

      // Second retry at +2000ms (t = 3000ms) -> ws3 fails as well
      vi.advanceTimersByTime(2000);
      expect(MockWebSocket.instances.length).toBe(3);
      const ws3 = MockWebSocket.instances[2];
      ws3.simulateClose(1006, 'Still down');

      // Third retry at +4000ms (t = 7000ms > 5000ms grace window) -> RECONNECT_TIMEOUT
      vi.advanceTimersByTime(4000);
      expect(harness.result.connectionStatus).toBe('disconnected');
      expect(harness.result.lastError.code).toBe('RECONNECT_TIMEOUT');

      harness.unmount();
    });

    it('cleans up socket with code 1000 on unmount', () => {
      const harness = createHookHarness((props) =>
        useLoungeWebSocket('lounge_alpha', {
          WebSocketClass: MockWebSocket,
          tokenProvider: () => 'test_token',
          ...props,
        })
      );

      harness.render();
      const ws = MockWebSocket.instances[0];
      ws.simulateOpen();

      harness.unmount();
      expect(ws.readyState).toBe(3); // CLOSED
    });
  });

  // --------------------------------------------------------------------------
  // Inbound Event Handling Tests
  // --------------------------------------------------------------------------
  describe('Inbound Events', () => {
    let harness;
    let ws;

    beforeEach(() => {
      harness = createHookHarness((props) =>
        useLoungeWebSocket('lounge_alpha', {
          WebSocketClass: MockWebSocket,
          tokenProvider: () => 'test_token',
          ...props,
        })
      );
      harness.render();
      ws = MockWebSocket.instances[0];
      ws.simulateOpen();
    });

    afterEach(() => {
      harness.unmount();
    });

    it('handles lounge.state.snapshot correctly populating all room state fields', () => {
      ws.simulateMessage({
        event: 'lounge.state.snapshot',
        loungeId: 'lounge_alpha',
        payload: {
          nowPlaying: {
            trackId: 't_np_1',
            title: 'Subzero Drift',
            artist: 'Arctic Pulse',
            bpm: 124.0,
            camelotKey: '8A',
            energy: 0.82,
            valence: 0.65,
          },
          queue: [
            {
              queueEntryId: 'qe_1',
              trackId: 't_next_1',
              title: 'Neon Horizon',
              artist: 'Solaris',
              position: 1000,
              voteCount: 3,
            },
          ],
          queueVersion: 1,
          participants: [
            { userId: 'u_host', displayName: 'DJ Nova', role: 'host' },
            { userId: 'u_listener', displayName: 'Alex', role: 'listener' },
          ],
          chatHistory: [
            {
              id: 'msg_1',
              userId: 'u_host',
              displayName: 'DJ Nova',
              body: 'Welcome to the lounge!',
              sentimentBucket: 'positive',
              createdAt: '2026-11-26T10:00:00Z',
            },
          ],
          soundclash: { active: false, battleId: null, candidates: [], votes: {} },
          sentiment: { rollingScore: 0.72, windowSeconds: 60, sampleSize: 12 },
          hostStatus: 'connected',
        },
      });

      expect(harness.result.nowPlaying?.title).toBe('Subzero Drift');
      expect(harness.result.queue.length).toBe(1);
      expect(harness.result.queue[0].title).toBe('Neon Horizon');
      expect(harness.result.queueVersion).toBe(1);
      expect(harness.result.participants.length).toBe(2);
      expect(harness.result.chatMessages.length).toBe(1);
      expect(harness.result.chatMessages[0].body).toBe('Welcome to the lounge!');
      expect(harness.result.sentiment.rollingScore).toBe(0.72);
      expect(harness.result.hostStatus.mutationsFrozen).toBe(false);
    });

    it('updates queue on lounge.queue.update and guards against stale out-of-order versions', () => {
      // First update at version 5
      ws.simulateMessage({
        event: 'lounge.queue.update',
        loungeId: 'lounge_alpha',
        payload: {
          version: 5,
          queue: [{ queueEntryId: 'qe_v5', trackId: 't_5', position: 1000, voteCount: 5 }],
          reason: 'vote',
        },
      });

      expect(harness.result.queueVersion).toBe(5);
      expect(harness.result.queue[0].queueEntryId).toBe('qe_v5');

      // Stale update at version 4 (arrives late over network) -> MUST BE DROPPED!
      ws.simulateMessage({
        event: 'lounge.queue.update',
        loungeId: 'lounge_alpha',
        payload: {
          version: 4,
          queue: [{ queueEntryId: 'qe_stale_v4', trackId: 't_4', position: 1000, voteCount: 1 }],
          reason: 'vote',
        },
      });

      // Still at version 5!
      expect(harness.result.queueVersion).toBe(5);
      expect(harness.result.queue[0].queueEntryId).toBe('qe_v5');

      // Newer update at version 6 -> Accepted!
      ws.simulateMessage({
        event: 'lounge.queue.update',
        loungeId: 'lounge_alpha',
        payload: {
          version: 6,
          queue: [{ queueEntryId: 'qe_v6', trackId: 't_6', position: 1000, voteCount: 8 }],
          reason: 'vote',
        },
      });
      expect(harness.result.queueVersion).toBe(6);
      expect(harness.result.queue[0].queueEntryId).toBe('qe_v6');
    });

    it('handles lounge.presence.update for user joined and left', () => {
      // Initial user
      ws.simulateMessage({
        event: 'lounge.presence.update',
        loungeId: 'lounge_alpha',
        payload: {
          participantId: 'u_new_user',
          displayName: 'New Listener',
          action: 'joined',
          participantCount: 1,
          role: 'listener',
        },
      });

      expect(harness.result.participants.length).toBe(1);
      expect(harness.result.participants[0].userId).toBe('u_new_user');

      // User leaves
      ws.simulateMessage({
        event: 'lounge.presence.update',
        loungeId: 'lounge_alpha',
        payload: {
          participantId: 'u_new_user',
          action: 'left',
          participantCount: 0,
        },
      });

      expect(harness.result.participants.length).toBe(0);
    });

    it('handles lounge.chat.message and deduplicates messages', () => {
      const msgPayload = {
        messageId: 'msg_101',
        userId: 'u_fan',
        displayName: 'BassHead',
        body: 'This beat is incredible! 🔥',
        sentimentBucket: 'positive',
        tsSeconds: 1700000000,
        createdAt: '2026-11-26T10:15:00Z',
      };

      ws.simulateMessage({
        event: 'lounge.chat.message',
        loungeId: 'lounge_alpha',
        payload: msgPayload,
      });

      expect(harness.result.chatMessages.length).toBe(1);
      expect(harness.result.chatMessages[0].body).toBe('This beat is incredible! 🔥');

      // Duplicate delivery of msg_101
      ws.simulateMessage({
        event: 'lounge.chat.message',
        loungeId: 'lounge_alpha',
        payload: msgPayload,
      });

      expect(harness.result.chatMessages.length).toBe(1);
    });

    it('handles lounge.chat.typing and expires typing users after timeout', () => {
      ws.simulateMessage({
        event: 'lounge.chat.typing',
        loungeId: 'lounge_alpha',
        payload: {
          userId: 'u_typing_1',
          displayName: 'Alex',
          isTyping: true,
          action: 'started',
        },
      });

      expect(harness.result.typingUsers.length).toBe(1);
      expect(harness.result.typingUsers[0].userId).toBe('u_typing_1');

      // Fast forward past TYPING_TIMEOUT_MS (4000ms)
      vi.advanceTimersByTime(4500);
      expect(harness.result.typingUsers.length).toBe(0);
    });

    it('handles lounge.chat.reaction toggling and aggregation', () => {
      // Add message
      ws.simulateMessage({
        event: 'lounge.chat.message',
        loungeId: 'lounge_alpha',
        payload: {
          messageId: 'msg_react_test',
          userId: 'u_1',
          displayName: 'User 1',
          body: 'Check this reaction out',
        },
      });

      // User 2 adds fire reaction
      ws.simulateMessage({
        event: 'lounge.chat.reaction',
        loungeId: 'lounge_alpha',
        payload: {
          messageId: 'msg_react_test',
          userId: 'u_2',
          emoji: '🔥',
          action: 'add',
        },
      });

      expect(harness.result.chatMessages[0].reactions['🔥']).toEqual(['u_2']);

      // User 3 also adds fire reaction
      ws.simulateMessage({
        event: 'lounge.chat.reaction',
        loungeId: 'lounge_alpha',
        payload: {
          messageId: 'msg_react_test',
          userId: 'u_3',
          emoji: '🔥',
          action: 'add',
        },
      });

      expect(harness.result.chatMessages[0].reactions['🔥']).toEqual(['u_2', 'u_3']);

      // User 2 removes fire reaction
      ws.simulateMessage({
        event: 'lounge.chat.reaction',
        loungeId: 'lounge_alpha',
        payload: {
          messageId: 'msg_react_test',
          userId: 'u_2',
          emoji: '🔥',
          action: 'remove',
        },
      });

      expect(harness.result.chatMessages[0].reactions['🔥']).toEqual(['u_3']);
    });

    it('handles lounge.host.status and toggles mutationsFrozen', () => {
      ws.simulateMessage({
        event: 'lounge.host.status',
        loungeId: 'lounge_alpha',
        payload: {
          status: 'host_disconnected',
          activeHostUserId: null,
          mutationsFrozen: true,
        },
      });

      expect(harness.result.hostStatus.status).toBe('host_disconnected');
      expect(harness.result.hostStatus.mutationsFrozen).toBe(true);

      // Reconnection unfreezes
      ws.simulateMessage({
        event: 'lounge.host.status',
        loungeId: 'lounge_alpha',
        payload: {
          status: 'host_reconnected',
          activeHostUserId: 'u_host',
          mutationsFrozen: false,
        },
      });

      expect(harness.result.hostStatus.status).toBe('host_reconnected');
      expect(harness.result.hostStatus.mutationsFrozen).toBe(false);
    });

    it('calculates latency on lounge.sync.pong', () => {
      const now = Date.now();
      ws.simulateMessage({
        event: 'lounge.sync.pong',
        loungeId: 'lounge_alpha',
        payload: {
          clientSentAt: now - 35,
          serverReceivedAt: now - 15,
        },
      });

      expect(harness.result.latencyMs).toBeGreaterThanOrEqual(35);
    });

    it('captures domain error on lounge.error', () => {
      ws.simulateMessage({
        event: 'lounge.error',
        loungeId: 'lounge_alpha',
        payload: {
          code: 'PERMISSION_DENIED',
          message: 'Only Host/Co-DJ can perform this action',
        },
      });

      expect(harness.result.lastError).toEqual({
        code: 'PERMISSION_DENIED',
        message: 'Only Host/Co-DJ can perform this action',
      });
    });
  });

  // --------------------------------------------------------------------------
  // Outgoing Action Commands Tests
  // --------------------------------------------------------------------------
  describe('Outbound Actions', () => {
    let harness;
    let ws;

    beforeEach(() => {
      harness = createHookHarness((props) =>
        useLoungeWebSocket('lounge_alpha', {
          WebSocketClass: MockWebSocket,
          tokenProvider: () => 'test_token',
          ...props,
        })
      );
      harness.render();
      ws = MockWebSocket.instances[0];
      ws.simulateOpen();
    });

    afterEach(() => {
      harness.unmount();
    });

    it('voteTrack sends canonical lounge.queue.vote envelope and is blocked during freeze', () => {
      const sent = harness.result.voteTrack('track_101');
      expect(sent).toBe(true);
      expect(ws.sentMessages.length).toBe(1);

      const parsed = JSON.parse(ws.sentMessages[0]);
      expect(parsed.event).toBe('lounge.queue.vote');
      expect(parsed.loungeId).toBe('lounge_alpha');
      expect(parsed.payload).toEqual({ trackId: 'track_101' });

      // Freeze mutations
      ws.simulateMessage({
        event: 'lounge.host.status',
        loungeId: 'lounge_alpha',
        payload: { status: 'host_disconnected', mutationsFrozen: true },
      });

      const blockedVote = harness.result.voteTrack('track_102');
      expect(blockedVote).toBe(false);
      expect(harness.result.lastError.code).toBe('MUTATION_FROZEN');
      // No new message sent
      expect(ws.sentMessages.length).toBe(1);
    });

    it('sendChatMessage sends lounge.chat.message with trimmed body', () => {
      const sent = harness.result.sendChatMessage('   Hello DJ Lounge!   ');
      expect(sent).toBe(true);

      const parsed = JSON.parse(ws.sentMessages[0]);
      expect(parsed.event).toBe('lounge.chat.message');
      expect(parsed.payload.body).toBe('Hello DJ Lounge!');

      // Empty or whitespace-only message returns false
      expect(harness.result.sendChatMessage('   ')).toBe(false);
    });

    it('sendTyping sends lounge.chat.typing envelope', () => {
      harness.result.sendTyping(true);
      const parsed1 = JSON.parse(ws.sentMessages[0]);
      expect(parsed1.event).toBe('lounge.chat.typing');
      expect(parsed1.payload.isTyping).toBe(true);

      harness.result.sendTyping(false);
      const parsed2 = JSON.parse(ws.sentMessages[1]);
      expect(parsed2.payload.isTyping).toBe(false);
    });

    it('sendReaction sends lounge.chat.reaction with target emoji and action', () => {
      harness.result.sendReaction('msg_abc', '🔥', 'toggle');
      const parsed = JSON.parse(ws.sentMessages[0]);
      expect(parsed.event).toBe('lounge.chat.reaction');
      expect(parsed.payload).toEqual({
        messageId: 'msg_abc',
        emoji: '🔥',
        action: 'toggle',
      });
    });

    it('sendPing sends lounge.sync.ping with client timestamp', () => {
      harness.result.sendPing();
      const parsed = JSON.parse(ws.sentMessages[0]);
      expect(parsed.event).toBe('lounge.sync.ping');
      expect(typeof parsed.payload.clientSentAt).toBe('number');
    });
  });
});
