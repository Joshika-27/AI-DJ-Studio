# Project Requirements Specification (Module 0)

**Project Title:** AI DJ Studio: Collaborative Live Queue Curation & Sentiment-Aware Audio Engine  
**Source of Truth:** `0e1d3119ff014db328f80dad75feb8cb.docx` (Spotify Product & Platform Engineering PRD v1.0, September 2026)  
**Target Delivery:** 4-Week Single-Engineer Build (Advanced Track)

---

## 1. Project Purpose, Problem Statement & Proposed Solution

### 1.1 Problem Statement (PRD Section 1.1)
Traditional shared playlists are static and collaborative in name only:
1. **No Context or Adaptability:** Adding a track to a queue is a one-time, context-free action that does not adapt to how the room is actually feeling.
2. **No Acoustic Awareness:** Shared queues do not respect harmonic or tempo compatibility between consecutive tracks, routinely following a high-energy track with something jarring and off-tempo.
3. **No Sentiment Awareness:** Shared queues have no way to detect when the room's energy is dropping until participants start leaving.
4. **No Narrative Layer:** Shared queues never explain themselves—they play whatever is next without the narrative energy or transition storytelling ("banter") of a live DJ set.

### 1.2 Project Vision & Proposed Solution (PRD Section 1.2 & Section 2)
Build a working, end-to-end, real-time AI-orchestrated **"DJ Lounge"**—a collaborative listening room where:
- The queue is a live, continuously-reordered state machine (`Now Playing`, `Next Up`, and `Soundclash Stage`) rather than a static playlist.
- An **AI DJ Engine** continuously ingests each track's acoustic feature vector (`BPM`, `Camelot Key`, `Energy`, `Valence`) and timestamped metadata timeline (`sample_credit`, `trivia`, `glossary`).
- A **Live Chat-Sentiment Aggregator** scores rolling room chat messages (`positive`, `neutral`, `negative` energy) and feeds that score into queue curation.
- Segue selection enforces deterministic **BPM and Camelot Wheel harmonic compatibility** rules and generates short, **RAG-grounded transition banter** and **conversational Q&A** with explicit metadata citations.
- Crowd-submitted **Soundclash queue battles** are opened, voted on in real time, resolved atomically, and announced by the AI DJ.
- A **User Dashboard & Co-Listening Analytics** layer provides session history, acoustic taste profiles, session sentiment timelines, an authenticated Admin acoustic-profile upload interface, and a **Hybrid Recommendation Engine** (content-based + collaborative filtering).

---

## 2. Mandatory Requirements vs. Engineering Recommendations

> [!IMPORTANT]
> Throughout all Module 0 documents, **Mandatory Requirements** come directly from the PRD (`REQ-001` through `REQ-095`). **Engineering Recommendations** are marked explicitly as `[RECOMMENDATION]` to specify concrete implementation choices within the boundaries permitted by the PRD. The project's confirmed delivery track is **Track A — Full-Stack** and the confirmed stack is **Option C — Python Full-Stack (React, Python FastAPI, PostgreSQL, SQLAlchemy, Native FastAPI WebSockets, JWT Authentication & 4-Role RBAC, Pytest)** in a **5-Layer Modular Monolith**.

---

## 3. Complete Extracted Requirements Catalog (`REQ-001` – `REQ-095`)

### 3.1 Purpose, Vision & Key Performance Indicators (PRD Sections 1 & 2)

- **REQ-001 (End-to-End Working System):** Deliver a single, working, end-to-end system (not a mockup or disconnected demos) enabling a user to create a DJ Lounge, join from a second session, watch the live queue reorder on votes and Soundclash submissions, trigger AI DJ transitions between tracks, and observe the sentiment engine react to chat tone shifts, backed by a real database and API. *(PRD Sec 1.1, 1.2, 2)*
- **REQ-002 (Interactive Client or Backend Harness):** Provide an interactive DJ console/dashboard client with Now Playing panel, Next Up queue, live Soundclash battle stage, chat with visible sentiment indicator, and AI DJ transition banter feed for Track A, or equivalent automated tests, Postman collection, and scripted WebSocket crowd clients for Track B. *(PRD Sec 2, 4.3)*
- **REQ-003 (Core Architectural Subsystems):** Implement all six concrete subsystems: (1) Interactive Client / Test Harness, (2) REST API Layer, (3) Real-Time WebSocket Queue & State Gateway, (4) AI DJ Engine, (5) Relational or Document Database, (6) Hybrid Recommendation Engine & Authenticated Admin Acoustic Upload Interface. *(PRD Sec 2)*
- **REQ-004 (Self-Sourced Audio & Acoustic Metadata):** Source or produce royalty-free/local audio files and author or extract their acoustic feature profiles (`BPM`, `Camelot Key`, `Energy`, `Valence`) and RAG metadata timelines (`sample_credit`, `trivia`, `glossary`) without using licensed/copyrighted commercial music. *(PRD Sec 2, 4.1, 4.2, 14.1)*
- **REQ-005 (KPI — Queue State Sync Drift):** Maximum delay between a queue-mutating event (reorder, vote, battle resolution) and identical reflection across all connected clients must be `< 100 ms` under normal network conditions. *(PRD Sec 1.3, 6.1, 10.2)*
- **REQ-006 (KPI — AI DJ Transition Generation Latency):** Time from a track reaching its outro window to the AI DJ's rendered transition script and segue decision must be `< 3 seconds (P95)`. *(PRD Sec 1.3, 10.2)*
- **REQ-007 (KPI — Room Capacity Design Target):** Single DJ Lounge architecture must be designed to support `50 concurrent participants per room`. *(PRD Sec 1.3, 10.2)*
- **REQ-008 (KPI — Sentiment Engine Accuracy):** Percentage of chat messages correctly bucketed into `positive`, `neutral`, or `negative` energy against a manually labeled test set must be `≥ 85%`. *(PRD Sec 1.3)*
- **REQ-009 (KPI — Harmonic Match Validity):** Percentage of AI-selected segues satisfying the BPM and Camelot-key compatibility rules in Section 6.2 must be `100%` (enforced as hard constraints in code, not soft preferences). *(PRD Sec 1.3, 6.2, 14.2)*
- **REQ-010 (KPI — Recommendation Relevance Offline Eval):** Offline evaluation `Precision@5` of the hybrid recommendation engine against a held-out self-generated interaction set must be `≥ 0.6`. *(PRD Sec 1.3)*

### 3.2 Scope, Boundaries, Tracks & Personas (PRD Sections 3, 4 & 5)

- **REQ-011 (Single-Operator Local/Private Deployment):** Build and run as a single-operator system in a local or private self-hosted/containerized environment supporting one or more concurrently open DJ Lounges. *(PRD Sec 4.1, 14.1)*
- **REQ-012 (Out-of-Scope Enforcement):** Do not implement raw DSP audio waveform crossfading, cloud CDN DRM streaming, multi-tenant production cohorts, real Spotify integration, new audio/waveform synthesis, native mobile apps, or from-scratch LLM training. Simulate transitions at the metadata/scheduling level and generate text banter only. *(PRD Sec 4.2, 5.3, 14.2)*
- **REQ-013 (Delivery Track Declaration):** Choose either **Track A — Full-Stack (Default)** or **Track B — Backend-Only** at the start of Week 1 and explicitly state the choice in `README.md`. *(PRD Sec 4.3)*
- **REQ-014 (Single Technology Stack Constraint):** Choose exactly one full stack row from Section 8 (Option A Java, Option B MERN, or Option C Python) and build the entire system on it without mixing backend frameworks across layers. *(PRD Sec 8, 9, 14.2)*
- **REQ-015 (Host / Head DJ Persona Journey):** Support the complete Host journey: log in, select seed track and vibe profile (e.g., `"High-Energy EDM"`, `"Lo-Fi Chill"`), create DJ Lounge, configure queue rules (`bpmTolerancePct`, `aiVerbosity`, `soundclashEnabled`, `maxParticipants`), share invite link/code, start playback, approve/reject AI-suggested segues, manually promote tracks, resolve tied Soundclash votes, moderate room, and end session with persisted summary and analytics snapshot. *(PRD Sec 5.1)*
- **REQ-016 (Listener / Party Guest Persona Journey):** Support the complete Listener journey: open invite link/code, authenticate, join lounge and sync to current queue and `Now Playing` position, send chat messages and emoji reactions (feeding live sentiment), submit tracks to Soundclash queue, vote on queued tracks and active battles, ask the AI DJ natural-language lore questions with grounded answers, and leave/rejoin with automatic resynchronization. *(PRD Sec 5.2)*
- **REQ-017 (AI DJ Service Actor Behavior):** Implement the AI DJ as a first-class service actor that ingests acoustic vectors and metadata timelines, aggregates rolling chat sentiment, selects harmonic/BPM-valid segues, generates RAG-grounded transition banter, and tallies/announces Soundclash battle outcomes. *(PRD Sec 5.3)*
- **REQ-018 (Scope Decision Documentation):** Document any deliberate scope decision or deviation from the specification along with sound engineering reasoning. *(PRD Sec 4.2, 13.2, 15.1)*

### 3.3 Functional Requirements: Epic 1 — Real-Time Collaborative Queue & Lounge War Room (PRD Section 6.1)

- **REQ-019 (Lounge Creation, Invite Code & Capacity Enforcement):** Provide room creation, unique invite-link/code generation (e.g., `SP-9X2L`), and join flow enforcing configurable room capacity limits (`maxParticipants`, up to 50 design target). *(PRD Sec 6.1, 7.3.1)*
- **REQ-020 (Three-Zone Authoritative Queue State Machine):** Maintain an authoritative server-side queue state machine per lounge with three distinct zones: `Now Playing`, `Next Up` (ordered upcoming queue), and `Soundclash Stage` (active crowd battle between 2–4 candidate tracks). *(PRD Sec 6.1, 6.2)*
- **REQ-021 (Atomic Vote-Driven Queue Re-Ordering):** Allow listeners to upvote queued tracks in `Next Up`; re-sort the queue server-side and broadcast the updated order to all connected clients atomically so no client ever observes a partially applied reorder. *(PRD Sec 6.1)*
- **REQ-022 (Soundclash Queue Battles):** Allow listeners to submit candidate tracks challenging the current `Next Up` slot; allow Host or AI DJ to open a timed battle (`2–4` candidates), collect real-time listener votes, and splice the winning track into the queue at the correct position upon resolution. *(PRD Sec 6.1, 6.2)*
- **REQ-023 (Real-Time Chat, Typing Indicators, Reactions & Persistence):** Provide real-time room chat with typing indicators, emoji reactions, and session message history persisted to the database. *(PRD Sec 6.1)*
- **REQ-024 (Presence, Connection Quality & Reconnect Resync):** Track live participant presence (`joined`/`left`, `participantCount`), display a connection quality indicator (driven by `lounge.sync.ping`/`pong`), and resynchronize clients on network drop/reconnect. *(PRD Sec 6.1, 7.3.4)*
- **REQ-025 (Host Moderation Controls):** Implement server-enforced Host moderation actions: mute participant, remove/kick participant, lock room, and transfer Host or Co-DJ role. *(PRD Sec 6.1)*
- **REQ-026 (Epic 1 AC — Sub-100ms Queue Reorder Sync):** Every vote-driven reorder or Soundclash resolution must reflect identically across all connected clients within `< 100 ms` under normal local-network test conditions. *(PRD Sec 6.1)*
- **REQ-027 (Epic 1 AC — Concurrent Vote Determinism):** Two simultaneous votes on different tracks must never produce a queue state where the server and any client disagree on the final order; must be verified via an automated concurrent-vote test. *(PRD Sec 6.1)*
- **REQ-028 (Epic 1 AC — Fast Mid-Session Join Sync):** A new participant joining mid-session must receive the complete current lounge state (`Now Playing`, `Next Up`, active `Soundclash Stage` if any, and current sentiment score) within `< 2 seconds` of connecting. *(PRD Sec 6.1)*
- **REQ-029 (Epic 1 AC — Deterministic Host Disconnect Handling):** If the Host disconnects, the room must enter a clearly indicated `"host disconnected"` state and deterministically either promote a Co-DJ/new Host or freeze queue mutation until Host reconnection; behavior must be tested. *(PRD Sec 6.1)*

### 3.4 Functional Requirements: Epic 2 — Live AI DJ & Acoustic Sentiment Engine (PRD Section 6.2)

- **REQ-030 (Track Acoustic Feature Vector Model):** Represent every track with an acoustic feature vector containing `bpm` (positive number), `camelotKey` (24-key Camelot wheel code: `1A`–`12A`, `1B`–`12B`), `energy` (`0.0–1.0`), and `valence` (`0.0–1.0`). *(PRD Sec 6.2, 7.2)*
- **REQ-031 (Hard Constraint — BPM Compatibility Rule):** A candidate track is BPM-compatible with the current track if and only if its BPM falls within `±8%` (or lounge-configured `bpmTolerancePct`) of the current track's BPM (e.g., `120 BPM` accepts `110.4–129.6 BPM`), **OR** is a clean half-time (`×0.5`) or double-time (`×2.0`) multiple of the current BPM within the same `±8%` tolerance window. *(PRD Sec 6.2)*
- **REQ-032 (Hard Constraint — Camelot Wheel Key Compatibility Rule):** A candidate track is harmonically compatible with the current track if and only if its Camelot key is:
  - **(a) Identical key:** e.g., `8A → 8A`
  - **(b) Adjacent number on the same letter (with circular wrap-around 1 ↔ 12):** e.g., `8A → 7A` or `9A`; `1A → 12A` or `2A`
  - **(c) Same number with opposite letter (relative major/minor):** e.g., `8A → 8B`
  - Any other key relationship is a harmonic clash and strictly disqualifies the candidate as an immediate segue. *(PRD Sec 6.2)*
- **REQ-033 (Weighted Multi-Candidate Ranking Formula):** When multiple candidate tracks satisfy both `REQ-031` and `REQ-032`, rank valid candidates using the exact weighted score:
  $$\text{Score} = 0.50 \times \text{HarmonicCloseness} + 0.30 \times \text{EnergyCurveFit} + 0.20 \times \text{RecencyScore}$$
  where `HarmonicCloseness` prioritizes exact/compatible key & minimal BPM delta, `EnergyCurveFit` aligns candidate energy/valence with the current room sentiment trend, and `RecencyScore` deprioritizes tracks played within the last $N$ tracks. *(PRD Sec 6.2)*
- **REQ-034 (Acoustic Profile & Metadata Timeline Ingestion Pipeline):** Support ingesting per-track acoustic vectors (`bpm`, `camelotKey`, `energy`, `valence`) alongside a timestamped metadata timeline (`ts`, `type` $\in \{\text{sample\_credit}, \text{trivia}, \text{glossary}\}$, `text`/`payload`) for RAG grounding. *(PRD Sec 6.2, 7.2)*
- **REQ-035 (Live Rolling-Window Chat Sentiment Aggregator):** Continuously bucket incoming chat messages into `positive`, `neutral`, or `negative` energy, compute a rolling-window room sentiment score (`0.0–1.0` or `-1.0–1.0` normalized to `0.0–1.0`), broadcast `lounge.sentiment.score`, and factor the score into queue curation (favoring higher-energy candidates when positive/energetic, or pulling back tempo/energy when chat signals fatigue). *(PRD Sec 5.3, 6.2, 7.3.4)*
- **REQ-036 (RAG-Grounded Transition Script Generation):** As the current track reaches its outro window, select the highest-ranked valid segue and generate a short transition script ("banter") grounded in the track's ingested metadata timeline, citing the `sourceEvents` used. *(PRD Sec 6.2, 7.3.3)*
- **REQ-037 (Conversational RAG Q&A with Citations & Non-Hallucination):** Allow listeners to ask natural-language questions about the currently playing or recently played track; responses must cite the exact metadata entry IDs (`sourceEvents`) used and gracefully decline or hedge without fabricating facts when no relevant metadata exists. *(PRD Sec 6.2, 15.1)*
- **REQ-038 (AI DJ Soundclash Resolution & Banter Announcement):** Tally votes for active Soundclash battles (`2–4` candidates), resolve the winner deterministically (with Host tie-break or deterministic tie-break rule), splice the winning track into the queue, and broadcast an `lounge.ai.dj_announcement` banter line. *(PRD Sec 5.1, 6.2, 7.3.4)*
- **REQ-039 (Epic 2 AC — 20-Decision Segue Compliance Test):** Provide an automated test suite asserting `100%` BPM and Camelot-key rule compliance across at least `20 simulated segue decisions`. *(PRD Sec 6.2)*
- **REQ-040 (Epic 2 AC — Grounded RAG Citation Verification):** Verify that every RAG response (transition script and Q&A answer) includes a machine-readable citation list (`sourceEvents`) referencing ingested metadata entries and never hallucinates facts absent from the track profile. *(PRD Sec 6.2, 7.3.3)*
- **REQ-041 (Epic 2 AC — Single-Window Sentiment Shift Reaction):** Verify that a deliberate burst of positive or negative chat messages visibly shifts the room sentiment score within `1 aggregation window`. *(PRD Sec 6.2)*
- **REQ-042 (Epic 2 AC — Simultaneous Soundclash Queue Splicing):** Verify that Soundclash resolution applies identically and simultaneously to all connected clients and splices the winning track into the exact target queue slot. *(PRD Sec 6.2)*

### 3.5 Functional Requirements: Epic 3 — User Dashboard & Co-Listening Analytics (PRD Section 6.3)

- **REQ-043 (Personal Dashboard & Acoustic Taste Profile):** Provide a user dashboard (and REST endpoints) displaying past DJ Lounges attended/hosted, an aggregated acoustic taste profile (preferred BPM range, top Camelot keys, average energy/valence from listening history), and quick-rejoin links for active lounges. *(PRD Sec 6.3)*
- **REQ-044 (Hybrid Recommendation Engine):** Implement a hybrid recommendation model combining **content-based filtering** (acoustic vector similarity: BPM, Camelot key, energy, valence) and **collaborative filtering** (co-listening interaction patterns from `interactions`), producing a personalized ranked `"Recommended for You"` track rail. *(PRD Sec 3, 6.3)*
- **REQ-045 (Session Analytics & Sentiment Timeline):** For each ended DJ Lounge, compute and display: session duration, peak concurrent participants, total Soundclash battles held, total AI DJ questions asked, and a time-series sentiment timeline graph. *(PRD Sec 6.3)*
- **REQ-046 (Authenticated Admin Acoustic-Feature Upload Interface/API):** Provide an authenticated Admin interface (and REST API endpoint) to create/upload and validate a track's acoustic feature profile and metadata timeline without editing raw database or JSON files. *(PRD Sec 2, 6.3)*
- **REQ-047 (Epic 3 AC — Incremental Recommendation Updates):** Recording new user interactions must update the `"Recommended for You"` rail without requiring a full code redeploy (online scoring or an on-demand/background batch refresh job is acceptable). *(PRD Sec 6.3)*
- **REQ-048 (Epic 3 AC — Analytics Reconciliation with Event Log):** Session analytics metrics and the sentiment timeline graph must reconcile `100%` with the underlying `queue_events`/`chat_messages` log for at least one fully verified test session. *(PRD Sec 6.3)*
- **REQ-049 (Epic 3 AC — Strict Acoustic Profile Validation):** The acoustic-feature upload endpoint/form must validate: `bpm > 0`, `camelotKey` matches one of the 24 valid codes (`^(1[0-2]|[1-9])[AB]$`), and `energy` and `valence` are within `[0.0, 1.0]`, rejecting invalid payloads with clear `400 Bad Request` error messages. *(PRD Sec 6.3)*

### 3.6 Architecture, Data Modeling & In-Memory State Requirements (PRD Sections 7.1, 7.2 & 9.4)

- **REQ-050 (Five-Layer Modular Architecture):** Structure the application into five cooperating logical layers: (1) Client Layer, (2) Application REST API Layer, (3) Real-Time WebSocket Queue & State Gateway, (4) AI DJ Engine Service/Module, and (5) Persistence Layer + Audio Asset Storage. Keep the Queue Gateway and AI DJ Engine logically separated even within a single process. *(PRD Sec 7.1, 9.4)*
- **REQ-051 (Six Core Domain Entities):** Model the six core entities required by Section 7.2: `User`, `Track` (with acoustic features and RAG metadata timeline), `DJ Lounge`, `QueueEvent` / `QueueEntry` + `Soundclash`, `ChatMessage`, and `Interaction`. *(PRD Sec 7.2)*
- **REQ-052 (Document Database Schema — MongoDB Option):** When using MongoDB, implement the collections `users`, `tracks_with_acoustic_features` (with embedded `acoustic` object and `timeline` array), `dj_lounges`, `queue_events` (append-only log), and `interactions` (including `coListenedWith: [userId]`). *(PRD Sec 7.2.1, 9.2)*
- **REQ-053 (Relational Database Schema — PostgreSQL Option):** When using PostgreSQL, implement the 9 tables specified in Section 7.2.2: `users`, `tracks` (indexed on acoustic columns), `track_timeline_events`, `dj_lounges`, `queue_entries`, `soundclash_battles`, `soundclash_candidates`, `chat_messages`, and `interactions`. *(PRD Sec 7.2.2)*
- **REQ-054 (Sparse Integer Queue Positioning):** Store queue entry `position` using sparse integers (e.g., increments of `1000`: `1000, 2000, 3000...`) so single-track reorders only update the moved entry in the common case rather than renumbering the entire queue. *(PRD Sec 7.2.2)*
- **REQ-055 (In-Memory Authoritative Live Room State):** Maintain authoritative live queue order, active Soundclash battle state, and rolling sentiment window in memory per room so high-frequency votes and sentiment ticks never block on synchronous database reads/writes; persist full session summaries when a Lounge ends. *(PRD Sec 3, 9.4)*
- **REQ-056 (Batched High-Frequency Event Persistence):** Batch high-frequency persistence writes (`chat_messages`, `queue_events`, sentiment ticks) on a short interval (e.g., every `1–2 seconds` or every `N` events, whichever comes first) using connection pooling. *(PRD Sec 9.1, 9.2, 9.3, 9.4)*

### 3.7 Mandatory REST API & WebSocket Contracts (PRD Section 7.3)

- **REQ-057 (Mandatory Endpoint — `POST /api/v1/lounges`):** Implement `POST /api/v1/lounges` accepting `{ seedTrackId, vibeProfile, maxParticipants, bpmTolerancePct, soundclashEnabled, aiVerbosity }` and returning HTTP `201` with `{ loungeId, inviteCode, status, hostUserId, createdAt }`. *(PRD Sec 7.3.1)*
- **REQ-058 (Mandatory Endpoint — `GET /api/v1/tracks/{trackId}/acoustic-profile`):** Implement `GET /api/v1/tracks/{trackId}/acoustic-profile` returning HTTP `200` with `{ trackId, title, artist, acoustic: { bpm, camelotKey, energy, valence }, timeline: [ { ts, type, text } ] }`. *(PRD Sec 7.3.2)*
- **REQ-059 (Mandatory Endpoint — `POST /api/v1/lounges/{id}/ai/segue`):** Implement `POST /api/v1/lounges/{id}/ai/segue` accepting `{ currentTrackId, currentTs, candidateTrackIds, currentSentimentScore }` and returning HTTP `200` with `{ selectedTrackId, matchReason: { bpmDeltaPct, camelotRelation, energyFit }, transitionScript, sourceEvents, generatedAt }`. *(PRD Sec 7.3.3)*
- **REQ-060 (Namespaced WebSocket Envelope Contract):** Route all real-time Lounge traffic over a namespaced WebSocket connection per Lounge using the exact envelope: `{ "event": "<event.name>", "loungeId": "<id>", "payload": { ... }, "ts": 1690000000123 }`. *(PRD Sec 7.3.4)*
- **REQ-061 (WS Event — `lounge.queue.update`):** Broadcast `{ queue: [ { trackId, position } ], reason: 'vote' | 'segue' | 'battle' }` on every queue mutation. *(PRD Sec 7.3.4)*
- **REQ-062 (WS Event — `lounge.presence.update`):** Broadcast `{ participantId, action: 'joined' | 'left', participantCount }` on room join/leave transitions. *(PRD Sec 7.3.4)*
- **REQ-063 (WS Event — `lounge.chat.message`):** Broadcast `{ messageId, userId, body, sentimentBucket, tsSeconds }` when a chat message is processed. *(PRD Sec 7.3.4)*
- **REQ-064 (WS Event — `lounge.sentiment.score`):** Broadcast `{ rollingScore, windowSeconds, sampleSize }` on sentiment aggregation ticks. *(PRD Sec 7.3.4)*
- **REQ-065 (WS Event — `lounge.soundclash.open`):** Broadcast `{ battleId, candidateTrackIds, closesAt }` when a Soundclash battle starts. *(PRD Sec 7.3.4)*
- **REQ-066 (WS Event — `lounge.soundclash.resolved`):** Broadcast `{ battleId, winningTrackId }` when a Soundclash battle finishes. *(PRD Sec 7.3.4)*
- **REQ-067 (WS Event — `lounge.ai.dj_announcement`):** Broadcast `{ text, relatedTrackId, sourceEvents }` for AI DJ transition scripts and battle announcements. *(PRD Sec 7.3.4)*
- **REQ-068 (WS Event — `lounge.sync.ping` & `lounge.sync.pong`):** Support `{ clientSentAt, serverReceivedAt }` ping/pong events for client-server clock and sync drift measurement. *(PRD Sec 7.3.4)*

### 3.8 Non-Functional Requirements: Security, Performance & Observability (PRD Section 10)

- **REQ-069 (Short-Lived JWT Auth & Refresh Token Flow):** Require valid JWT authentication on all protected endpoints; access tokens must expire in `≤ 1 hour` and be paired with a refresh-token endpoint/flow. *(PRD Sec 10.1)*
- **REQ-070 (Four-Tier RBAC Enforcement Server-Side):** Enforce four roles (`listener`, `co_dj`, `host`, `admin`) server-side on all HTTP routes: Admin-only for acoustic profile uploads, Host/Co-DJ-only for queue overrides and manual battle resolution, and Host-only for lounge creation/moderation. *(PRD Sec 3, 4.1, 10.1)*
- **REQ-071 (WebSocket Handshake JWT Auth & Action Re-Validation):** Authenticate WebSocket connections using the JWT at handshake time and re-validate user role permissions on room-sensitive WebSocket commands (e.g., force-resolving a Soundclash or muting a user). *(PRD Sec 3, 10.1)*
- **REQ-072 (Server-Side Input Validation & XSS Sanitization):** Validate and sanitize all user inputs (chat messages, AI questions, acoustic-feature uploads) server-side to prevent SQL/NoSQL injection and stored XSS in chat history. *(PRD Sec 10.1)*
- **REQ-073 (Environment Variable Secrets Policy):** Load all secrets (DB credentials, JWT signing secret, LLM API keys) exclusively from environment variables (`.env` excluded via `.gitignore`); never commit secrets to Git history. *(PRD Sec 10.1, 13.2, 14.2)*
- **REQ-074 (Non-AI REST API Latency Target):** Achieve `P95 < 200 ms` latency across all non-AI REST endpoints. *(PRD Sec 10.2)*
- **REQ-075 (WebSocket Fan-Out Latency Target):** Achieve `< 500 ms` WebSocket message fan-out from server receipt to last client delivery for rooms up to design capacity (50 participants). *(PRD Sec 10.2)*
- **REQ-076 (Multi-Room Concurrent Architecture):** Never hard-code a single-room assumption; key all in-memory state managers, WebSocket rooms, and background tasks by `loungeId` to support multiple concurrent lounges. *(PRD Sec 4.1, 10.2)*
- **REQ-077 (Structured Logging with Session Correlation ID):** Emit structured JSON logs for every queue-mutating event and every AI DJ request/response (excluding sensitive fields) tagged with a correlation ID per Lounge session (`loungeId` / `correlationId`). *(PRD Sec 10.3)*
- **REQ-078 (Graceful Reconnect Without Duplicate Joins):** When a client drops and reconnects within a short grace window, restore its session with current queue/sentiment state and chat history without emitting duplicate `joined` presence events. *(PRD Sec 10.3)*
- **REQ-079 (API & Gateway Health-Check Endpoints):** Expose health-check endpoints (e.g., `GET /api/v1/health` and `GET /api/v1/health/ws`) reporting database, AI engine, and WebSocket gateway readiness. *(PRD Sec 10.3)*

### 3.9 Testing, Deliverables, Evidence & Evaluation Criteria (PRD Sections 11, 12, 13, 14 & 15)

- **REQ-080 (Seed Dataset — Minimum 3 Profiled Tracks in Valid Segue Chain):** Provide a deterministic seed script populating at least `3 tracks` (recommended `8–12 tracks` for rich Soundclash and recommendation testing) with realistic acoustic profiles and metadata timelines forming at least one valid harmonic/BPM segue chain. *(PRD Sec 4.1, 12.1, 14.1)*
- **REQ-081 (Automated Unit & Integration Test Suite):** Implement automated tests covering: (a) queue state machine & concurrent votes, (b) acoustic matching rules across `≥ 20` segue decisions (`100%` compliance), (c) sentiment scoring accuracy (`≥ 85%` on labeled test set), and (d) server-side 4-role RBAC enforcement. *(PRD Sec 4.1, 11.1, 12.1, 13)*
- **REQ-082 (Scripted Multi-Client Load & Sync Drift Benchmark):** Build a scripted multi-client WebSocket load/simulation script (e.g., 5–10+ simulated concurrent clients) that submits concurrent votes and chat bursts, and measures/records actual queue sync drift (`< 100 ms`), API latency (`< 200 ms`), and AI DJ latency (`< 3 s`). *(PRD Sec 11.1, 12.2, 13, 14.1)*
- **REQ-083 (Deliverable — Incremental Git Commit History):** Maintain a clean Git repository with incremental commits per module/feature rather than a single monolithic commit. *(PRD Sec 8.1, 11.2, 12.1, 13.1)*
- **REQ-084 (Deliverable — Setup & Run README):** Provide a comprehensive `README.md` with step-by-step setup, environment variable configuration, seed instructions, test execution commands, and explicit declaration of chosen Stack and Track. *(PRD Sec 4.3, 12.1, 13.1)*
- **REQ-085 (Deliverable — Architecture Overview Document):** Provide `docs/ARCHITECTURE.md` reflecting the built 5-layer architecture, data flow, and in-memory state strategy. *(PRD Sec 12.1, 13.1)*
- **REQ-086 (Deliverable — Full API & WebSocket Reference + HTTP Collection):** Provide `docs/API_DESIGN.md` and an exportable Postman/HTTP client collection covering all REST endpoints and WebSocket events with sample payloads. *(PRD Sec 8.1, 12.1, 13.1)*
- **REQ-087 (Deliverable — Database Schema & ER Diagram Document):** Provide `docs/DATABASE_DESIGN.md` with complete entity definitions, indexes, sparse positioning strategy, and Mermaid ER diagram. *(PRD Sec 12.1, 13.1)*
- **REQ-088 (Deliverable — Project Retrospective Document):** Provide a written retrospective covering what was built, any descoped items and why, and future improvements. *(PRD Sec 12.1, 13.1)*
- **REQ-089 (Evidence — Multi-Session Live Queue Reorder Proof):** Produce recorded or scripted output demonstrating 2+ sessions connected to the same Lounge with the queue reordering live in response to votes within `< 100 ms`. *(PRD Sec 12.2)*
- **REQ-090 (Evidence — Full AI DJ Segue & Grounded Q&A Proof):** Produce recorded or scripted output demonstrating at least one full AI DJ segue with acoustic match reasoning, generated transition script, and grounded listener Q&A with `sourceEvents` citations. *(PRD Sec 12.2)*
- **REQ-091 (Evidence — End-to-End Soundclash Battle Proof):** Produce recorded or scripted output showing a full Soundclash battle opened, voted on by participants, resolved, and spliced into the queue. *(PRD Sec 12.2)*
- **REQ-092 (Evidence — Sentiment Engine Tone-Shift Proof):** Produce before/after sentiment score evidence responding within 1 aggregation window to a scripted burst of positive/negative chat messages. *(PRD Sec 12.2)*
- **REQ-093 (Evidence — Automated Test Report & Performance Benchmark Report):** Produce test execution output (`100%` passing) and recorded P95 latency / sync drift benchmark numbers compared against Section 10.2 targets. *(PRD Sec 12.2, 13.1)*
- **REQ-094 (Evidence — Server-Side 4-Role RBAC Rejection Proof):** Produce concrete test/log proof showing HTTP `403 Forbidden` / WebSocket error rejection when a `listener` token attempts `co_dj`, `host`, or `admin` actions. *(PRD Sec 12.2)*
- **REQ-095 (Evaluation Rubric Alignment):** Satisfy all evaluation criteria weighted across: Epic 1 Real-Time Queue & Lounge War Room (`25%`), Epic 2 AI DJ & Acoustic/Sentiment Engine (`30%`), Epic 3 Dashboard, Recommendations & Analytics (`10%`), Architecture, Data Modeling & API Design (`15%`), Security, Testing & Performance Evidence (`15%`), and Documentation & Communication Quality (`5%`). *(PRD Sec 15.1, 15.2)*

---

## 4. User Roles & Permissions Matrix (RBAC — `REQ-070`, `REQ-071`)

| Permission / Action | Listener (`listener`) | Co-DJ (`co_dj`) | Host (`host`) | Admin (`admin`) | Enforcement Entry Point |
| :--- | :---: | :---: | :---: | :---: | :--- |
| Register, Login, Refresh JWT, View Dashboard & Recommendations | ✅ | ✅ | ✅ | ✅ | REST API |
| Join Active Lounge (via Invite Code), Sync State, Leave/Rejoin | ✅ | ✅ | ✅ | ✅ | REST API + WebSocket |
| Send Chat Messages, Emoji Reactions, Typing Indicators | ✅ | ✅ | ✅ | ✅ | WebSocket |
| Upvote Tracks in `Next Up` Queue | ✅ | ✅ | ✅ | ✅ | WebSocket / REST API |
| Submit Track to `Soundclash Stage` & Vote in Active Battle | ✅ | ✅ | ✅ | ✅ | WebSocket / REST API |
| Ask AI DJ Natural-Language Track Lore Questions (RAG Q&A) | ✅ | ✅ | ✅ | ✅ | REST API / WebSocket |
| Approve/Override AI Segue, Manually Promote Track in Queue | ❌ (`403`) | ✅ | ✅ | ✅ | REST API + WebSocket |
| Open Timed Soundclash Battle or Force-Resolve / Tie-Break Battle | ❌ (`403`) | ✅ | ✅ | ✅ | REST API + WebSocket |
| Create DJ Lounge, Configure Lounge Rules, End Lounge Session | ❌ (`403`) | ❌ (`403`) | ✅ | ✅ | REST API |
| Moderation: Mute Participant, Kick Participant, Lock Room, Transfer Role | ❌ (`403`) | ❌ (`403`) | ✅ | ✅ | REST API + WebSocket |
| Upload / Validate / Edit Track Acoustic Profile & RAG Metadata Timeline | ❌ (`403`) | ❌ (`403`) | ❌ (`403`) | ✅ | REST API (`POST /api/v1/admin/tracks`) |
