# personal_ai

Single-user personal AI chat app built with Flask, SQLite, and vanilla JavaScript (ES modules).  
It supports streamed responses, model/tool calling, optional image uploads, and local conversation history.

## Features

- Password-protected login
- Server-sent event (SSE) streaming responses
- Conversation history stored in SQLite
- OpenRouter model support
- Optional Google AI Studio model support
- Tool support:
  - `web_search` (SearXNG)
  - `web_fetch`
  - `run_python` (sandboxed Python execution)

## Requirements

- Python 3.10+ (recommended)
- An OpenRouter API key

## Quick start

1. Clone and enter the repo.
2. Create and activate a virtual environment.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create your environment file from the template:

```bash
cp .env.template .env
```

5. Set at least:
   - `OPENROUTER_API_KEY`
   - `SECRET_KEY` (32+ chars, random)

6. Set the login password (interactive terminal required):

```bash
python3 set_password.py
```

7. Run the app:

```bash
python3 app.py
```

The app runs on port `8001` by default.

## Environment variables

See `.env.template` for the full list.

Core values:

- `OPENROUTER_API_KEY` (required)
- `SECRET_KEY` (required)
- `FLASK_ENV` (`development` or `production`)
- `SESSION_COOKIE_SECURE` (`true` when served over HTTPS/proxy)
- `TRUST_X_FORWARDED_FOR` (`true` behind trusted reverse proxy)
- `SEARXNG_URL` (optional, for `web_search`)
- `GOOGLE_AI_STUDIO_API_KEY` (optional)
- `ENABLE_UNSAFE_PYTHON_TOOL` (safety toggle)
- `ENABLE_BROWSER_FETCH` (safety toggle)

## Project structure

- `app.py` — Flask app, routes, streaming chat loop, model/tool orchestration
- `auth.py` — password hash, login checks, CSRF, rate limiting
- `database.py` — SQLite schema and conversation/message persistence
- `config.py` — environment-driven config
- `set_password.py` — interactive password setup
- `tools/` — tool implementations (`web_search`, `web_fetch`, `python_sandbox`)
- `static/`, `templates/` — frontend assets and HTML templates

## Notes

- `set_password.py` must be run in a real interactive terminal.
- For production, run behind HTTPS and set secure cookie/proxy settings accordingly.
