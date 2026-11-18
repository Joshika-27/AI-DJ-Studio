# Technology Stack Specification (Revised Module 0)

## 1. PRD Mandatory Stack Rules & Options Matrix (PRD Section 8 & Section 14.2)

Per **PRD Section 8** and **Section 14.2 (`REQ-014`)**, the project must choose **exactly one full row** from the three official stack options below and build the entire system on it without mixing backend frameworks across layers:

| Layer | Option A — Java | Option B — MERN | **Option C — Python (SELECTED)** |
| :--- | :--- | :--- | :--- |
| **Frontend** | React | React | **React** |
| **Backend Framework** | Spring Boot | Express.js (Node.js) | **FastAPI** |
| **Real-Time Layer** | Spring WebFlux (reactive WebSockets) | Socket.io | **Native WebSockets (FastAPI)** |
| **Database** | PostgreSQL or MongoDB | MongoDB (required) | **PostgreSQL** |
| **ORM / Data Access** | JPA/Hibernate or Spring Data MongoDB | Mongoose | **SQLAlchemy (`asyncpg`)** |
| **Auth** | Spring Security + JWT | Custom middleware + JWT (`jsonwebtoken`) | **OAuth2/JWT via FastAPI security utils** |
| **Testing** | JUnit / Mockito | Jest / Supertest | **Pytest** |
| **Language** | Java | JavaScript (not TypeScript) | **Python** |

---

## 2. Confirmed Track & Technology Stack Selection

- **Confirmed Delivery Track:** **Track A — Full-Stack** (`REQ-013`)
- **Confirmed Architecture:** **5-Layer Modular Monolith** (`REQ-050`)
- **Confirmed Stack Row:** **Option C — Python Full-Stack** (`REQ-014`)
  - **Frontend:** React (JavaScript + Vite)
  - **Backend Framework:** Python FastAPI
  - **Real-Time Layer:** Native FastAPI WebSockets (`ConnectionManager` keyed by `loungeId`)
  - **Database:** PostgreSQL
  - **ORM / Data Access:** SQLAlchemy 2.0 (async with `asyncpg`)
  - **Auth & Authorization:** JWT authentication (`≤ 1h` access token + refresh flow) and 4-role server-side RBAC (`listener`, `co_dj`, `host`, `admin`)
  - **Testing:** Pytest (`pytest`, `pytest-asyncio`, `httpx`)

---

## 3. Detailed Technology Breakdown (Purpose, Suitability & Requirement Status)

| Technology | Layer / Purpose | Why It Is Suitable | Requirement Status |
| :--- | :--- | :--- | :--- |
| **React (JavaScript + Vite)** | **Frontend Client** (DJ Console, War Room, Sentiment Chat, Dashboard, Admin Upload UI) | Component-based state management handles high-frequency WebSocket updates (`lounge.queue.update`, `lounge.sentiment.score`) cleanly with minimal re-renders. Introduced early in **Module 5** and integrated progressively across **Modules 6–14**. | **Mandatory** for Track A (PRD Sec 8). |
| **Python 3.11+ & FastAPI** | **Application REST API Layer** (`/api/v1/*`) & Pydantic payload validation | Native async (`asyncio`) performance achieves `< 200 ms` REST P95 latency (`REQ-074`); Pydantic enforces strict validation for acoustic profiles (`BPM > 0`, 24 Camelot keys, `0.0–1.0` energy/valence) (`REQ-049`). | **Mandatory Option C Selection** (PRD Sec 8, 9.3). |
| **Native FastAPI WebSockets (`ConnectionManager`)** | **Real-Time Queue & State Gateway** (`/ws/lounges/{loungeId}`) | Per PRD Sec 9.3, an in-memory `ConnectionManager` keyed by `loungeId` runs on the single-threaded async event loop, achieving `< 10 ms` local fan-out (`< 100 ms` sync drift target) and atomic lock-serialized queue reordering (`REQ-005`, `REQ-027`). | **Mandatory Option C Selection** (PRD Sec 8, 9.3). |
| **PostgreSQL** | **Primary Relational Datastore** | Directly implements all 9 relational tables in PRD Sec 7.2.2 (`users`, `tracks`, `track_timeline_events`, `dj_lounges`, `queue_entries`, `soundclash_battles`, `soundclash_candidates`, `chat_messages`, `interactions`) plus `queue_events` with indexed acoustic columns (`REQ-053`). | **Mandatory Option C Selection** (PRD Sec 7.2.2, 8, 9.3). |
| **SQLAlchemy 2.0 (`asyncpg`)** | **Async ORM & Connection Pool** | Provides non-blocking async database access throughout the REST and WebSocket paths (`PRD Sec 9.3`) and supports batched event persistence (`REQ-056`). | **Mandatory Option C Selection** (PRD Sec 8, 9.3). |
| **OAuth2 / JWT (`PyJWT` + `bcrypt`)** | **Authentication & 4-Role RBAC** | Stateless JWT verification (`≤ 1h` expiry + refresh token flow) works identically across FastAPI HTTP dependencies and WebSocket handshake validators (`REQ-069`, `REQ-070`, `REQ-071`). | **Mandatory Option C Selection** (PRD Sec 8, 10.1). |
| **Python Scientific/NLP Stack (`numpy` + lexicon/energy classifier)** | **Sentiment Engine & Hybrid Recommendation Engine** | Provides clean vector math for Content-Based acoustic cosine similarity + Co-Listening Collaborative Filtering (`Precision@5 ≥ 0.6`, `REQ-010`) and fast rolling-window chat sentiment scoring (`≥ 85%` accuracy, `REQ-008`). | **[RECOMMENDATION]** within Option C (PRD Sec 6.2, 6.3). |
| **Hosted/Local LLM API + Deterministic Grounded Fallback** | **RAG Transition Script & Conversational Q&A** | Generates grounded DJ transition banter and Q&A answers from `track_timeline_events` in `< 2s`, while a deterministic metadata-grounded fallback guarantees `100%` offline test reproducibility and zero hallucination (`sourceEvents` citations, `REQ-040`). | **[RECOMMENDATION]** (PRD Sec 8.1, 14.1 allow any hosted/local LLM). |
| **Pytest (`pytest`, `pytest-asyncio`, `httpx`)** | **Automated Unit, Integration & Load Testing** | First-class async testing for WebSocket concurrent votes, 25+ segue compliance assertions, sentiment evaluation, and 4-role RBAC rejection tests (`REQ-081`). | **Mandatory Option C Selection** (PRD Sec 8). |

---

## 4. Shared Supporting Tools (PRD Section 8.1)

| Tool | Purpose | Requirement Status |
| :--- | :--- | :--- |
| **Git & GitHub** | Version control with incremental module-by-module commit history across `Module 0` – `Module 15` (`REQ-083`). | **Mandatory** (PRD Sec 8.1, 12.1). |
| **Postman / HTTP Client Collection** | Exported JSON collection (`docs/postman_collection.json`) for interactive and automated REST API verification (`REQ-086`). | **Mandatory** (PRD Sec 8.1, 12.1). |
| **Docker & Docker Compose** | Containerized single-command startup (`docker compose up --build`) bringing up PostgreSQL, FastAPI Backend, and React Frontend (`REQ-011`). | **[RECOMMENDATION]** (Explicitly recommended in PRD Sec 8.1). |
| **Environment Variables (`.env`)** | Externalizes `DATABASE_URL`, `JWT_SECRET`, `JWT_REFRESH_SECRET`, and `LLM_API_KEY` (`REQ-073`). | **Mandatory** (PRD Sec 10.1, 14.2). |
