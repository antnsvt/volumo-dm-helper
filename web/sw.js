/* Minimal service worker for the Volumo DM Helper PWA.
 *
 * Caches the app shell so the UI loads instantly on a second visit and works
 * offline if the user just wants to view their last-fetched dashboard. All
 * data requests (api.github.com) go straight to the network — never cached,
 * because they're per-PAT and time-sensitive.
 */
const SHELL_CACHE = 'volumo-dm-shell-v1';
const SHELL_ASSETS = [
  './',
  './index.html',
  './app.js',
  './style.css',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never cache API calls — always go to network.
  if (url.hostname === 'api.github.com') return;

  // Only handle same-origin GETs from here on.
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;

  // Cache-first for shell assets, network fallback to update.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const networkFetch = fetch(event.request).then((resp) => {
        if (resp && resp.ok) {
          const copy = resp.clone();
          caches.open(SHELL_CACHE).then((c) => c.put(event.request, copy));
        }
        return resp;
      }).catch(() => cached);
      return cached || networkFetch;
    })
  );
});
