# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
python3 app.py          # starts Flask on port 8001
```

First-time setup — set the login password (requires an interactive terminal):
```bash
python3 set_password.py
```

Sync to Raspberry Pi (production):
```bash
rsync -avz --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' --exclude '.env' --exclude 'uploads/' --exclude '.venv/' --exclude '.auth_hash' . mypi:~/personal_ai/
```

## Environment (`.env`)

```
OPENROUTER_API_KEY=...           # required
SECRET_KEY=...                   # required, ≥32 chars; generate with: openssl rand -base64 32
FLASK_ENV=development            # disables SESSION_COOKIE_SECURE for local http
GOOGLE_AI_STUDIO_API_KEY=...     # optional
SEARXNG_URL=http://...           # optional, defaults to evans-rasberry-pi.local:8888
TRUST_X_FORWARDED_FOR=true       # set when behind a reverse proxy
SESSION_COOKIE_SECURE=true       # force secure cookies (auto-true when not FLASK_ENV=development)
```

## Architecture

Single-user personal AI chat app. Flask backend + vanilla JS frontend (no bundler, native ES modules).

### Auth (`auth.py`, `set_password.py`)

- Password hash stored in `.auth_hash` (mode 600); `LOGIN_PASSWORD_HASH` env var overrides
- `before_request` in `app.py` gates every route — unauthenticated `/api/` calls get 401 JSON, everything else redirects to `/login`
- Login form includes a CSRF token (session-stored, `secrets.compare_digest` validated); rate limiter blocks 5 failed attempts per IP per 15 minutes
- `set_password.py` requires an interactive terminal; Claude Code's pseudo-tty can't run it — tell the user to run it in their own terminal

### Backend (`app.py`, `database.py`, `config.py`)

- **Flask** serves the SPA shell and all API routes
- **SQLite** (`personal_ai.db`, WAL mode) stores conversations and messages; FTS5 virtual table with triggers enables full-text search; `_migrate()` handles schema additions (e.g. adding `reasoning` column)
- **OpenRouter** is the primary AI provider — accessed via the `openai` Python SDK pointed at `https://openrouter.ai/api/v1`
- **Google AI Studio** is a secondary provider; model IDs prefixed `google-ai-studio/` route to a separate `google_client`; thinking chunks are detected via `extra_content.google.thought` in the raw delta
- **SSE streaming**: `POST /api/conversations/:id/messages` returns `text/event-stream`. The agentic loop (model → tool call → result → model) runs server-side and emits typed events: `content_delta`, `reasoning_delta`, `tool_call`, `tool_result`, `image_result`, `tools_warning`, `done`, `error`
- **Model capabilities** (`supports_vision`, `supports_tools`) are parsed from the OpenRouter `/models` response and cached in memory for 10 minutes; `supports_tools=False` omits the tools array and emits a `tools_warning` SSE event

### Tools (`tools/`)

- `web_search.py` — calls SearXNG; URL configurable via `SEARXNG_URL`
- `web_fetch.py` — HTTP GET with Chrome UA, Readability-style extraction via BeautifulSoup + html2text, 15-min cache, blocks private/internal hostnames including `.local`
- `python_sandbox.py` — subprocess with `cwd=tmpdir`; `realpath`-resolved path checks block reads/writes outside sandbox (handles macOS `/var` → `/private/var` symlink); `plt.show()` intercepted to return base64 PNG via `image_result` SSE event

### Frontend (`static/js/`, `templates/index.html`)

All JS is ES module imports — no bundler. Key contracts between Python and JS:

- `chat.js` expects message elements with classes `.msg.user`, `.msg.ai`, `.msg.err` — not `.message.*`
- `markLatest()` adds `.msg.latest` to the newest assistant message so action buttons stay visible
- Streaming cursor class is `.tt-cursor`; reasoning blocks are `<details class="reasoning">`; tool blocks are `<details class="tool">`
- `history.js` dispatches `conversationDeleted` and reads `conversationUpdated` custom events
- `models.js` dispatches `modelChanged` with `{ detail: { model } }` when selection changes

### Key design decisions

- **Incognito mode**: agentic loop runs normally but nothing is written to DB; `model_id` and `system_prompt` are passed as form fields instead of being read from DB
- **Auto-title**: after first message, user text is truncated to 60 chars — no LLM call
- **Retry**: removes assistant message el from DOM, re-calls `executeSend()` with stored user text; DB accumulates both exchanges (acceptable for single-user)
- **iOS keyboard**: `visualViewport` resize event shrinks `#main` to `vv.height` so the input bar stays above the keyboard
- **Layout**: `#app` uses `display:flex` at ≥640px so sidebar and main are natural flow siblings — no `margin-left` tricks that break at unusual DPR/window-width combinations
- **Tool `tool_choice` fallback**: if the provider rejects the specific `tool_choice` form, `app.py` retries with `"auto"`, then without `tool_choice` entirely
