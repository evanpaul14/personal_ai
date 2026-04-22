import { sendMessage } from "./api.js";
import { getCurrentModelId, currentModelSupportsVision } from "./models.js";
import { getReasoningEnabled } from "./settings.js";

const viewport = document.getElementById("chat-viewport");
const messagesContainer = document.getElementById("messages-container");
const emptyState = document.getElementById("empty-state");
const sendBtn = document.getElementById("send-btn");
const messageInput = document.getElementById("message-input");

let _streaming = false;
let _pendingImageFile = null;
let _latestAssistantEl = null;
let _userScrolled = false;
let _programmaticScroll = false;
let _abortController = null;

export function setPendingImage(file) { _pendingImageFile = file; }
export function clearPendingImage() { _pendingImageFile = null; }
export function isStreaming() { return _streaming; }
export function stopStreaming() { _abortController?.abort(); }

// Detect user-initiated scrolls — pause auto-scroll until they return to bottom
viewport.addEventListener("scroll", () => {
  if (_programmaticScroll) return;
  const distFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
  _userScrolled = distFromBottom > 80;
}, { passive: true });

// Render full conversation history from DB
export function renderHistory(messages) {
  messagesContainer.innerHTML = "";
  _latestAssistantEl = null;
  let lastAssistantEl = null;

  const pendingToolCalls = {};

  for (const m of messages) {
    if (m.role === "user") {
      appendUserMessage(m.content, m.image_path ? `/uploads/${m.image_path.split("/").pop()}` : null, true);

    } else if (m.role === "assistant") {
      const hasContent = m.content && m.content.trim();
      const hasReasoning = m.reasoning && m.reasoning.trim();

      if (hasContent || hasReasoning || !m.tool_calls) {
        const el = appendAssistantMessage(null, true);
        if (hasReasoning) appendReasoningDelta(el, m.reasoning);
        finalizeAssistantMessage(el, m.content || "");
        lastAssistantEl = el;
      }

      if (m.tool_calls) {
        try {
          for (const tc of JSON.parse(m.tool_calls)) {
            let args = {};
            try { args = JSON.parse(tc.function.arguments || "{}"); } catch {}
            pendingToolCalls[tc.id] = { name: tc.function.name, args };
          }
        } catch {}
      }

    } else if (m.role === "tool") {
      const tc = pendingToolCalls[m.tool_call_id];
      if (tc) {
        appendToolBlock(tc.name, tc.args, m.content, null);
        delete pendingToolCalls[m.tool_call_id];
      }
    }
  }

  if (lastAssistantEl) markLatest(lastAssistantEl);
  showEmpty(messages.length === 0);
  _userScrolled = false;
  scrollToBottom(false, true);
}

export function clearMessages() {
  messagesContainer.innerHTML = "";
  showEmpty(true);
}

function showEmpty(show) {
  emptyState.classList.toggle("hidden", !show);
}

function scrollToBottom(smooth = false, force = false) {
  if (!force && _userScrolled) return;
  _programmaticScroll = true;
  viewport.scrollTo({ top: viewport.scrollHeight, behavior: smooth ? "smooth" : "instant" });
  requestAnimationFrame(() => { _programmaticScroll = false; });
}

function timestamp() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function escHtml(str) {
  return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function makeMsg(role, nick, ts) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.innerHTML = `
    <div class="msg-gutter">
      <div class="msg-nick"><span class="lt">&lt;</span><span class="name">${escHtml(nick)}</span><span class="gt">&gt;</span></div>
      <span class="msg-ts">${escHtml(ts)}</span>
    </div>
    <div class="msg-body"></div>
  `;
  return el;
}

function makeCopyBtn(getTextFn) {
  const btn = document.createElement("button");
  btn.className = "msg-action copy";
  btn.textContent = "⎘ copy";
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(getTextFn());
      btn.textContent = "✓ copied";
      setTimeout(() => { btn.textContent = "⎘ copy"; }, 1200);
    } catch {}
  });
  return btn;
}

function addActions(el, opts = {}) {
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  actions.appendChild(makeCopyBtn(() => el.querySelector(".msg-body")?.innerText || ""));
  if (opts.retry) {
    const r = document.createElement("button");
    r.className = "msg-action retry";
    r.textContent = "↻ retry";
    r.addEventListener("click", () => {
      document.dispatchEvent(new CustomEvent("retryMessage", { detail: { text: opts.userText, msgEl: el } }));
    });
    actions.appendChild(r);
  }
  el.querySelector(".msg-body").appendChild(actions);
}

function markLatest(el) {
  messagesContainer.querySelectorAll(".msg.latest").forEach(m => m.classList.remove("latest"));
  if (el) el.classList.add("latest");
  _latestAssistantEl = el;
}

export function appendUserMessage(text, imageUrl = null, showActions = true) {
  showEmpty(false);
  const el = makeMsg("user", "you", timestamp());
  el.dataset.text = text || "";

  const body = el.querySelector(".msg-body");

  if (imageUrl) {
    const img = document.createElement("img");
    img.src = imageUrl;
    img.className = "msg-image";
    img.alt = "uploaded image";
    body.appendChild(img);
    if (text) { const p = document.createElement("p"); p.textContent = text; body.appendChild(p); }
  } else {
    const p = document.createElement("p");
    p.textContent = text;
    body.appendChild(p);
  }

  if (showActions) addActions(el);

  messagesContainer.appendChild(el);
  _userScrolled = false;
  scrollToBottom(true, true);
  return el;
}

export function appendAssistantMessage(userText = null, showActions = true) {
  showEmpty(false);
  const el = makeMsg("ai", "ai", timestamp());
  if (userText !== null) el.dataset.userText = userText;
  el._finalized = false;

  // Typing indicator until first content
  el.querySelector(".msg-body").innerHTML = `<div class="typing-indicator"><span></span><span></span><span></span></div>`;

  if (showActions) addActions(el, { retry: true, userText });

  messagesContainer.appendChild(el);
  markLatest(el);
  scrollToBottom(true);
  return el;
}

export function appendReasoningDelta(msgEl, delta) {
  let block = msgEl.querySelector(".reasoning");
  if (!block) {
    block = document.createElement("details");
    block.className = "reasoning";
    block.innerHTML = `
      <summary>
        <span class="tag">thinking</span>
        <span class="note">expand</span>
        <span class="chev">▸</span>
      </summary>
      <div class="reasoning-body"></div>`;
    const body = msgEl.querySelector(".msg-body");
    msgEl.insertBefore(block, body);
  }
  const content = block.querySelector(".reasoning-body");
  content.textContent = (content.textContent || "") + delta;
  // Update note with line count
  const note = block.querySelector(".note");
  if (note) note.textContent = content.textContent.split("\n").length + " lines · expand";
  scrollToBottom();
}

export function appendStreamDelta(msgEl, delta) {
  const body = msgEl.querySelector(".msg-body");
  // Clear typing indicator on first delta
  const typing = body.querySelector(".typing-indicator");
  if (typing) {
    body.innerHTML = "";
    msgEl._streamText = "";
  }
  msgEl._finalized = false;
  msgEl._streamText = (msgEl._streamText || "") + delta;

  if (msgEl._renderScheduled) return;
  msgEl._renderScheduled = true;
  requestAnimationFrame(() => {
    msgEl._renderScheduled = false;
    if (msgEl._finalized) return;
    renderStreamingMarkdown(msgEl);
  });
}

function appendStreamingCursor(body) {
  const cursor = document.createElement("span");
  cursor.className = "tt-cursor";

  const last = body.lastElementChild;
  if (!last) { body.appendChild(cursor); return; }
  if (last.tagName === "P" || last.tagName === "LI") { last.appendChild(cursor); return; }
  if (last.tagName === "UL" || last.tagName === "OL") {
    const lastItem = last.lastElementChild;
    (lastItem || body).appendChild(cursor); return;
  }
  if (last.tagName === "PRE") {
    const code = last.querySelector("code:last-child");
    (code || last).appendChild(cursor); return;
  }
  if (last.tagName === "IMG" || last.tagName === "HR" || last.tagName === "TABLE") {
    body.appendChild(cursor); return;
  }
  last.appendChild(cursor);
}

// Shared marked + DOMPurify config
if (typeof marked !== "undefined") marked.setOptions({ breaks: true, gfm: true });
const _purifyConfig = {
  FORBID_TAGS: ["style", "script", "svg", "math"],
  FORBID_ATTR: ["style", "onerror", "onload", "onclick", "onmouseover"],
};

function _renderMarkdown(text) {
  if (!text) return "";
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    return `<p>${text.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}</p>`;
  }
  const raw = marked.parse(text);
  return DOMPurify.sanitize(raw, _purifyConfig);
}

function _fixLinks(body) {
  body.querySelectorAll("a").forEach(a => {
    if (!a.href || a.protocol === "javascript:") { a.removeAttribute("href"); return; }
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
}

function renderStreamingMarkdown(msgEl) {
  const body = msgEl.querySelector(".msg-body");
  if (!body) return;
  const actions = body.querySelector(".msg-actions");

  body.innerHTML = _renderMarkdown(msgEl._streamText);
  _fixLinks(body);
  appendStreamingCursor(body);
  if (actions) body.appendChild(actions);
  scrollToBottom();
}

export function finalizeAssistantMessage(msgEl, fullText) {
  msgEl._finalized = true;
  msgEl._streamText = fullText || "";
  const body = msgEl.querySelector(".msg-body");
  const actions = body.querySelector(".msg-actions");

  body.innerHTML = _renderMarkdown(fullText);
  _fixLinks(body);
  if (actions) body.appendChild(actions);
  markLatest(msgEl);
}

export function appendToolBlock(toolName, input, output, imageData) {
  const inputStr = typeof input === "string" ? input : JSON.stringify(input, null, 2);
  let outputStr = typeof output === "string" ? output : JSON.stringify(output, null, 2);
  try { outputStr = JSON.stringify(JSON.parse(outputStr), null, 2); } catch {}

  const argPreview = (input && input.query) ? `"${input.query}"` :
                     (input && input.code) ? input.code.split("\n")[0].slice(0, 50) + (input.code.length > 50 ? "…" : "") :
                     JSON.stringify(input || {}).slice(0, 60);

  const imageHtml = imageData
    ? `<img src="data:image/png;base64,${imageData}" class="tool-image" alt="plot" />`
    : "";

  const d = document.createElement("details");
  d.className = "tool";
  d.innerHTML = `
    <summary>
      <span class="tag">TOOL</span>
      <span class="name">${escHtml(toolName)}</span>
      <span class="arg-preview">${escHtml(argPreview)}</span>
      <span class="status ok">● ok</span>
    </summary>
    <div class="tool-body">
      <div class="tool-label">input</div>
      <pre class="tool-pre">${escHtml(inputStr)}</pre>
      <div class="tool-label">output</div>
      <pre class="tool-pre">${escHtml(outputStr)}</pre>
      ${imageHtml}
    </div>`;

  messagesContainer.appendChild(d);
  scrollToBottom(true);
  return d;
}

export function appendErrorMessage(text) {
  const el = makeMsg("err", "err", timestamp());
  el.querySelector(".msg-body").innerHTML = `<p>⚠ ${escHtml(text)}</p>`;
  messagesContainer.appendChild(el);
  scrollToBottom(true);
}

// Core send logic
export async function executeSend(cid, text, imageFile, incognito, systemPrompt) {
  if (_streaming) return;
  if (!text && !imageFile) return;

  _streaming = true;
  _abortController = new AbortController();
  sendBtn.classList.add("stop");

  const modelId = getCurrentModelId();
  const reasoning = getReasoningEnabled();
  let currentAssistantEl = appendAssistantMessage(text, true);
  let fullContent = "";
  let lastToolInput = null;
  let lastToolName = null;
  let pendingImageData = null;

  try {
    const stream = sendMessage(cid, text, imageFile, incognito, modelId, systemPrompt, reasoning, _abortController.signal);
    for await (const event of stream) {
      if (event.type === "reasoning_delta") {
        appendReasoningDelta(currentAssistantEl, event.delta);

      } else if (event.type === "content_delta") {
        fullContent += event.delta;
        appendStreamDelta(currentAssistantEl, event.delta);

      } else if (event.type === "tool_call") {
        lastToolName = event.tool;
        lastToolInput = event.input;
        pendingImageData = null;

      } else if (event.type === "image_result") {
        pendingImageData = event.data;

      } else if (event.type === "tool_result") {
        appendToolBlock(lastToolName, lastToolInput, event.output, pendingImageData);
        pendingImageData = null;
        lastToolName = null;
        lastToolInput = null;

      } else if (event.type === "done") {
        if (currentAssistantEl) finalizeAssistantMessage(currentAssistantEl, fullContent);
        document.dispatchEvent(new CustomEvent("conversationUpdated", { detail: { cid } }));

      } else if (event.type === "tools_warning") {
        document.dispatchEvent(new CustomEvent("toolsWarningSSE", { detail: event }));
      } else if (event.type === "error") {
        appendErrorMessage(event.message);
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      // User stopped — keep whatever was generated so far
      if (currentAssistantEl) finalizeAssistantMessage(currentAssistantEl, fullContent);
    } else {
      if (currentAssistantEl) currentAssistantEl.remove();
      appendErrorMessage(err.message);
    }
  } finally {
    _streaming = false;
    _abortController = null;
    _userScrolled = false;
    sendBtn.classList.remove("stop");
    messageInput.focus();
  }
}

export async function sendCurrentMessage(cid, incognito, systemPrompt) {
  const text = messageInput.value.trim();
  const imageFile = _pendingImageFile;
  if (!text && !imageFile) return;
  if (!cid && !incognito) return;

  messageInput.value = "";
  autoResizeTextarea();
  const imagePreviewUrl = imageFile ? URL.createObjectURL(imageFile) : null;
  appendUserMessage(text, imagePreviewUrl, true);
  clearPendingImage();
  document.dispatchEvent(new CustomEvent("imageClear"));

  await executeSend(cid, text, imageFile, incognito, systemPrompt);
}

function autoResizeTextarea() {
  messageInput.style.height = "auto";
  messageInput.style.height = Math.min(messageInput.scrollHeight, 140) + "px";
}
messageInput.addEventListener("input", autoResizeTextarea);
