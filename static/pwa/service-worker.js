const CACHE_NAME = "vaimoto-shell-v1";

self.addEventListener("install", event => {
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", event => {
  // O aplicativo depende do servidor para login, GPS e corridas.
  // Não interceptamos as requisições para evitar dados desatualizados.
});
