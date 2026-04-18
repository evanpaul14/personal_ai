import { initModels, getCurrentModelId, currentModelSupportsVision, currentModelSupportsTools } from "./models.js";
import {
  renderHistory, clearMessages, sendCurrentMessage, executeSend, isStreaming, appendUserMessage
} from "./chat.js";
import {
  loadConversations, setOnSelect, setActiveCid, getActiveCid,
  refreshConversations, bumpConversation
} from "./history.js";
import { setCurrentCid, setOnSave, setSystemPromptValue, getSystemPromptValue } from "./settings.js";
import { initUpload, setVisionEnabled } from "./upload.js";
import { createConversation, fetchMessages, searchChats } from "./api.js";

// --- State ---
let currentCid = null;
let incognito = false;
let systemPrompt = null;

// --- DOM refs ---
const sidebar = document.getElementById("sidebar");
const sidebarOverlay = document.getElementById("sidebar-overlay");
const hamburger = document.getElementById("hamburger");
const newChatBtn = document.getElementById("new-chat-btn");
const sendBtn = document.getElementById("send-btn");
const messageInput = document.getElementById("message-input");
const incognitoCheckbox = document.getElementById("incognito-checkbox");
const incognitoBadge = document.getElementById("incognito-badge");
const searchInput = document.getElementById("search-input");
const convList = document.getElementById("conversation-list");
const searchResults = document.getElementById("search-results");

// --- Sidebar toggle ---
function openSidebar() {
  sidebar.classList.add("open");
  sidebarOverlay.classList.remove("hidden");
  hamburger.setAttribute("aria-expanded", "true");
}
function closeSidebar() {
  sidebar.classList.remove("open");
  sidebarOverlay.classList.add("hidden");
  hamburger.setAttribute("aria-expanded", "false");
}
hamburger.addEventListener("click", () => {
  sidebar.classList.contains("open") ? closeSidebar() : openSidebar();
});
sidebarOverlay.addEventListener("click", closeSidebar);

// Close sidebar on mobile after selecting a conversation
function closeSidebarIfMobile() {
  if (window.innerWidth < 768) closeSidebar();
}

// --- New chat ---
newChatBtn.addEventListener("click", () => {
  startNewChat();
  closeSidebarIfMobile();
});

function startNewChat() {
  currentCid = null;
  clearMessages();
  setActiveCid(null);
  setCurrentCid(null);
  systemPrompt = null;
  setSystemPromptValue(null);
}

// --- Load conversation ---
async function loadConversation(conv) {
  currentCid = conv.id;
  setActiveCid(conv.id);
  setCurrentCid(conv.id);
  systemPrompt = conv.system_prompt || null;
  setSystemPromptValue(systemPrompt);
  closeSidebarIfMobile();

  try {
    const messages = await fetchMessages(conv.id);
    renderHistory(messages);
  } catch (err) {
    console.error("Failed to load messages:", err);
  }
}

setOnSelect(loadConversation);

// --- System prompt save ---
setOnSave((prompt) => {
  systemPrompt = prompt || null;
});

// --- Incognito toggle ---
incognitoCheckbox.addEventListener("change", () => {
  incognito = incognitoCheckbox.checked;
  incognitoBadge.classList.toggle("hidden", !incognito);
});

// --- Send message ---
async function handleSend() {
  if (isStreaming()) return;
  const text = messageInput.value.trim();

  // Need either a conversation or create one first
  if (!incognito && !currentCid) {
    if (!text && !document.getElementById("upload-preview").classList.contains("hidden") === false) return;
    // Create conversation first
    try {
      const modelId = getCurrentModelId();
      const conv = await createConversation(modelId, systemPrompt, "New Chat");
      currentCid = conv.id;
      setActiveCid(conv.id);
      setCurrentCid(conv.id);
      await refreshConversations();
    } catch (err) {
      console.error("Failed to create conversation:", err);
      return;
    }
  }

  if (!incognito && !currentCid) return;

  await sendCurrentMessage(currentCid, incognito, systemPrompt);
}

sendBtn.addEventListener("click", handleSend);
messageInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
  // Shift+Enter inserts newline (default textarea behavior)
});

// --- Model change → update upload button + tools warning ---
const toolsWarning = document.getElementById("tools-warning");
const toolsWarningText = document.getElementById("tools-warning-text");

document.addEventListener("modelChanged", ({ detail }) => {
  setVisionEnabled(currentModelSupportsVision());
  const supportsTools = currentModelSupportsTools();
  toolsWarning.classList.toggle("hidden", supportsTools);
  if (!supportsTools && detail?.model) {
    toolsWarningText.textContent = `${detail.model.name} does not support tools — web search, web fetch and code execution are disabled`;
  }
});

// --- Conversation updated (after message sent) ---
document.addEventListener("conversationUpdated", async ({ detail }) => {
  if (detail.cid) {
    bumpConversation(detail.cid);
  }
});

// --- Conversation deleted ---
document.addEventListener("conversationDeleted", ({ detail }) => {
  if (detail.cid === currentCid) {
    startNewChat();
  }
});

// --- Retry ---
document.addEventListener("retryMessage", async ({ detail }) => {
  if (isStreaming()) return;
  const { text, msgEl } = detail;
  // Remove the assistant message and any tool blocks between it and the previous user msg
  let el = msgEl;
  while (el) {
    const prev = el.previousElementSibling;
    el.remove();
    if (!prev || prev.classList.contains("message") && prev.classList.contains("user")) break;
    el = prev;
  }
  appendUserMessage(text, null, true);
  await executeSend(currentCid, text, null, incognito, systemPrompt);
});

// --- Search ---
let _searchTimer = null;
searchInput.addEventListener("input", () => {
  clearTimeout(_searchTimer);
  const q = searchInput.value.trim();
  if (!q) {
    showConvList();
    return;
  }
  _searchTimer = setTimeout(() => runSearch(q), 300);
});
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    searchInput.value = "";
    showConvList();
  }
});

async function runSearch(q) {
  try {
    const results = await searchChats(q);
    showSearchResults(results, q);
  } catch (err) {
    console.error("Search failed:", err);
  }
}

function showConvList() {
  convList.classList.remove("hidden");
  searchResults.classList.add("hidden");
  searchResults.innerHTML = "";
}

function showSearchResults(results, query) {
  convList.classList.add("hidden");
  searchResults.classList.remove("hidden");
  searchResults.innerHTML = "";

  if (!results.length) {
    searchResults.innerHTML = `<div class="search-empty">no results for "${escHtml(query)}"</div>`;
    return;
  }

  for (const r of results) {
    const item = document.createElement("div");
    item.className = "search-result-item";
    item.setAttribute("role", "listitem");
    const roleLabel = r.role === "assistant" ? "ai" : r.role === "user" ? "you" : (r.role || "");
    item.innerHTML = `
      <div class="search-result-conv">${escHtml(r.conversation_title || "Chat")}</div>
      <div class="search-result-snippet"><span class="search-result-role">${escHtml(roleLabel)}</span> ${highlightSnippet(r.snippet)}</div>`;
    item.addEventListener("click", async () => {
      searchInput.value = "";
      showConvList();
      await loadConversation({
        id: r.conversation_id,
        system_prompt: r.conversation_system_prompt || null,
      });
    });
    searchResults.appendChild(item);
  }
}

// Snippets arrive with control-char markers (STX/ETX) around matched terms.
// Escape the user-authored content, then swap the markers for <mark> tags.
function highlightSnippet(snippet) {
  if (!snippet) return "";
  return escHtml(snippet)
    .replace(/\u0002/g, "<mark>")
    .replace(/\u0003/g, "</mark>");
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// --- Init ---
async function init() {
  initUpload();
  await Promise.all([initModels(), loadConversations()]);

  // Register service worker
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/sw.js").catch(() => {});
  }

  // iOS keyboard: shrink #main to visual viewport so input bar
  // sits just above the keyboard instead of underneath it
  if (window.visualViewport) {
    const main = document.getElementById("main");
    const chatViewport = document.getElementById("chat-viewport");

    const onViewportResize = () => {
      const vv = window.visualViewport;
      // offsetTop handles cases where the page itself has scrolled
      main.style.height = `${vv.height}px`;
      main.style.marginTop = `${vv.offsetTop}px`;
      // Keep chat scrolled to bottom when keyboard opens
      chatViewport.scrollTop = chatViewport.scrollHeight;
    };

    window.visualViewport.addEventListener("resize", onViewportResize);
    window.visualViewport.addEventListener("scroll", onViewportResize);
  }
}

// Handle tools_warning SSE event emitted by backend
document.addEventListener("toolsWarningSSE", ({ detail }) => {
  toolsWarning.classList.remove("hidden");
  toolsWarningText.textContent = detail.message;
});

init();
