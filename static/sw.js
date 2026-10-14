const CACHE_NAME = 'easytravel-cache-v1.0.1';
const OFFLINE_URL = '/offline/';

const PRECACHE_ASSETS = [
  '/',
  '/?source=pwa',
  OFFLINE_URL,
  '/manifest.json',
  '/static/images/pwa/icon-192x192.png',
  '/static/images/pwa/icon-512x512.png',
  '/static/images/pwa/favicon-32x32.png',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js',
  'https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap'
];

// 1. Installation du Service Worker et mise en cache des ressources essentielles
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[EasyTravel SW] Mise en cache des ressources statiques');
      return cache.addAll(PRECACHE_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

// 2. Activation et suppression des anciens caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((name) => {
          if (name !== CACHE_NAME) {
            console.log('[EasyTravel SW] Suppression ancien cache:', name);
            return caches.delete(name);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// 3. Stratégie réseau : Network-First pour les pages dynamiques, Cache-First pour les fichiers statiques
self.addEventListener('fetch', (event) => {
  const request = event.request;

  // On ignore les requêtes non-GET (POST, PUT, DELETE pour réservations/paiements)
  if (request.method !== 'GET') {
    return;
  }

  // Ne pas intercepter les requêtes vers l'admin Django ou les webhooks de paiement
  if (request.url.includes('/admin/') || request.url.includes('/paiements/cinetpay/')) {
    return;
  }

  // Pour les pages de navigation HTML : Network-First avec fallback vers la page Hors-Ligne
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((networkResponse) => {
          // Mettre à jour la page d'accueil dans le cache si la réponse est valide
          if (networkResponse.status === 200 && request.url.endsWith('/')) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseClone);
            });
          }
          return networkResponse;
        })
        .catch(async () => {
          console.log('[EasyTravel SW] Hors connexion, tentative chargement cache...');
          const cachedResponse = await caches.match(request, { ignoreSearch: true });
          if (cachedResponse) {
            return cachedResponse;
          }
          // Si la page demandée n'est pas en cache, retourner la page d'accueil ou l'écran hors-ligne
          return caches.match(OFFLINE_URL);
        })
    );
    return;
  }

  // Pour les assets statiques (CSS, JS, Fonts, Images) : Stale-While-Revalidate
  if (
    request.destination === 'style' ||
    request.destination === 'script' ||
    request.destination === 'image' ||
    request.destination === 'font'
  ) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        const fetchPromise = fetch(request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseClone);
            });
          }
          return networkResponse;
        }).catch(() => cachedResponse);

        return cachedResponse || fetchPromise;
      })
    );
  }
});
