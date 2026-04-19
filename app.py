import json
import os
import time
import threading
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory
from openai import OpenAI

from config import config
from database import (
    init_db, create_conversation, get_conversation, list_conversations,
    update_conversation, delete_conversation, add_message, get_messages,
    search_messages, touch_conversation, new_id
)
from tools.web_search import web_search, WEB_SEARCH_SCHEMA
from tools.web_fetch import web_fetch, WEB_FETCH_SCHEMA
from tools.python_sandbox import run_python, RUN_PYTHON_SCHEMA

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = config.SECRET_KEY

os.makedirs(config.UPLOAD_DIR, exist_ok=True)
init_db()

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

@app.route("/")
def index():
    from flask import render_template
    return render_template("index.html")

@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(config.UPLOAD_DIR, filename)

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/models")
def api_models():
    try:
        return jsonify(get_models())
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.route("/api/conversations", methods=["GET"])
def api_list_conversations():
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    return jsonify(list_conversations(limit, offset))

@app.route("/api/conversations", methods=["POST"])
def api_create_conversation():
    data = request.get_json(force=True)
    model_id = data.get("model_id", "openai/gpt-4o-mini")
    conv = create_conversation(
        model_id=model_id,
        title=data.get("title", "New Chat"),
        system_prompt=data.get("system_prompt"),
    )
    return jsonify(conv), 201

@app.route("/api/conversations/<cid>", methods=["PATCH"])
def api_update_conversation(cid):
    if not get_conversation(cid):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True)
    conv = update_conversation(cid, **data)
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
        model_id = request.form.get("model_id", "openai/gpt-4o-mini")
        system_prompt = request.form.get("system_prompt")
        history = []

    user_text = request.form.get("message", "").strip()
    image_file = request.files.get("image")
    reasoning_enabled = request.form.get("reasoning", "false").lower() == "true"

    image_path = None
    if image_file:
        ext = os.path.splitext(image_file.filename)[1].lower() or ".jpg"
        fname = new_id() + ext
        image_path = os.path.join(config.UPLOAD_DIR, fname)
        image_file.save(image_path)

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
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"

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
    limit = int(request.args.get("limit", 20))
    if not q:
        return jsonify([])
    try:
        return jsonify(search_messages(q, limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Agentic loop ---

TOOLS = [WEB_SEARCH_SCHEMA, WEB_FETCH_SCHEMA, RUN_PYTHON_SCHEMA]

def _model_supports_tools(model_id: str) -> bool:
    models = _models_cache.get("data") or []
    m = next((x for x in models if x["id"] == model_id), None)
    if m is None:
        return True  # assume support if unknown
    return m.get("supports_tools", True)

def _build_openrouter_messages(system_prompt, history, user_text, image_path):
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})

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
    messages = _build_openrouter_messages(system_prompt, history, user_text, image_path)

    new_messages = []
    user_image_path = image_path
    is_first_message = not incognito and len(history) == 0

    is_google = model_id.startswith("google-ai-studio/")
    api_model_id = model_id[len("google-ai-studio/"):] if is_google else model_id
    active_client = google_client if is_google and google_client else client

    reasoning_body = {"reasoning": {"effort": "medium"}} if reasoning_enabled else {"reasoning": {"exclude": True}}
    use_tools = _model_supports_tools(model_id)
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
            create_kwargs["tools"] = TOOLS
            create_kwargs["tool_choice"] = "auto"

        stream = active_client.chat.completions.create(**create_kwargs)

        content_parts = []
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
                    idx = tc.index
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": tc.id or "",
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
        tool_calls_list = [tool_calls_map[i] for i in sorted(tool_calls_map)]

        if finish_reason == "tool_calls" and tool_calls_list:
            tool_calls_json = json.dumps(tool_calls_list)
            messages.append({
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls_list,
            })
            new_messages.append({
                "role": "assistant",
                "content": assistant_content,
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
                    result = run_python(**fn_args)
                    if isinstance(result, dict) and result.get("image"):
                        yield _sse({"type": "image_result", "data": result["image"], "mime": "image/png"})
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
