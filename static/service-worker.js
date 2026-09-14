const CACHE_NAME = "vai-de-moto-v3";

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

// Recebe a notificação Push mesmo quando o painel não está aberto.
self.addEventListener("push", event => {
  let dados = {};

  try {
    dados = event.data ? event.data.json() : {};
  } catch (e) {
    dados = {};
  }

  const titulo = dados.title || "VAI_DE_MOTO";
  const opcoes = {
    body: dados.body || "Nova corrida disponível!",
    icon: "/icone/icon-192.png",
    badge: "/icone/icon-192.png",
    vibrate: [300, 150, 300, 150, 500],
    requireInteraction: true,
    data: dados.data || {
      url: "/motorista"
    }
  };

  event.waitUntil(
    self.registration.showNotification(titulo, opcoes)
  );
});

// Ao tocar na notificação, abre ou retorna para o painel do motorista.
self.addEventListener("notificationclick", event => {
  event.notification.close();

  const url =
    (event.notification.data && event.notification.data.url) ||
    "/motorista";

  event.waitUntil(
    clients.matchAll({
      type: "window",
      includeUncontrolled: true
    }).then(lista => {
      for (const cliente of lista) {
        if ("focus" in cliente) {
          cliente.navigate(url);
          return cliente.focus();
        }
      }

      if (clients.openWindow) {
        return clients.openWindow(url);
      }
    })
  );
});

self.addEventListener("fetch", event => {
  // O VAI_DE_MOTO depende do servidor para login,
  // GPS, corridas e dados em tempo real.
  // Não interceptamos as requisições.
});
