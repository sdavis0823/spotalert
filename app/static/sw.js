// SpotAlert service worker — makes the app installable (PWA) and receives push.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
// Network-first passthrough (a fetch handler is required for installability).
self.addEventListener("fetch", () => {});

self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data && event.data.text() }; }
  const title = data.title || "✈︎ SpotAlert";
  const options = {
    body: data.body || "Notable aircraft detected.",
    tag: data.tag || "spotalert",
    data: { url: data.url || "/" },
    badge: undefined,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(clients.matchAll({ type: "window" }).then(list => {
    for (const c of list) { if (c.url.includes(url) && "focus" in c) return c.focus(); }
    return clients.openWindow(url);
  }));
});
