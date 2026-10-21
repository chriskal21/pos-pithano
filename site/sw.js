// SamurAI bet: service worker για εγκατάσταση σαν εφαρμογή και λειτουργία χωρίς σύνδεση.
// Πάντα δοκιμάζει πρώτα το δίκτυο, ώστε να βλέπεις τις τελευταίες πιθανότητες. Μόνο αν δεν υπάρχει
// σύνδεση δείχνει την τελευταία αποθηκευμένη έκδοση.
const CACHE = "samuraibet-v1";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];
const KEEP = /\.(html|json|css|webmanifest)$|\/$|icons\//;

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;          // γραμματοσειρές, μετρητής κ.λπ.: όπως είναι
  const key = url.origin + url.pathname;                     // χωρίς ?t=…, ώστε να υπάρχει ένα αντίγραφο ανά αρχείο
  e.respondWith(
    fetch(req).then(res => {
      if (res.ok && KEEP.test(url.pathname)) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(key, copy));
      }
      return res;
    }).catch(() => caches.match(key).then(hit => hit ||
      (req.mode === "navigate" ? caches.match("index.html") : Response.error())))
  );
});
