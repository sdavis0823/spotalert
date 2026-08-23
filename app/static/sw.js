// SpotAlert service worker — receives web-push messages and shows notifications.
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
