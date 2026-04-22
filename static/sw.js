const CACHE_NAME = "pai-v1";
const STATIC_ASSETS = [
  "/static/css/main.css",
  "/static/js/app.js",
  "/static/js/api.js",
  "/static/js/chat.js",
  "/static/js/models.js",
  "/static/js/history.js",
  "/static/js/settings.js",
  "/static/js/upload.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // HTML navigations should always hit network for current auth/session state.
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request));
    return;
  }

  // SSE streams — always network, never cache
  if (url.pathname.endsWith("/messages") && e.request.method === "POST") {
    return;
  }

  // API calls — network first, no cache fallback
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/uploads/")) {
    e.respondWith(fetch(e.request));
    return;
  }

  // Static assets — cache first
  e.respondWith(
    caches.match(e.request).then((cached) => {
      if (cached) return cached;
      return fetch(e.request).then((res) => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(e.request, clone));
        }
        return res;
      });
    })
  );
});
