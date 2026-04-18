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

export function setPendingImage(file) { _pendingImageFile = file; }
export function clearPendingImage() { _pendingImageFile = null; }
export function isStreaming() { return _streaming; }

// Render full conversation history from DB
export function renderHistory(messages) {
  messagesContainer.innerHTML = "";
  _latestAssistantEl = null;
  let lastAssistantEl = null;
  for (const m of messages) {
    if (m.role === "user") {
      appendUserMessage(m.content, m.image_path ? `/uploads/${m.image_path.split("/").pop()}` : null, true);
    } else if (m.role === "assistant") {
      const el = appendAssistantMessage(null, true);
      finalizeAssistantMessage(el, m.content || "");
      lastAssistantEl = el;
    }
  }
  // Mark only the last assistant message as latest
  if (lastAssistantEl) markLatest(lastAssistantEl);
  showEmpty(messages.length === 0);
  scrollToBottom();
}

export function clearMessages() {
  messagesContainer.innerHTML = "";
  showEmpty(true);
}

function showEmpty(show) {
  emptyState.classList.toggle("hidden", !show);
}

function scrollToBottom(smooth = false) {
  viewport.scrollTo({ top: viewport.scrollHeight, behavior: smooth ? "smooth" : "instant" });
}

function timestamp() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function makeCopyBtn(getTextFn) {
  const btn = document.createElement("button");
  btn.className = "msg-action-btn copy-btn";
  btn.title = "Copy";
  btn.setAttribute("aria-label", "Copy message");
  btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
  btn.addEventListener("click", async () => {
    const text = getTextFn();
    try {
      await navigator.clipboard.writeText(text);
      btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
      setTimeout(() => {
        btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
      }, 1500);
    } catch {}
  });
  return btn;
}

export function appendUserMessage(text, imageUrl = null, showActions = true) {
  showEmpty(false);
  const wrap = document.createElement("div");
  wrap.className = "message user";
  wrap.dataset.text = text || "";

  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = `you · ${timestamp()}`;

  if (showActions) {
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    actions.appendChild(makeCopyBtn(() => wrap.dataset.text));
    meta.appendChild(actions);
  }

  const body = document.createElement("div");
  body.className = "message-body";

  if (imageUrl) {
    const img = document.createElement("img");
    img.src = imageUrl;
    img.className = "message-image";
    img.alt = "uploaded image";
    body.appendChild(img);
    if (text) { const p = document.createElement("p"); p.textContent = text; body.appendChild(p); }
  } else {
    body.textContent = text;
  }

  wrap.appendChild(meta);
  wrap.appendChild(body);
  messagesContainer.appendChild(wrap);
  scrollToBottom(true);
  return wrap;
}

function markLatest(el) {
  // Remove latest from previous
  messagesContainer.querySelectorAll(".message.latest").forEach(m => m.classList.remove("latest"));
  if (el) el.classList.add("latest");
  _latestAssistantEl = el;
}

export function appendAssistantMessage(userText = null, showActions = true) {
  showEmpty(false);
  const wrap = document.createElement("div");
  wrap.className = "message assistant";
  if (userText !== null) wrap.dataset.userText = userText;

  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = `ai · ${timestamp()}`;

  if (showActions) {
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    actions.appendChild(makeCopyBtn(() => {
      const body = wrap.querySelector(".message-body");
      return body ? body.innerText : "";
    }));
    if (userText !== null) {
      const retryBtn = document.createElement("button");
      retryBtn.className = "msg-action-btn retry-btn";
      retryBtn.title = "Retry";
      retryBtn.setAttribute("aria-label", "Retry response");
      retryBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>`;
      retryBtn.addEventListener("click", () => {
        document.dispatchEvent(new CustomEvent("retryMessage", { detail: { text: userText, msgEl: wrap } }));
      });
      actions.appendChild(retryBtn);
    }
    meta.appendChild(actions);
  }

  // Typing indicator shown until first content arrives
  const body = document.createElement("div");
  body.className = "message-body";
  body.innerHTML = `<div class="typing-indicator"><span></span><span></span><span></span></div>`;

  wrap.appendChild(meta);
  wrap.appendChild(body);
  messagesContainer.appendChild(wrap);
  markLatest(wrap);
  scrollToBottom(true);
  return wrap;
}

export function appendStreamDelta(msgEl, delta) {
  const body = msgEl.querySelector(".message-body");
  // Clear typing indicator on first delta
  const typing = body.querySelector(".typing-indicator");
  if (typing) {
    body.innerHTML = "";
    msgEl._streamText = "";
  }
  msgEl._streamText = (msgEl._streamText || "") + delta;

  if (msgEl._renderScheduled) return;
  msgEl._renderScheduled = true;
  requestAnimationFrame(() => {
    msgEl._renderScheduled = false;
    renderStreamingMarkdown(msgEl);
  });
}

function renderStreamingMarkdown(msgEl) {
  const body = msgEl.querySelector(".message-body");
  if (!body) return;
  const text = msgEl._streamText || "";
  if (typeof marked !== "undefined") {
    marked.setOptions({ breaks: true, gfm: true });
    body.innerHTML = marked.parse(text);
    body.querySelectorAll("a").forEach(a => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
  } else {
    body.textContent = text;
  }
  const cursor = document.createElement("span");
  cursor.className = "stream-cursor";
  body.appendChild(cursor);
  scrollToBottom();
}

export function finalizeAssistantMessage(msgEl, fullText) {
  const body = msgEl.querySelector(".message-body");
  body.innerHTML = "";
  if (typeof marked !== "undefined" && fullText) {
    marked.setOptions({ breaks: true, gfm: true });
    body.innerHTML = marked.parse(fullText);
    body.querySelectorAll("a").forEach(a => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
  } else {
    body.textContent = fullText || "";
  }
  markLatest(msgEl);
}

export function appendToolBlock(toolName, input, output, imageData) {
  const block = document.createElement("div");
  block.className = "tool-block";
  const inputStr = typeof input === "string" ? input : JSON.stringify(input, null, 2);
  let outputStr = typeof output === "string" ? output : JSON.stringify(output, null, 2);
  try { outputStr = JSON.stringify(JSON.parse(outputStr), null, 2); } catch {}
  const imageHtml = imageData
    ? `<img src="data:image/png;base64,${imageData}" class="sandbox-image" alt="plot" />`
    : "";
  block.innerHTML = `
    <details>
      <summary>
        <span class="tool-label">⚡ tool</span>
        <span class="tool-name">${escHtml(toolName)}</span>
        <span class="tool-chevron">▾</span>
      </summary>
      <div class="tool-content">
        <div class="tool-content-label">input</div>
        <pre>${escHtml(inputStr)}</pre>
        <div class="tool-content-label" style="margin-top:8px">output</div>
        <pre>${escHtml(outputStr)}</pre>
        ${imageHtml}
      </div>
    </details>`;
  messagesContainer.appendChild(block);
  scrollToBottom(true);
  return block;
}

export function appendErrorMessage(text) {
  const wrap = document.createElement("div");
  wrap.className = "message error-msg";
  wrap.innerHTML = `<div class="message-meta">error · ${timestamp()}</div><div class="message-body">⚠ ${escHtml(text)}</div>`;
  messagesContainer.appendChild(wrap);
  scrollToBottom(true);
}

function escHtml(str) {
  return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// Core send logic — shared by normal send and retry
export async function executeSend(cid, text, imageFile, incognito, systemPrompt) {
  if (_streaming) return;
  if (!text && !imageFile) return;

  _streaming = true;
  sendBtn.disabled = true;

  const modelId = getCurrentModelId();
  const reasoning = getReasoningEnabled();
  let currentAssistantEl = appendAssistantMessage(text, true);
  let fullContent = "";
  let lastToolInput = null;
  let lastToolName = null;
  let pendingImageData = null;

  try {
    const stream = sendMessage(cid, text, imageFile, incognito, modelId, systemPrompt, reasoning);
    for await (const event of stream) {
      if (event.type === "content_delta") {
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
    if (currentAssistantEl) currentAssistantEl.remove();
    appendErrorMessage(err.message);
  } finally {
    _streaming = false;
    sendBtn.disabled = false;
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
  messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + "px";
}
messageInput.addEventListener("input", autoResizeTextarea);
