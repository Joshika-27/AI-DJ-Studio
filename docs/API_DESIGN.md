# REST API & WebSocket Specification (Module 0)

This document defines the complete HTTP REST API (Task 7) and Real-Time WebSocket Contract (Task 8) for **AI DJ Studio**.

> [!IMPORTANT]
> - All **Mandatory APIs and WebSocket Contracts** explicitly specified in **PRD Section 7.3** (`7.3.1`–`7.3.4`) are preserved **verbatim** without altering field names, status codes, or payload shapes.
> - Additional endpoints required to fulfill the functional requirements in **PRD Sections 5, 6, and 10** are explicitly marked as **`[RECOMMENDED ENDPOINT — Derived from PRD Functional Requirements]`**.

---

## PART 1 — HTTP REST API DESIGN (Task 7)

### 1.1 Mandatory Endpoints (Explicitly Specified in PRD Section 7.3)

#### 1. `POST /api/v1/lounges` — `[MANDATORY — PRD Sec 7.3.1, REQ-057]`
- **Purpose:** Creates a new DJ Lounge. Called by the Host.
- **Authentication & RBAC:** Bearer JWT Required (`role ∈ {host, admin}`).
- **Request Structure (`application/json`):**
```json
{
  "seedTrackId": "t_2001",
  "vibeProfile": "High-Energy EDM",
  "maxParticipants": 40,
  "bpmTolerancePct": 8,
  "soundclashEnabled": true,
  "aiVerbosity": "normal"
}
```
- **Response Structure (`201 Created`):**
```json
{
  "loungeId": "lg_77120",
  "inviteCode": "SP-9X2L",
  "status": "scheduled",
  "hostUserId": "u_501",
  "createdAt": "2026-09-18T10:15:00Z"
}
```
- **Error Cases:**
  - `400 Bad Request`: Invalid `bpmTolerancePct <= 0`, `maxParticipants` out of range (`2..50`), or missing `seedTrackId`.
  - `401 Unauthorized`: Missing or expired JWT.
  - `403 Forbidden`: Caller role is `listener` or `co_dj`.
  - `404 Not Found`: `seedTrackId` does not exist in catalog.

---

#### 2. `GET /api/v1/tracks/{trackId}/acoustic-profile` — `[MANDATORY — PRD Sec 7.3.2, REQ-058]`
- **Purpose:** Returns a track's full acoustic feature vector and RAG metadata timeline. Used by the AI DJ Engine to evaluate segue candidates and ground transition scripts.
- **Authentication & RBAC:** Bearer JWT Required (`role ∈ {listener, co_dj, host, admin}`).
- **Response Structure (`200 OK`):**
```json
{
  "trackId": "t_2001",
  "title": "Night Drive",
  "artist": "Nova Circuit",
  "acoustic": {
    "bpm": 124,
    "camelotKey": "8A",
    "energy": 0.78,
    "valence": 0.65
  },
  "timeline": [
    {
      "ts": 12,
      "type": "sample_credit",
      "text": "Vocal chop sampled from a 1981 soul record."
    }
  ]
}
```
- **Error Cases:**
  - `401 Unauthorized`: Missing or invalid JWT.
  - `404 Not Found`: Track `trackId` not found.

---

#### 3. `POST /api/v1/lounges/{id}/ai/segue` — `[MANDATORY — PRD Sec 7.3.3, REQ-059]`
- **Purpose:** Requests the AI DJ's next-segue decision and generated transition script as the current track approaches its outro window (`< 3 seconds P95`).
- **Authentication & RBAC:** Bearer JWT Required (`role ∈ {co_dj, host, admin}` or internal service call).
- **Request Structure (`application/json`):**
```json
{
  "currentTrackId": "t_2001",
  "currentTs": 198,
  "candidateTrackIds": ["t_2002", "t_2003", "t_2004"],
  "currentSentimentScore": 0.72
}
```
- **Response Structure (`200 OK`):**
```json
{
  "selectedTrackId": "t_2003",
  "matchReason": {
    "bpmDeltaPct": 3.2,
    "camelotRelation": "adjacent",
    "energyFit": 0.81
  },
  "transitionScript": "Keeping the energy up — sliding into an 8A cut at a near-identical tempo, no brakes needed.",
  "sourceEvents": ["evt_5541"],
  "generatedAt": "2026-09-18T10:24:47Z"
}
```
- **Error Cases:**
  - `400 Bad Request`: Empty `candidateTrackIds` or `currentSentimentScore` outside `[0.0, 1.0]`.
  - `401 Unauthorized` / `403 Forbidden`: Unauthorized token.
  - `404 Not Found`: Lounge `{id}` or `currentTrackId` not found.
  - `422 Unprocessable Entity`: Zero candidates in `candidateTrackIds` satisfy the hard BPM (`±8%` / `×0.5` / `×2.0`) and Camelot Wheel compatibility rules (`REQ-009`, `REQ-031`, `REQ-032`).

---

### 1.2 Recommended Endpoints (Derived from PRD Functional & Non-Functional Requirements)

#### 4. Authentication & RBAC Endpoints (`REQ-069`, `REQ-070`) — `[RECOMMENDED ENDPOINTS]`
| Method | Endpoint | Purpose | Auth / Role | Request Body | Response (`200`/`201`) | Error Cases |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/auth/register` | Register a new user account | Public | `{ email, password, displayName, role? }` | `201`: `{ userId, email, displayName, role, accessToken, refreshToken, expiresIn: 3600 }` | `400` Validation error, `409` Email exists |
| `POST` | `/api/v1/auth/login` | Authenticate & issue short-lived JWT (`≤ 1h`) + refresh token | Public | `{ email, password }` | `200`: `{ accessToken, refreshToken, expiresIn: 3600, user: { id, email, displayName, role } }` | `401` Invalid credentials |
| `POST` | `/api/v1/auth/refresh` | Exchange valid refresh token for a new access JWT | Public (Refresh Token) | `{ refreshToken }` | `200`: `{ accessToken, expiresIn: 3600 }` | `401` Expired or revoked refresh token |
| `GET` | `/api/v1/auth/me` | Return current authenticated user profile | Bearer JWT (Any Role) | None | `200`: `{ id, email, displayName, role, preferences }` | `401` Invalid token |

#### 5. Track Catalog & Admin Acoustic Upload Endpoints (`REQ-046`, `REQ-049`) — `[RECOMMENDED ENDPOINTS]`
| Method | Endpoint | Purpose | Auth / Role | Request Body | Response (`200`/`201`) | Error Cases |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/tracks` | List all catalog tracks with acoustic vectors | Bearer JWT (Any Role) | Query params: `?camelotKey=8A&minBpm=115&maxBpm=130` | `200`: `{ tracks: [ { trackId, title, artist, durationSeconds, audioAssetUrl, acoustic, timeline } ] }` | `401` Unauthorized |
| `POST` | `/api/v1/admin/tracks` | Upload & validate a track's acoustic profile & RAG timeline (`REQ-046`, `REQ-049`) | Bearer JWT (`admin` only) | `{ trackId?, title, artist, durationSeconds, audioAssetUrl, acoustic: { bpm, camelotKey, energy, valence }, timeline: [ { ts, type, text } ] }` | `201`: `{ trackId, title, artist, acoustic, timeline, createdAt }` | `400` Invalid BPM (`<=0`), invalid Camelot key (not in 24 valid codes), or energy/valence outside `[0.0, 1.0]`; `403` Non-admin token |

#### 6. Lounge Lifecycle, Queue Override, Soundclash & Moderation Endpoints (`REQ-015`, `REQ-019`–`REQ-025`) — `[RECOMMENDED ENDPOINTS]`
| Method | Endpoint | Purpose | Auth / Role | Request Body | Response (`200`/`201`) | Error Cases |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/lounges` | List active and scheduled DJ Lounges | Bearer JWT (Any Role) | None | `200`: `{ lounges: [...] }` | `401` Unauthorized |
| `GET` | `/api/v1/lounges/{id}` | Fetch full current Lounge state (`Now Playing`, `Next Up`, `Soundclash`, `sentimentScore`, `participants`) | Bearer JWT (Any Role) | None | `200`: `{ loungeId, inviteCode, status, nowPlaying, nextUp, soundclash, sentimentScore, participants, settings }` | `404` Lounge not found |
| `POST` | `/api/v1/lounges/join/{inviteCode}` | Join a Lounge via invite code (`SP-9X2L`) with capacity & lock check (`REQ-019`) | Bearer JWT (Any Role) | None | `200`: `{ loungeId, status, wsUrl, snapshot: { nowPlaying, nextUp, soundclash, sentimentScore } }` | `403` Room locked or user kicked; `404` Invalid code; `409` Room at `maxParticipants` capacity |
| `POST` | `/api/v1/lounges/{id}/start` | Transition Lounge from `scheduled` to `live` and begin playback | Bearer JWT (`host`, `admin`) | None | `200`: `{ loungeId, status: "live", currentTrackId }` | `403` Not Host |
| `POST` | `/api/v1/lounges/{id}/end` | End Lounge session and persist session summary & analytics snapshot (`REQ-015`) | Bearer JWT (`host`, `admin`) | None | `200`: `{ loungeId, status: "ended", endedAt, summary }` | `403` Not Host |
| `POST` | `/api/v1/lounges/{id}/queue` | Add a track to `Next Up` queue | Bearer JWT (Any Role) | `{ trackId }` | `201`: `{ queue: [ { trackId, position, voteCount } ] }` | `400` Duplicate track; `403` Room frozen |
| `POST` | `/api/v1/lounges/{id}/queue/vote` | Upvote a track in `Next Up` and trigger atomic reorder (`REQ-021`) | Bearer JWT (Any Role) | `{ trackId }` | `200`: `{ queue: [ { trackId, position, voteCount } ] }` | `404` Track not in queue |
| `POST` | `/api/v1/lounges/{id}/queue/override` | Host/Co-DJ manual promotion, reorder, or segue override (`REQ-015`, `REQ-070`) | Bearer JWT (`co_dj`, `host`, `admin`) | `{ action: "promote" \| "remove" \| "force_segue", trackId }` | `200`: `{ nowPlaying, queue }` | `403` Listener token rejected |
| `POST` | `/api/v1/lounges/{id}/soundclash/submit` | Submit candidate track to Soundclash Stage (`REQ-022`) | Bearer JWT (Any Role) | `{ trackId }` | `201`: `{ candidateTrackIds, status }` | `400` Soundclash disabled or battle full (max 4) |
| `POST` | `/api/v1/lounges/{id}/soundclash/open` | Open timed Soundclash battle between `2–4` candidates (`REQ-038`) | Bearer JWT (`co_dj`, `host`, `admin`) | `{ candidateTrackIds, durationSeconds: 30 }` | `201`: `{ battleId, candidateTrackIds, closesAt }` | `400` Fewer than 2 or more than 4 candidates; `403` Unauthorized |
| `POST` | `/api/v1/lounges/{id}/soundclash/{battleId}/vote` | Vote on a candidate track in an open Soundclash battle | Bearer JWT (Any Role) | `{ trackId }` | `200`: `{ battleId, votes: { trackId: count } }` | `400` Battle closed; `404` Invalid candidate |
| `POST` | `/api/v1/lounges/{id}/soundclash/{battleId}/resolve` | Resolve Soundclash battle (or break tie) and splice winner into queue (`REQ-015`, `REQ-038`) | Bearer JWT (`co_dj`, `host`, `admin`) | `{ tieBreakTrackId? }` | `200`: `{ battleId, winningTrackId, announcement, queue }` | `403` Listener token rejected |
| `POST` | `/api/v1/lounges/{id}/moderation` | Host moderation controls (`mute`, `remove`, `lock`, `transfer_role`) (`REQ-025`) | Bearer JWT (`host`, `admin`) | `{ action: "mute"\|"unmute"\|"remove"\|"lock"\|"unlock"\|"set_role", targetUserId?, newRole? }` | `200`: `{ success: true, moderationState }` | `403` Non-host token rejected |

#### 7. Conversational RAG Q&A, Dashboard, Recommendations, Analytics & Health Endpoints (`REQ-037`, `REQ-043`–`REQ-045`, `REQ-079`) — `[RECOMMENDED ENDPOINTS]`
| Method | Endpoint | Purpose | Auth / Role | Request Body | Response (`200`/`201`) | Error Cases |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/lounges/{id}/ai/qa` | Ask AI DJ natural-language question about current/recent track with grounded citations (`REQ-037`, `REQ-040`) | Bearer JWT (Any Role) | `{ question: "Where is this sample from?", trackId? }` | `200`: `{ answer, trackId, sourceEvents: ["evt_5541"], grounded: true, generatedAt }` | `400` Empty question; `404` Track not found |
| `GET` | `/api/v1/users/me/dashboard` | Personal dashboard: lounge history, acoustic taste profile, active lounges (`REQ-043`) | Bearer JWT (Any Role) | None | `200`: `{ attendedLounges, hostedLounges, activeLounges, acousticTasteProfile: { bpmRange, preferredKeys, avgEnergy, avgValence } }` | `401` Unauthorized |
| `POST` | `/api/v1/interactions` | Record listening/co-listening interaction (`REQ-044`, `REQ-047`) | Bearer JWT (Any Role) | `{ trackId, loungeId?, playedSeconds, completed, liked, coListenedWith }` | `201`: `{ interactionId, recordedAt }` | `400` Validation error |
| `GET` | `/api/v1/recommendations` | Fetch personalized Hybrid `"Recommended for You"` rail (`REQ-044`, `REQ-010`) | Bearer JWT (Any Role) | Query: `?limit=5` | `200`: `{ recommendations: [ { trackId, title, artist, acoustic, score, contentScore, collabScore, coldStartMode, reason } ] }` | `401` Unauthorized |
| `GET` | `/api/v1/recommendations/evaluate` | Run chronological train/test split (`70/30`) offline `Precision@5` evaluation (`REQ-010`) | Bearer JWT (`admin`, `host`) | None | `200`: `{ meanPrecisionAt5, target: 0.6, passed: true, contentOnlyPrecisionAt5, collabOnlyPrecisionAt5, evaluatedUsers: [...] }` | `401` / `403` Unauthorized |
| `GET` | `/api/v1/lounges/{id}/analytics` | Fetch session analytics & sentiment timeline reconciled `100%` with event log (`REQ-045`, `REQ-048`) | Bearer JWT (Any Role) | None | `200`: `{ loungeId, durationSeconds, peakConcurrentParticipants, soundclashBattlesHeld, aiQuestionsAsked, sentimentTimeline: [ { ts, tsSeconds, rollingScore, sampleSize } ], reconciliation: { verified: true } }` | `404` Lounge not found |
| `GET` | `/api/v1/health` | API, Database, and WebSocket Gateway health check (`REQ-079`) | Public | None | `200`: `{ status: "ok", db: "connected", wsGateway: "ready", activeLounges: 2, uptimeSeconds: 120 }` | `503` DB unreachable |

---

## PART 2 — REAL-TIME WEBSOCKET DESIGN (Task 8)

### 2.1 Connection Endpoint, Room Scoping & Handshake Authentication (`REQ-060`, `REQ-071`)
- **WebSocket URL / Namespace (FastAPI Native WebSockets):**
  - `ws://<host>:<port>/ws/lounges/{loungeId}?token=<JWT>` (scoped per `loungeId` via the in-memory `ConnectionManager`).
- **Room / Channel Scoping:**
  - Every Lounge has an isolated room keyed by `loungeId` (e.g., `lg_77120`). Broadcasts never leak across lounges (`REQ-076`).
- **Handshake Authentication & Action Re-Validation:**
  1. At connection handshake, the gateway verifies the JWT signature and expiry (`≤ 1h`) and confirms the user is not kicked and the room is not locked/full. Invalid tokens immediately close the socket with code `4001 (Unauthorized)` or `4003 (Forbidden)`.
  2. When a connected client sends a privileged WebSocket event (e.g., `lounge.soundclash.open`, `lounge.soundclash.force_resolve`, `lounge.moderation.command`), the gateway re-validates the user's current role in the Lounge (`host` or `co_dj`) before executing.

---

### 2.2 Mandatory WebSocket Envelope & Core Server-to-Client Events (PRD Section 7.3.4 Verbatim)

Every WebSocket message shares the exact envelope shape specified in **PRD Section 7.3.4 (`REQ-060`)**:

```json
{
  "event": "<event.name>",
  "loungeId": "lg_77120",
  "payload": { ... },
  "ts": 1690000000123
}
```

#### Core Mandatory Events (`REQ-061` – `REQ-068`):

| Event Name | Direction | Exact Payload Contract (PRD Section 7.3.4) | Trigger Condition |
| :--- | :--- | :--- | :--- |
| `lounge.queue.update` | Server → Room | `{ "queue": [ { "trackId": "t_2003", "position": 1000 } ], "reason": "vote" \| "segue" \| "battle" }` | Broadcast atomically on any vote reorder, AI DJ segue advance, or Soundclash battle resolution (`< 100 ms` drift). |
| `lounge.presence.update` | Server → Room | `{ "participantId": "u_502", "action": "joined" \| "left", "participantCount": 12 }` | Broadcast when a participant joins or leaves (after reconnect grace window expires). |
| `lounge.chat.message` | Server → Room | `{ "messageId": "msg_901", "userId": "u_502", "body": "This drop is insane 🔥", "sentimentBucket": "positive", "tsSeconds": 145 }` | Broadcast when a non-muted user submits a chat message; includes real-time `sentimentBucket` (`positive` \| `neutral` \| `negative`). |
| `lounge.sentiment.score` | Server → Room | `{ "rollingScore": 0.78, "windowSeconds": 60, "sampleSize": 18 }` | Broadcast on rolling-window sentiment ticks and immediately upon significant chat tone shifts. |
| `lounge.soundclash.open` | Server → Room | `{ "battleId": "bt_301", "candidateTrackIds": ["t_2004", "t_2005"], "closesAt": 1690000030123 }` | Broadcast when Host, Co-DJ, or AI DJ opens a timed Soundclash battle (`2–4` candidates). |
| `lounge.soundclash.resolved` | Server → Room | `{ "battleId": "bt_301", "winningTrackId": "t_2004" }` | Broadcast simultaneously to all clients when a Soundclash battle finishes, alongside `lounge.queue.update` (`reason: 'battle'`). |
| `lounge.ai.dj_announcement` | Server → Room | `{ "text": "Keeping the energy up — sliding into an 8A cut...", "relatedTrackId": "t_2003", "sourceEvents": ["evt_5541"] }` | Broadcast when the AI DJ renders transition banter, answers a public Q&A, or announces a Soundclash winner. |
| `lounge.sync.ping` | Client → Server | `{ "clientSentAt": 1690000000100 }` | Sent periodically by client to measure RTT and clock offset. |
| `lounge.sync.pong` | Server → Client | `{ "clientSentAt": 1690000000100, "serverReceivedAt": 1690000000108 }` | Immediate server reply to `lounge.sync.ping` for connection quality indicator and sync drift tracking. |

---

### 2.3 Supporting Client-to-Server & Synchronization Events (`[RECOMMENDED WS EVENTS]`)

To support interactive voting, chat, typing indicators, emoji reactions, mid-session join snapshots (`REQ-028`), and Host disconnect alerts (`REQ-029`) over the same envelope:

| Event Name | Direction | Payload Structure | Purpose & Requirement Mapping |
| :--- | :--- | :--- | :--- |
| `lounge.state.snapshot` | Server → Client | `{ "nowPlaying": {...}, "queue": [...], "soundclash": {...}, "sentiment": {...}, "participants": [...], "chatHistory": [...], "hostStatus": "connected" \| "host_disconnected" }` | Sent immediately (`< 2 seconds`) to a newly connected or reconnecting client (`REQ-028`, `REQ-078`). |
| `lounge.queue.vote` | Client → Server | `{ "trackId": "t_2003" }` | Casts an upvote on a queued track; triggers atomic server re-sort and `lounge.queue.update` (`REQ-021`). |
| `lounge.chat.send` | Client → Server | `{ "body": "Love this synth line!", "tsSeconds": 145 }` | Submits a chat message (validated & XSS-sanitized server-side; rejected if user is muted) (`REQ-023`, `REQ-072`). |
| `lounge.chat.typing` | Client ↔ Room | `{ "userId": "u_502", "displayName": "Alex", "isTyping": true }` | Real-time typing indicator (`REQ-023`). |
| `lounge.chat.reaction` | Client ↔ Room | `{ "messageId": "msg_901", "userId": "u_502", "emoji": "🔥" }` | Real-time emoji reaction (`REQ-023`). |
| `lounge.soundclash.vote` | Client → Server | `{ "battleId": "bt_301", "trackId": "t_2004" }` | Casts a listener vote in an active Soundclash battle (`REQ-022`). |
| `lounge.soundclash.tally` | Server → Room | `{ "battleId": "bt_301", "votes": { "t_2004": 8, "t_2005": 5 } }` | Real-time live vote tally update during an open battle (`REQ-038`). |
| `lounge.host.status` | Server → Room | `{ "status": "host_disconnected" \| "host_reconnected" \| "co_dj_promoted", "activeHostUserId": "u_503", "mutationsFrozen": false }` | Deterministic Host disconnect/promotion notification (`REQ-029`). |
| `lounge.error` | Server → Client | `{ "code": "FORBIDDEN" \| "MUTED" \| "INVALID_PAYLOAD", "message": "Only Host or Co-DJ may resolve a battle." }` | Structured WebSocket error response without dropping the socket. |

---

### 2.4 Connection Lifecycle, Synchronization & Reconnection Strategy (`REQ-028`, `REQ-029`, `REQ-078`)

```mermaid
sequenceDiagram
    participant C as Client (Listener / Host)
    participant GW as WebSocket Queue Gateway
    participant RM as In-Memory RoomStateManager

    C->>GW: Connect /ws/lounges/lg_77120?token=<JWT>
    GW->>GW: Verify JWT signature, expiry (<=1h) & room lock/capacity
    GW->>RM: registerParticipant(lg_77120, user)
    alt Reconnecting within 30s Grace Window
        RM-->>GW: Restored session (suppress duplicate 'joined' event)
    else Fresh Join
        RM-->>GW: New session
        GW-->>C: Broadcast lounge.presence.update { action: 'joined', participantCount }
    end
    GW-->>C: Send lounge.state.snapshot (< 2 seconds: Now Playing, Next Up, Soundclash, Sentiment, Chat)
    loop Every 10 seconds
        C->>GW: lounge.sync.ping { clientSentAt }
        GW-->>C: lounge.sync.pong { clientSentAt, serverReceivedAt }
    end
    Note over C,RM: If Host Socket Drops Unexpectedly
    GW->>RM: handleDisconnect(lg_77120, hostUserId)
    RM->>RM: Enter 'host_disconnected' state; promote active Co-DJ or freeze queue mutations
    GW-->>C: Broadcast lounge.host.status { status: 'host_disconnected', ... }
```
