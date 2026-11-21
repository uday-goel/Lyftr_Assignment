# Relay — Multi-Turn AI Chat with Key Failover & Caching

A production-grade, full-stack AI chat application built with **React**, **FastAPI**, **LiteLLM Gateway**, and **SQLite**. Built to demonstrate multi-turn state persistence, automatic key failover across rate limits/errors, built-in response caching, and exact per-request token/latency metrics.

---

## 1. Live URLs
- **Frontend (UI)**: `https://<your-frontend>.vercel.app` *(or update with your deployed frontend URL)*
- **Backend (API)**: `https://<your-backend>.onrender.com` *(or update with your deployed backend URL)*
  - Health check: `https://<your-backend>.onrender.com/health`
  - Chat endpoint: `POST https://<your-backend>.onrender.com/chat`
  - History endpoint: `GET https://<your-backend>.onrender.com/sessions/{session_id}/messages`

---

## 2. Run locally: with Docker and without

### A. With Docker (Recommended)
The entire stack starts with a single command using Docker Compose:

```bash
# 1. Create your environment configuration file
cp .env.example .env

# 2. Add at least two valid API keys in .env (e.g., Gemini or OpenAI keys)
# LLM_API_KEYS=key_alpha,key_beta
# LLM_MODEL=gemini/gemini-2.0-flash

# 3. Launch the full stack (builds multi-stage images and mounts SQLite volume)
make up

# 4. View logs or stop the stack
make logs
make down
```

- **Frontend**: [http://localhost:3000](http://localhost:3000)
- **Backend**: [http://localhost:8000](http://localhost:8000)
- **Health check**: [http://localhost:8000/health](http://localhost:8000/health)
- SQLite database is persisted under Docker volume `app-data` mounted at `/data/app.db`.

### B. Without Docker (Local Development)

#### Backend Setup
```bash
cd backend
python -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
# On Windows (PowerShell):
# .\.venv\Scripts\Activate.ps1

pip install -r requirements-dev.txt

# Configure environment variables
export LLM_API_KEYS="your_key_1,your_key_2"
export LLM_MODEL="gemini/gemini-2.0-flash"
export DATABASE_URL="sqlite:///./app.db"
export ALLOWED_ORIGINS="http://localhost:3000,http://localhost:5173"
export ENABLE_FAILURE_SIMULATION="true"

# Start FastAPI server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### Frontend Setup
```bash
cd frontend
npm install   # or: npx pnpm install
VITE_API_URL=http://localhost:8000 npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

#### Running Backend Tests
```bash
# Run 14/14 automated pytest tests (all provider network calls mocked):
cd backend
python -m pytest -v
```

---

## 3. Design decisions: key fallback, caching, SQLite session storage

### A. Key Fallback & Resilience
- **Sequential Pool Traversal**: `llm.complete()` iterates through keys in order (`key_1`, `key_2`, ...). Each key is invoked via `litellm.acompletion()` with `num_retries=0`. Disabling LiteLLM's internal retry backoff ensures that upon encountering an HTTP 429 (rate-limit), quota exhaustion, or 5xx server error, failover to the subsequent key occurs immediately with minimal latency impact.
- **Failover Accounting**: The system tracks `fallback_attempts` (how many keys failed before the successful response) and reports the successful `key_id` (`key_1`, `key_2`, etc.). Real API keys are never exposed in logs or API responses.
- **Graceful Error Termination**: If all configured keys fail or quota is exhausted across all providers, the system cleanly raises an HTTP 503 with body `{"detail": "all providers unavailable"}` without generating unhandled exceptions, stack traces, or recording broken partial messages to SQLite.
- **Test Hook Simulation**: When `ENABLE_FAILURE_SIMULATION=true`, the `X-Simulate-Failure` header (e.g., `key_1=429`, `key_1=500`, or `*=429`) intercepts calls to targeted keys and simulates provider failures on demand for that specific request only, enabling failover demonstration without burning production quotas.

### B. Response Caching
- **LiteLLM Native Cache**: Response caching uses LiteLLM's cache (`local` in-memory by default, or `disk` persisted to `/data/cache`).
- **Cache Key Composition**: Formed strictly by `(model, messages, parameters)`. The messages list encapsulates the system prompt + all conversation turns + the current user message.
- **Deterministic Cache Hits**: The identical first prompt in two different sessions generates a cache hit (`LLM_CACHE`) on the second request. Any alteration in context or message content results in a cache miss (`LIVE_INFERENCE`).
- **Cache Hit Semantics**: On a cache hit, `resolution_source` is set to `LLM_CACHE`, `key_id` is returned as `null`, `fallback_attempts` is `0`, token counts reflect the cached generation usage, and measured latency is significantly faster (1–5 ms) than live provider calls.
- **Bypass on Simulated Failure**: When `X-Simulate-Failure` is supplied, cache *reading* is bypassed (`cache: {"no-cache": True}`) so live key failover can be tested and verified repeatedly even for queries that have already been cached.

### C. SQLite Session Storage
- **Isolated Storage**: Sessions are maintained purely within SQLite (`/data/app.db`) with zero external database dependencies.
- **Atomic Persistence**: Exchanges are saved via `save_exchange()` only *after* the LLM response successfully completes. If a request fails validation (422) or providers are exhausted (503), nothing is recorded in history.
- **Schema & Indexes**:
  ```sql
  CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      metadata TEXT,
      created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

  CREATE TABLE IF NOT EXISTS sessions (
      session_id TEXT PRIMARY KEY,
      system_prompt TEXT,
      created_at TEXT NOT NULL
  );
  ```
- **Session-Level System Prompts**: Custom system prompts are persisted per session in the `sessions` table. History sent to the model prepends this system prompt followed by stored user/assistant dialogue turns (up to `MAX_HISTORY_MESSAGES`), while `GET /sessions/{session_id}/messages` returns purely dialogue messages without exposing the system prompt.

### D. Per-Request Token Usage & Latency Tracking
- **Request-Specific Usage**: Tokens (`input_tokens`, `output_tokens`) are extracted directly from the provider completion usage for that single transaction. Input tokens represent prompt tokens (system prompt + history + current message); output tokens represent response completion tokens. They are never cumulative running totals.
- **Accurate Server Latency**: `latency_ms` is measured server-side using high-resolution monotonic clocks (`time.perf_counter()`), measuring total time spent in route processing and gateway execution.

---

## 4. Verification: how to see failover, cache hits and per-message token usage

### Verification via Web UI
1. **Live Inference**: Open the app. Send `"What is the capital of France?"`. Notice badge `LIVE_INFERENCE`, model, latency (e.g., ~800 ms), input/output tokens, and key (e.g. `key_1`).
2. **Cache Hit**: Click **"+ New chat"** to start a new session. Send the exact same question: `"What is the capital of France?"`. Notice badge instantly turns to green `LLM_CACHE`, latency drops to ~1–5 ms, `key` is `—` (`null`), and tokens match the cached usage.
3. **Simulated Failover**: Open **"⚙ Settings & Hooks"** in the top bar. Select `Simulate key_1=429 (Rate limit primary → key_2)`. Send `"Hello there"`. Notice reply arrives successfully with badge `LIVE_INFERENCE`, `key: key_2`, and tag `1 failover`!
4. **Provider Exhaustion (503)**: In the failover selector, choose `Simulate *=429 (All keys fail → 503 error)`. Send any message. The chat displays an inline error message: `"All AI providers are temporarily unavailable. Please try again in a moment."` No broken page, and nothing is added to session history.

### Verification via `curl` CLI

```bash
BASE="http://localhost:8000"

# 1. Health check
curl -s "$BASE/health" | jq .

# 2. Live inference (new session)
curl -s -X POST "$BASE/chat" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-s1", "message": "What is the capital of France?"}' | jq .

# 3. Cache hit (different session, identical prompt)
curl -s -X POST "$BASE/chat" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-s2", "message": "What is the capital of France?"}' | jq .

# 4. Failover simulation (forces key_1=429 -> succeeds on key_2)
curl -s -X POST "$BASE/chat" \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=429" \
  -d '{"session_id": "test-s3", "message": "Tell me a short joke"}' | jq .

# 5. All providers fail simulation (*=429 -> 503 error)
curl -i -X POST "$BASE/chat" \
  -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: *=429" \
  -d '{"session_id": "test-s4", "message": "This will fail"}'

# 6. Retrieve stored history (oldest first, no system prompt)
curl -s "$BASE/sessions/test-s1/messages" | jq .
```

---

## 5. Setup Used: your editor and AI tools
- **Editor**: Visual Studio Code / Antigravity IDE
- **AI Agent & Tools**: Google DeepMind Antigravity Agent, Claude / Gemini models for iterative development, architecture analysis, and full-stack integration testing.
- **Environment**: Python 3.12 / 3.14 (FastAPI, LiteLLM, Pydantic v2), Node.js v20 / v24 (React 18, Vite), Docker with Alpine Linux images.

---

## 6. Bonus features (if any): what you added beyond the requirements and why
1. **Interactive Failover Simulator in UI**: A dedicated test selector directly in the interface allowing users and evaluators to trigger `X-Simulate-Failure` scenarios (`key_1=429`, `key_1=500`, `*=429`) with one click without opening terminal or Postman.
2. **Real-time Live Backend Health Indicator**: An animated pulsing status indicator in the top navbar displaying connection status, number of active keys configured, and cache status in real-time.
3. **Session-Level System Prompt Customization**: Clean UI field enabling users to define custom personas (e.g. "Answer as a pirate in one sentence") persisted into SQLite per session.
4. **Adaptive Dark / Light Mode**: Automatically respects operating system preferences with clean typography and custom color palettes for live inference vs. cached responses.
5. **Interactive Quick-Start Prompts**: Suggestion chips on empty session state for rapid testing and demonstrations.
6. **Multi-Stage Lean Docker Containers**: Security-hardened non-root runtime users (`app:app`), automated healthchecks, and volume mounting for SQLite data durability across container lifecycles.
7. **Offline-Resilient Startup Guard**: Automatic fallback configuration preventing LiteLLM from blocking on remote cost map fetching when running in air-gapped or restricted network environments.
