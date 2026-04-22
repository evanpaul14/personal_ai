// All backend fetch wrappers + SSE stream consumer

const BASE = "";
let csrfToken = null;
let csrfInFlight = null;

function needsCsrf(method = "GET") {
  return ["POST", "PUT", "PATCH", "DELETE"].includes(String(method).toUpperCase());
}

export async function initCsrfToken(force = false) {
  if (csrfToken && !force) return csrfToken;
  if (csrfInFlight && !force) return csrfInFlight;

  csrfInFlight = fetch(BASE + "/api/csrf", {
    method: "GET",
    credentials: "same-origin",
  })
    .then(async (res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      csrfToken = body.csrf_token || null;
      return csrfToken;
    })
    .finally(() => {
      csrfInFlight = null;
    });

  return csrfInFlight;
}

async function apiFetch(path, options = {}, allowRetry = true) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});

  if (needsCsrf(method)) {
    if (!csrfToken) await initCsrfToken();
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  }

  const res = await fetch(BASE + path, {
    ...options,
    method,
    headers,
    credentials: "same-origin",
  });

  if (res.status === 403 && needsCsrf(method) && allowRetry) {
    await initCsrfToken(true);
    return apiFetch(path, options, false);
  }

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.error || msg; } catch {}
    throw new Error(msg);
  }
  return res;
}

export async function fetchModels() {
  const res = await apiFetch("/api/models");
  return res.json();
}

export async function fetchConversations(limit = 50, offset = 0) {
  const res = await apiFetch(`/api/conversations?limit=${limit}&offset=${offset}`);
  return res.json();
}

export async function createConversation(modelId, systemPrompt, title) {
  const res = await apiFetch("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId, system_prompt: systemPrompt, title }),
  });
  return res.json();
}

export async function updateConversation(cid, fields) {
  const res = await apiFetch(`/api/conversations/${cid}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  return res.json();
}

export async function deleteConversation(cid) {
  await apiFetch(`/api/conversations/${cid}`, { method: "DELETE" });
}

export async function fetchMessages(cid) {
  const res = await apiFetch(`/api/conversations/${cid}/messages`);
  return res.json();
}

export async function searchChats(query, limit = 20) {
  const res = await apiFetch(`/api/search?q=${encodeURIComponent(query)}&limit=${limit}`);
  return res.json();
}

/**
 * Send a message and return an async generator that yields parsed SSE events.
 * @param {string} cid - conversation id
 * @param {string} text - user message text
 * @param {File|null} imageFile - optional image
 * @param {boolean} incognito
 * @param {string} modelId - only used for incognito
 * @param {string|null} systemPrompt - only used for incognito
 */
export async function* sendMessage(cid, text, imageFile, incognito, modelId, systemPrompt, reasoning, signal) {
  const form = new FormData();
  form.append("message", text);
  form.append("incognito", incognito ? "true" : "false");
  form.append("reasoning", reasoning ? "true" : "false");
  if (imageFile) form.append("image", imageFile);
  if (incognito) {
    form.append("model_id", modelId);
    if (systemPrompt) form.append("system_prompt", systemPrompt);
  }

  if (!csrfToken) await initCsrfToken();
  const headers = csrfToken ? { "X-CSRF-Token": csrfToken } : {};

  let res = await fetch(`/api/conversations/${cid}/messages`, {
    method: "POST",
    headers,
    body: form,
    credentials: "same-origin",
    signal,
  });

  if (res.status === 403) {
    await initCsrfToken(true);
    const retryHeaders = csrfToken ? { "X-CSRF-Token": csrfToken } : {};
    res = await fetch(`/api/conversations/${cid}/messages`, {
      method: "POST",
      headers: retryHeaders,
      body: form,
      credentials: "same-origin",
      signal,
    });
  }

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.error || msg; } catch {}
    throw new Error(msg);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newline;
    while ((newline = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 2);
      if (chunk.startsWith("data: ")) {
        try {
          yield JSON.parse(chunk.slice(6));
        } catch {}
      }
    }
  }
}
