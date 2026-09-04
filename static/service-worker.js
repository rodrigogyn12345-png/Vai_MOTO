const CACHE_NAME = "vai-de-moto-v2";

self.addEventListener("install", event => {
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  // O VAI_DE_MOTO depende do servidor para login,
  // GPS, corridas e dados em tempo real.
  // Não interceptamos as requisições.
});
