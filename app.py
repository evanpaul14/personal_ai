import json
import os
import time
import threading
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory, \
    render_template, redirect, url_for, session
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from openai import OpenAI
from PIL import Image, UnidentifiedImageError

from config import config
from auth import (
    load_hash, verify_password,
    is_rate_limited, record_failed, clear_failed,
    make_csrf, valid_csrf, get_client_ip, request_has_same_origin,
)
from database import (
    init_db, create_conversation, get_conversation, list_conversations,
    update_conversation, delete_conversation, add_message, get_messages,
    search_messages, touch_conversation, new_id
)
from tools.web_search import web_search, WEB_SEARCH_SCHEMA
from tools.web_fetch import web_fetch, WEB_FETCH_SCHEMA
from tools.python_sandbox import run_python, RUN_PYTHON_SCHEMA

app = Flask(__name__, static_folder="static", template_folder="templates")
if config.TRUST_X_FORWARDED_FOR:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)
app.secret_key = config.SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = config.SESSION_COOKIE_HTTPONLY
app.config["SESSION_COOKIE_SAMESITE"] = config.SESSION_COOKIE_SAMESITE
app.config["SESSION_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE
app.config["PERMANENT_SESSION_LIFETIME"] = config.PERMANENT_SESSION_LIFETIME
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES

os.makedirs(config.UPLOAD_DIR, exist_ok=True)
init_db()

Image.MAX_IMAGE_PIXELS = 25_000_000

# ── Auth gate ─────────────────────────────────────────────────────────────────

_PUBLIC_ENDPOINTS = {"login", "static"}
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _csrf_required() -> bool:
    if request.method not in _MUTATING_METHODS:
        return False
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return False
    return request.path.startswith("/api/") or request.endpoint == "logout"


def _parse_int_arg(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = request.args.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if value < min_value or value > max_value:
        raise ValueError(f"{name} must be between {min_value} and {max_value}")
    return value


def _clean_text(value, max_len: int, default: str | None = None) -> str | None:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return text[:max_len]


def _save_uploaded_image(image_file):
    if image_file.mimetype not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise ValueError("unsupported image type")

    image_file.stream.seek(0)
    try:
        with Image.open(image_file.stream) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("invalid image file") from None

    image_file.stream.seek(0)
    with Image.open(image_file.stream) as img:
        normalized = img.convert("RGB")
        normalized.thumbnail((2048, 2048))
        fname = f"{new_id()}.jpg"
        path = os.path.join(config.UPLOAD_DIR, fname)
        normalized.save(path, format="JPEG", quality=90, optimize=True)
    return path

@app.before_request
def require_auth():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if not session.get("authed"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "authentication required"}), 401
        return redirect(url_for("login"))

    if _csrf_required():
        token = request.headers.get("X-CSRF-Token", "")
        if not token and not request.path.startswith("/api/"):
            token = request.form.get("csrf_token", "")
        if not valid_csrf(token):
            if request.path.startswith("/api/"):
                return jsonify({"error": "invalid csrf token"}), 403
            return redirect(url_for("login"))


@app.after_request
def set_security_headers(response):
    csp = "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "worker-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ])
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_e):
    if request.path.startswith("/api/"):
        return jsonify({"error": f"upload too large (max {config.MAX_UPLOAD_BYTES} bytes)"}), 413
    return "Payload too large", 413

client = OpenAI(
    api_key=config.OPENROUTER_API_KEY,
    base_url=config.OPENROUTER_BASE_URL,
)

google_client = OpenAI(
    api_key=config.GOOGLE_AI_STUDIO_API_KEY or "placeholder",
    base_url=config.GOOGLE_AI_STUDIO_BASE_URL,
) if config.GOOGLE_AI_STUDIO_API_KEY else None

GOOGLE_AI_STUDIO_EXTRA_MODELS = [
    {
        "id": "google-ai-studio/gemma-4-26b-a4b-it",
        "name": "Gemma 4 26B A4B Instruct",
        "context_length": 32768,
        "supports_vision": False,
        "supports_tools": True,
        "input_modalities": ["text"],
        "pricing": {},
    },
    {
        "id": "google-ai-studio/gemma-4-31b-it",
        "name": "Gemma 4 31B Instruct",
        "context_length": 32768,
        "supports_vision": False,
        "supports_tools": True,
        "input_modalities": ["text"],
        "pricing": {},
    },
]

# --- Models cache ---
_models_cache = {"data": None, "ts": 0}
_models_lock = threading.Lock()

def get_models():
    with _models_lock:
        if _models_cache["data"] and (time.time() - _models_cache["ts"]) < config.MODELS_CACHE_TTL:
            return _models_cache["data"]
    import httpx
    resp = httpx.get(
        f"{config.OPENROUTER_BASE_URL}/models",
        headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
        timeout=10,
    )
    resp.raise_for_status()
    models = resp.json().get("data", [])
    parsed = []
    for m in models:
        modalities = m.get("architecture", {}).get("input_modalities") or \
                     m.get("architecture", {}).get("modality", "text").split("+")
        supported_params = m.get("supported_parameters") or []
        parsed.append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "context_length": m.get("context_length", 0),
            "supports_vision": "image" in modalities,
            "supports_tools": "tools" in supported_params,
            "input_modalities": modalities,
            "pricing": m.get("pricing", {}),
        })
    parsed.sort(key=lambda x: x["name"].lower())
    if config.GOOGLE_AI_STUDIO_API_KEY:
        parsed = GOOGLE_AI_STUDIO_EXTRA_MODELS + parsed
    with _models_lock:
        _models_cache["data"] = parsed
        _models_cache["ts"] = time.time()
    return parsed

# --- Routes ---

@app.route("/login", methods=["GET", "POST"])
def login():
    setup_mode = not load_hash()

    if request.method == "GET":
        if session.get("authed"):
            return redirect(url_for("index"))
        return render_template("login.html", setup=setup_mode, error=None, csrf=make_csrf())

    if setup_mode:
        return render_template(
            "login.html",
            setup=True,
            error="password not configured on server; run python3 set_password.py locally",
            csrf=make_csrf(),
        ), 503

    # POST
    ip = get_client_ip()
    form_csrf = request.form.get("csrf_token", "")
    if not valid_csrf(form_csrf) and not request_has_same_origin():
        return render_template("login.html", setup=setup_mode, error="invalid request", csrf=make_csrf()), 403

    password = request.form.get("password", "")

    # Normal login
    if is_rate_limited(ip):
        return render_template("login.html", setup=False, error="too many attempts — try again in 15 minutes", csrf=make_csrf()), 429

    if verify_password(password):
        clear_failed(ip)
        session.clear()
        session["authed"] = True
        make_csrf()
        session.permanent = True
        return redirect(url_for("index"))

    record_failed(ip)
    return render_template("login.html", setup=False, error="invalid password", csrf=make_csrf())


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(config.UPLOAD_DIR, filename)

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/csrf", methods=["GET"])
def api_csrf():
    token = session.get("_csrf") or make_csrf()
    return jsonify({"csrf_token": token})

@app.route("/api/models")
def api_models():
    try:
        return jsonify(get_models())
    except Exception:
        app.logger.exception("Failed to fetch models")
        return jsonify({"error": "model provider unavailable"}), 502

@app.route("/api/conversations", methods=["GET"])
def api_list_conversations():
    try:
        limit = _parse_int_arg("limit", 50, 1, 200)
        offset = _parse_int_arg("offset", 0, 0, 10000)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(list_conversations(limit, offset))

@app.route("/api/conversations", methods=["POST"])
def api_create_conversation():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid json payload"}), 400

    model_id = _clean_text(data.get("model_id"), 200, "openai/gpt-4o-mini")
    title = _clean_text(data.get("title"), 200, "New Chat")
    system_prompt = _clean_text(data.get("system_prompt"), 10000, None)
    conv = create_conversation(
        model_id=model_id,
        title=title,
        system_prompt=system_prompt,
    )
    return jsonify(conv), 201

@app.route("/api/conversations/<cid>", methods=["PATCH"])
def api_update_conversation(cid):
    if not get_conversation(cid):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid json payload"}), 400

    cleaned = {}
    if "title" in data:
        cleaned["title"] = _clean_text(data.get("title"), 200, "New Chat")
    if "system_prompt" in data:
        cleaned["system_prompt"] = _clean_text(data.get("system_prompt"), 10000, None)
    if "model_id" in data:
        model_id = _clean_text(data.get("model_id"), 200, None)
        if not model_id:
            return jsonify({"error": "model_id cannot be empty"}), 400
        cleaned["model_id"] = model_id

    conv = update_conversation(cid, **cleaned)
    return jsonify(conv)

@app.route("/api/conversations/<cid>", methods=["DELETE"])
def api_delete_conversation(cid):
    if not get_conversation(cid):
        return jsonify({"error": "not found"}), 404
    delete_conversation(cid)
    return "", 204

@app.route("/api/conversations/<cid>/messages", methods=["GET"])
def api_get_messages(cid):
    if not get_conversation(cid):
        return jsonify({"error": "not found"}), 404
    return jsonify(get_messages(cid))

@app.route("/api/conversations/<cid>/messages", methods=["POST"])
def api_post_message(cid):
    incognito = request.form.get("incognito", "false").lower() == "true"

    if not incognito:
        conv = get_conversation(cid)
        if not conv:
            return jsonify({"error": "not found"}), 404
        model_id = conv["model_id"]
        system_prompt = conv.get("system_prompt")
        history = get_messages(cid)
    else:
        # Pull model_id and system_prompt from form for incognito
        model_id = _clean_text(request.form.get("model_id"), 200, "openai/gpt-4o-mini")
        system_prompt = _clean_text(request.form.get("system_prompt"), 10000, None)
        history = []

    user_text = _clean_text(request.form.get("message"), 20000, "") or ""
    image_file = request.files.get("image")
    reasoning_enabled = request.form.get("reasoning", "false").lower() == "true"

    if not user_text and not image_file:
        return jsonify({"error": "message or image is required"}), 400

    image_path = None
    if image_file:
        try:
            image_path = _save_uploaded_image(image_file)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    def generate():
        try:
            yield from _agentic_loop(
                cid=cid,
                model_id=model_id,
                system_prompt=system_prompt,
                history=history,
                user_text=user_text,
                image_path=image_path,
                incognito=incognito,
                reasoning_enabled=reasoning_enabled,
            )
        except Exception as e:
            app.logger.exception("Message pipeline failed")
            yield f"data: {json.dumps({'type':'error','message':_friendly_error(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    try:
        limit = _parse_int_arg("limit", 20, 1, 100)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not q:
        return jsonify([])
    try:
        return jsonify(search_messages(q, limit))
    except Exception:
        app.logger.exception("Search failed")
        return jsonify({"error": "search unavailable"}), 500

# --- Helpers ---

def _friendly_error(exc: Exception) -> str:
    """Return a human-readable error message, preferring the provider's 'raw' field."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        raw = body.get("error", {}).get("metadata", {}).get("raw")
        if raw:
            return str(raw)
        msg = body.get("error", {}).get("message")
        if msg:
            return str(msg)
    return str(exc)

# --- Agentic loop ---

def _active_tools():
    tools = [WEB_SEARCH_SCHEMA, WEB_FETCH_SCHEMA]
    if config.ENABLE_UNSAFE_PYTHON_TOOL:
        tools.append(RUN_PYTHON_SCHEMA)
    return tools

def _tool_use_system_prompt() -> str:
    tool_names = ["web_search", "web_fetch"]
    if config.ENABLE_UNSAFE_PYTHON_TOOL:
        tool_names.append("run_python")
    tools_joined = ", ".join(tool_names)
    return (
        f"You have access to callable tools: {tools_joined}. "
        "When the user asks to use a tool, issue a tool call instead of describing a plan. "
        "If a tool result is needed, call the tool first and then answer using that result. "
        "Do not ask for confirmation unless required parameters are missing."
    )

def _model_supports_tools(model_id: str) -> bool:
    models = _models_cache.get("data") or []
    m = next((x for x in models if x["id"] == model_id), None)
    if m is None:
        return True  # assume support if unknown
    return m.get("supports_tools", True)

def _compose_system_prompt(system_prompt, use_tools: bool):
    from datetime import datetime
    now = datetime.now()
    date_line = f"Current date and time: {now.strftime('%A, %B %-d, %Y, %-I:%M %p')}"
    parts = [date_line]
    if system_prompt:
        parts.append(system_prompt.strip())
    if use_tools:
        parts.append(_tool_use_system_prompt())
    return "\n\n".join(parts)

def _requested_tool_name(user_text: str):
    text = (user_text or "").lower()
    if "web_search" in text or "web search" in text or "search the web" in text:
        return "web_search"
    if "web_fetch" in text or "web fetch" in text or "fetch this url" in text or "fetch url" in text:
        return "web_fetch"
    if "run_python" in text or "run python" in text or "python tool" in text:
        return "run_python" if config.ENABLE_UNSAFE_PYTHON_TOOL else None
    return None

def _build_openrouter_messages(system_prompt, history, user_text, image_path, use_tools):
    msgs = []
    final_system_prompt = _compose_system_prompt(system_prompt, use_tools)
    if final_system_prompt:
        msgs.append({"role": "system", "content": final_system_prompt})

    for m in history:
        if m["role"] == "tool":
            msgs.append({
                "role": "tool",
                "tool_call_id": m["tool_call_id"],
                "content": m["content"],
            })
        elif m["tool_calls"]:
            msgs.append({
                "role": "assistant",
                "content": m["content"],
                "tool_calls": json.loads(m["tool_calls"]),
            })
        else:
            content = m["content"] or ""
            if m["image_path"]:
                import base64
                with open(m["image_path"], "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                ext = os.path.splitext(m["image_path"])[1].lstrip(".")
                mime = f"image/{ext}" if ext else "image/jpeg"
                msgs.append({"role": m["role"], "content": [
                    {"type": "text", "text": content},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]})
            else:
                msgs.append({"role": m["role"], "content": content})

    # New user message
    if image_path:
        import base64
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(image_path)[1].lstrip(".")
        mime = f"image/{ext}" if ext else "image/jpeg"
        user_content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
    else:
        user_content = user_text

    msgs.append({"role": "user", "content": user_content})
    return msgs

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"

def _agentic_loop(cid, model_id, system_prompt, history, user_text, image_path, incognito, reasoning_enabled=False):
    available_tools = _active_tools()
    use_tools = _model_supports_tools(model_id) and bool(available_tools)
    force_tool_name = _requested_tool_name(user_text) if use_tools else None
    messages = _build_openrouter_messages(system_prompt, history, user_text, image_path, use_tools)

    new_messages = []
    user_image_path = image_path
    is_first_message = not incognito and len(history) == 0

    is_google = model_id.startswith("google-ai-studio/")
    api_model_id = model_id[len("google-ai-studio/"):] if is_google else model_id
    active_client = google_client if is_google and google_client else client

    reasoning_body = {"reasoning": {"effort": "medium"}} if reasoning_enabled else {"reasoning": {"exclude": True}}
    if not use_tools:
        yield _sse({"type": "tools_warning", "message": f"Model {model_id} does not support tools — web search, web fetch and code execution are disabled for this conversation."})

    while True:
        create_kwargs = dict(
            model=api_model_id,
            messages=messages,
            stream=True,
        )
        if not is_google:
            create_kwargs["extra_body"] = reasoning_body
        if use_tools:
            create_kwargs["tools"] = available_tools
            if force_tool_name:
                create_kwargs["tool_choice"] = {
                    "type": "function",
                    "function": {"name": force_tool_name},
                }
            else:
                create_kwargs["tool_choice"] = "auto"

        try:
            stream = active_client.chat.completions.create(**create_kwargs)
        except Exception as e:
            # Some providers reject specific tool_choice forms; degrade gracefully.
            if use_tools and "tool_choice" in create_kwargs and "tool_choice" in str(e).lower():
                fallback_kwargs = dict(create_kwargs)
                fallback_kwargs["tool_choice"] = "auto"
                try:
                    stream = active_client.chat.completions.create(**fallback_kwargs)
                except Exception:
                    fallback_kwargs.pop("tool_choice", None)
                    stream = active_client.chat.completions.create(**fallback_kwargs)
            else:
                raise

        content_parts = []
        reasoning_parts = []
        tool_calls_map = {}
        finish_reason = None
        usage = None
        google_thought_active = False

        for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = {"prompt_tokens": chunk.usage.prompt_tokens,
                             "completion_tokens": chunk.usage.completion_tokens}
                continue

            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta

            # OpenRouter reasoning field (DeepSeek R1, etc.)
            reasoning_text = getattr(delta, "reasoning", None)
            if reasoning_text:
                reasoning_parts.append(reasoning_text)
                yield _sse({"type": "reasoning_delta", "delta": reasoning_text})

            if delta.content:
                if is_google:
                    # Detect Google AI Studio thinking chunks via extra_content flag
                    raw_delta = chunk.model_dump().get("choices", [{}])[0].get("delta", {})
                    is_thought = raw_delta.get("extra_content", {}).get("google", {}).get("thought", False)
                    content = delta.content
                    if is_thought:
                        if not google_thought_active:
                            google_thought_active = True
                            if content.startswith("<thought>"):
                                content = content[len("<thought>"):]
                        if content:
                            reasoning_parts.append(content)
                            yield _sse({"type": "reasoning_delta", "delta": content})
                    else:
                        if google_thought_active:
                            google_thought_active = False
                            if content.startswith("</thought>"):
                                content = content[len("</thought>"):]
                        if content:
                            content_parts.append(content)
                            yield _sse({"type": "content_delta", "delta": content})
                else:
                    content_parts.append(delta.content)
                    yield _sse({"type": "content_delta", "delta": delta.content})

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    # Google AI Studio sends null index; assign sequential keys
                    idx = tc.index if tc.index is not None else len(tool_calls_map)
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": tc.id or f"call_{new_id()}",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls_map[idx]["id"] = tc.id
                    if tc.function.name:
                        tool_calls_map[idx]["function"]["name"] += tc.function.name
                    if tc.function.arguments:
                        tool_calls_map[idx]["function"]["arguments"] += tc.function.arguments

        assistant_content = "".join(content_parts) or None
        assistant_reasoning = "".join(reasoning_parts) or None
        tool_calls_list = [tool_calls_map[i] for i in sorted(tool_calls_map)]

        if tool_calls_list:
            force_tool_name = None
            tool_calls_json = json.dumps(tool_calls_list)
            # Gemma 4 requires explicit empty string content (not null) alongside tool calls
            msg_content = assistant_content if assistant_content is not None else ""
            messages.append({
                "role": "assistant",
                "content": msg_content,
                "tool_calls": tool_calls_list,
            })
            new_messages.append({
                "role": "assistant",
                "content": assistant_content,
                "reasoning": assistant_reasoning,
                "tool_calls": tool_calls_json,
            })

            tool_result_msgs = []
            for tc in tool_calls_list:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}

                yield _sse({"type": "tool_call", "tool": fn_name, "input": fn_args})

                if fn_name == "web_search":
                    result = web_search(**fn_args)
                elif fn_name == "web_fetch":
                    result = web_fetch(**fn_args)
                elif fn_name == "run_python":
                    if config.ENABLE_UNSAFE_PYTHON_TOOL:
                        result = run_python(**fn_args)
                        if isinstance(result, dict) and result.get("image"):
                            yield _sse({"type": "image_result", "data": result["image"], "mime": "image/png"})
                    else:
                        result = {"error": "run_python is disabled by server policy"}
                else:
                    result = {"error": f"Unknown tool: {fn_name}"}

                result_str = json.dumps(result) if isinstance(result, dict) else str(result)
                yield _sse({"type": "tool_result", "tool": fn_name, "output": result_str})

                tool_result_msgs.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                })
                new_messages.append({
                    "role": "tool",
                    "content": result_str,
                    "tool_call_id": tc["id"],
                })

            messages.extend(tool_result_msgs)

        else:
            # Done
            new_messages.append({
                "role": "assistant",
                "content": assistant_content,
                "reasoning": assistant_reasoning,
            })

            if usage:
                yield _sse({"type": "done", "usage": usage})
            else:
                yield _sse({"type": "done"})
            break

    # Persist to DB
    if not incognito:
        # Save the user message first
        user_msg_id = add_message(
            cid, "user", user_text,
            image_path=user_image_path
        )

        # Save all assistant/tool messages
        for m in new_messages:
            add_message(
                cid,
                role=m["role"],
                content=m.get("content"),
                reasoning=m.get("reasoning"),
                tool_calls=m.get("tool_calls"),
                tool_call_id=m.get("tool_call_id"),
            )

        touch_conversation(cid)

        if is_first_message and user_text:
            threading.Thread(
                target=_auto_title,
                args=(cid, user_text),
                daemon=True
            ).start()

def _auto_title(cid, first_message):
    title = first_message.strip()[:60]
    if len(first_message.strip()) > 60:
        title = title.rsplit(" ", 1)[0] + "…"
    if title:
        update_conversation(cid, title=title)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, debug=False)
