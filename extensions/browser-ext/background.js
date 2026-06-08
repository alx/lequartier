const DEFAULT_BACKEND = 'http://localhost:5010';

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== 'FETCH_POIS') return false;

  (async () => {
    try {
      const stored = await chrome.storage.sync.get('backendUrl');
      const base   = (stored.backendUrl || '').trim() || DEFAULT_BACKEND;

      const url = new URL('/api/nearby', base);
      url.searchParams.set('lat', message.lat);
      url.searchParams.set('lon', message.lon);
      if (message.radius)    url.searchParams.set('radius', message.radius);
      if (message.zillow_id) url.searchParams.set('zillow_id', message.zillow_id);

      const resp = await fetch(url.toString());
      if (!resp.ok) {
        sendResponse({ error: `HTTP ${resp.status} from Le Quartier backend` });
        return;
      }
      const geojson = await resp.json();
      sendResponse({ geojson, backendBase: base });
    } catch (err) {
      const msg = err.message.includes('fetch')
        ? 'Cannot reach the Le Quartier server. Is it running?'
        : err.message;
      sendResponse({ error: msg });
    }
  })();

  return true; // keep message port open for async response
});
