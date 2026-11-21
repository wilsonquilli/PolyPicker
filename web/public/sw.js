/* PolyPicker service worker.
 *
 * Goals: installable app shell, fast repeat loads, and a useful offline experience without ever
 * passing off old forecasts as current:
 *   - Pages (navigations): network first. A copy of each successfully loaded page is kept; when
 *     offline the saved copy is shown (the app displays an "offline, may be outdated" banner),
 *     or the /offline page if that page was never visited.
 *   - Build assets (/_next/static, icons): cache first (file names are content-hashed).
 *   - API proxy (/api/v1), React Server Component payloads, prefetches: always network, never cached.
 *
 * Bump VERSION to force clients to drop old caches.
 */
const VERSION = "pp-v1";
const SHELL = `${VERSION}-shell`;
const PAGES = `${VERSION}-pages`;
const ASSETS = `${VERSION}-assets`;
const MAX_PAGES = 40;
const PRECACHE = ["/offline", "/icons/icon-192.png", "/icons/icon-512.png", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function isRscRequest(req, url) {
  return req.headers.get("RSC") === "1" || req.headers.has("Next-Router-Prefetch") || url.searchParams.has("_rsc");
}

async function trimCache(name, max) {
  const cache = await caches.open(name);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - max; i++) await cache.delete(keys[i]);
}

async function networkFirstPage(request) {
  try {
    const res = await fetch(request);
    if (res.ok && res.type === "basic") {
      const copy = res.clone();
      caches.open(PAGES).then((c) => c.put(request, copy)).then(() => trimCache(PAGES, MAX_PAGES));
    }
    return res;
  } catch {
    const saved = await caches.match(request, { ignoreVary: true });
    if (saved) return saved;
    return (await caches.match("/offline")) ?? new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } });
  }
}

async function cacheFirstAsset(request) {
  const hit = await caches.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok) {
    const copy = res.clone();
    caches.open(ASSETS).then((c) => c.put(request, copy));
  }
  return res;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;           // never touch third-party requests
  if (url.pathname.startsWith("/api/")) return;               // live data only
  if (isRscRequest(req, url)) return;                         // client navigations: let Next handle failures

  if (req.mode === "navigate") {
    event.respondWith(networkFirstPage(req));
    return;
  }
  if (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/") ||
      url.pathname === "/icon.png" || url.pathname === "/apple-icon.png") {
    event.respondWith(cacheFirstAsset(req));
  }
});
