// All backend fetch wrappers + SSE stream consumer

const BASE = "";

async function apiFetch(path, options = {}) {
  const res = await fetch(BASE + path, options);
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
export async function* sendMessage(cid, text, imageFile, incognito, modelId, systemPrompt, reasoning) {
  const form = new FormData();
  form.append("message", text);
  form.append("incognito", incognito ? "true" : "false");
  form.append("reasoning", reasoning ? "true" : "false");
  if (imageFile) form.append("image", imageFile);
  if (incognito) {
    form.append("model_id", modelId);
    if (systemPrompt) form.append("system_prompt", systemPrompt);
  }

  const res = await fetch(`/api/conversations/${cid}/messages`, {
    method: "POST",
    body: form,
  });

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
