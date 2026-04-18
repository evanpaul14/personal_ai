import { fetchConversations, deleteConversation, updateConversation } from "./api.js";

const list = document.getElementById("conversation-list");
let _conversations = [];
let _activeCid = null;
let _onSelect = null;

export function setOnSelect(fn) { _onSelect = fn; }
export function getActiveCid() { return _activeCid; }
export function setActiveCid(cid) {
  _activeCid = cid;
  _renderActive();
}

export async function loadConversations() {
  try {
    _conversations = await fetchConversations();
    _render();
  } catch (err) {
    console.error("Failed to load conversations:", err);
  }
}

export async function refreshConversations() {
  await loadConversations();
}

function _render() {
  list.innerHTML = "";
  if (!_conversations.length) {
    const empty = document.createElement("div");
    empty.className = "search-empty";
    empty.textContent = "no conversations yet";
    list.appendChild(empty);
    return;
  }
  for (const conv of _conversations) {
    list.appendChild(_makeItem(conv));
  }
  _renderActive();
}

function _renderActive() {
  list.querySelectorAll(".conv-item").forEach(el => {
    el.classList.toggle("active", el.dataset.cid === _activeCid);
  });
}

function _makeItem(conv) {
  const item = document.createElement("div");
  item.className = "conv-item";
  item.dataset.cid = conv.id;
  item.setAttribute("role", "listitem");

  const titleEl = document.createElement("span");
  titleEl.className = "conv-title";
  titleEl.textContent = conv.title || "New Chat";
  titleEl.title = conv.title || "New Chat";

  // Double-click to rename
  titleEl.addEventListener("dblclick", (e) => {
    e.stopPropagation();
    _startRename(item, titleEl, conv);
  });

  const delBtn = document.createElement("button");
  delBtn.className = "conv-delete btn-icon";
  delBtn.setAttribute("aria-label", `Delete ${conv.title}`);
  delBtn.title = "Delete";
  delBtn.innerHTML = `<svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="1" y1="1" x2="10" y2="10"/><line x1="10" y1="1" x2="1" y2="10"/></svg>`;

  delBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete "${conv.title}"?`)) return;
    try {
      await deleteConversation(conv.id);
      _conversations = _conversations.filter(c => c.id !== conv.id);
      item.remove();
      if (_activeCid === conv.id) {
        _activeCid = null;
        document.dispatchEvent(new CustomEvent("conversationDeleted", { detail: { cid: conv.id } }));
      }
      if (!_conversations.length) _render();
    } catch (err) {
      console.error("Delete failed:", err);
    }
  });

  item.appendChild(titleEl);
  item.appendChild(delBtn);

  item.addEventListener("click", () => {
    if (_activeCid === conv.id) return;
    _activeCid = conv.id;
    _renderActive();
    _onSelect?.(conv);
  });

  return item;
}

function _startRename(item, titleEl, conv) {
  const input = document.createElement("input");
  input.className = "conv-title-input";
  input.value = conv.title || "";
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  async function commit() {
    const newTitle = input.value.trim() || "New Chat";
    conv.title = newTitle;
    const newTitle2 = document.createElement("span");
    newTitle2.className = "conv-title";
    newTitle2.textContent = newTitle;
    newTitle2.title = newTitle;
    newTitle2.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      _startRename(item, newTitle2, conv);
    });
    input.replaceWith(newTitle2);
    try {
      await updateConversation(conv.id, { title: newTitle });
    } catch (err) {
      console.error("Rename failed:", err);
    }
  }

  input.addEventListener("blur", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    if (e.key === "Escape") { input.value = conv.title || ""; input.blur(); }
  });
}

// Update title in sidebar when auto-title fires
export function updateConvTitle(cid, title) {
  const conv = _conversations.find(c => c.id === cid);
  if (conv) {
    conv.title = title;
    const item = list.querySelector(`[data-cid="${cid}"]`);
    if (item) {
      const titleEl = item.querySelector(".conv-title");
      if (titleEl) { titleEl.textContent = title; titleEl.title = title; }
    }
  }
}

// Move a conversation to top (after message sent)
export function bumpConversation(cid) {
  const idx = _conversations.findIndex(c => c.id === cid);
  if (idx > 0) {
    const [conv] = _conversations.splice(idx, 1);
    _conversations.unshift(conv);
    _render();
  }
}
