/* AUOREO app-shell service worker (opt-in from Settings → "Install as app (PWA)").
 *
 * Caches ONLY the static app shell: index.html + the hashed build assets listed in
 * /sw-assets.json (written by the Vite `swAssets` plugin, which also stamps BUILD below so
 * every build is a new SW version). Live traffic is never cached or replayed:
 * /api/*, /ws, /hooks/*, /v1/*, /mcp, /acp, /docs, /openapi.json and any text/event-stream
 * go straight to the network. Offline navigations get the cached index.html.
 */
const BUILD = "__AUOREO_BUILD__";
const PREFIX = "auoreo-shell-";
const CACHE = `${PREFIX}${BUILD}`;
const SHELL = "/index.html";
// The server sends `Vary: Origin` (CORS) on every file; module scripts carry an Origin header
// the precache request did not, so matching must ignore Vary (static files are identical).
const MATCH = { cacheName: CACHE, ignoreVary: true };

const NETWORK_ONLY = [/^\/api(\/|$)/, /^\/ws(\/|$)/, /^\/hooks(\/|$)/, /^\/v1(\/|$)/, /^\/mcp(\/|$)/, /^\/acp(\/|$)/, /^\/docs(\/|$)/, /^\/redoc(\/|$)/, /^\/openapi\.json$/, /^\/sw(-assets\.json|\.js)$/];

function networkOnly(request, url) {
  if (url.origin !== self.location.origin) return true;
  if (request.method !== "GET") return true;
  if (NETWORK_ONLY.some((re) => re.test(url.pathname))) return true;
  const accept = request.headers.get("accept") || "";
  return accept.includes("text/event-stream") || request.headers.get("upgrade") === "websocket";
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const res = await fetch("/sw-assets.json", { cache: "no-store" });
      const assets = res.ok ? (await res.json()).assets : [];
      const cache = await caches.open(CACHE);
      await cache.addAll([SHELL, ...assets.filter((a) => a !== SHELL)]);
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k.startsWith(PREFIX) && k !== CACHE).map((k) => caches.delete(k)));
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (networkOnly(request, url)) return; // default browser handling: never cached here

  if (request.mode === "navigate") {
    // Network first so a running server always serves the freshest shell; cache when offline.
    event.respondWith(
      fetch(request).catch(async () => (await caches.match(SHELL, MATCH)) || Response.error()),
    );
    return;
  }

  // Hashed assets are immutable: cache first, network for anything not precached (not stored).
  event.respondWith(caches.match(request, MATCH).then((hit) => hit || fetch(request)));
});
