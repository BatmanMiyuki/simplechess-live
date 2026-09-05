/* =============================================================
   Service Worker — SimpleChess Live
   - Shell de l'app (HTML, manifest, config, icônes) : CACHE FIRST
   - Données (classement) : NETWORK FIRST avec repli sur le cache
     => la dernière copie connue reste consultable hors connexion.
   ============================================================= */
const CACHE_NAME = "simplechess-live-v1";
const SHELL_URLS = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./config.js",
  "./static/icons/icon-192.png",
  "./static/icons/icon-512.png",
  "./static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // ne pas toucher aux POST

  const url = new URL(req.url);

  // Données de classement (API amont ou notre backend) : network-first
  const isData =
    url.hostname === "api.echecs.com" || url.pathname.includes("/api/leaderboard") ||
    url.pathname.includes("/api/player");

  // Navigation : network-first, repli index.html en cache (hors-ligne)
  const isNav = req.mode === "navigate";

  if (isNav) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put("./index.html", copy));
          return res;
        })
        .catch(() => caches.match("./index.html"))
    );
    return;
  }

  if (isData) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // Le reste du shell : cache-first
  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req))
  );
});
