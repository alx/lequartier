'use strict';

const LQ_ROOT_ID   = 'lq-zillow-map-root';
const LQ_DATA_ID   = 'lq-geojson-data';
const LQ_STATUS_ID = 'lq-status';

// ── Extract Zillow ID from URL ─────────────────────────────────────────────
function extractZillowId() {
  const m = window.location.pathname.match(/\/homedetails\/(.+?)\/?$/);
  return m ? m[1] : null;
}

// ── Coordinate extraction strategies ──────────────────────────────────────

function isValidCoord(lat, lon) {
  return typeof lat === 'number' && typeof lon === 'number'
    && Math.abs(lat) <= 90 && Math.abs(lon) <= 180
    && (lat !== 0 || lon !== 0);
}

function findCoordsRecursive(obj, depth) {
  if (depth > 15 || !obj || typeof obj !== 'object') return null;
  if (Array.isArray(obj)) {
    for (const v of obj) {
      const r = findCoordsRecursive(v, depth + 1);
      if (r) return r;
    }
    return null;
  }
  const lat = obj.latitude ?? obj.lat;
  const lon = obj.longitude ?? obj.lng ?? obj.lon;
  if (isValidCoord(lat, lon)) return { lat: +lat, lon: +lon };
  for (const v of Object.values(obj)) {
    const r = findCoordsRecursive(v, depth + 1);
    if (r) return r;
  }
  return null;
}

function extractFromNextData() {
  const el = document.getElementById('__NEXT_DATA__');
  if (!el) return null;
  try {
    return findCoordsRecursive(JSON.parse(el.textContent), 0);
  } catch { return null; }
}

function extractFromLdJson() {
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const d = JSON.parse(s.textContent);
      const nodes = Array.isArray(d['@graph']) ? d['@graph'] : [d];
      for (const node of nodes) {
        const lat = node.geo?.latitude;
        const lon = node.geo?.longitude;
        if (isValidCoord(+lat, +lon)) return { lat: +lat, lon: +lon };
      }
    } catch { continue; }
  }
  return null;
}

function extractFromScriptRegex() {
  // Match "latitude": <float> followed (within ~200 chars) by "longitude": <float>
  const re = /"(?:latitude|lat)"\s*:\s*(-?\d{1,3}\.\d{4,})[^]*?"(?:longitude|lng|lon)"\s*:\s*(-?\d{1,3}\.\d{4,})/;
  for (const s of document.querySelectorAll('script:not([src])')) {
    const text = s.textContent || '';
    if (!text.includes('latitude') && !text.includes('"lat"')) continue;
    const m = re.exec(text);
    if (m) {
      const lat = parseFloat(m[1]), lon = parseFloat(m[2]);
      if (isValidCoord(lat, lon)) return { lat, lon };
    }
  }
  return null;
}

function extractCoordinates() {
  return extractFromNextData()
      || extractFromLdJson()
      || extractFromScriptRegex();
}

// ── Neighborhood anchor detection ──────────────────────────────────────────

function findNeighborhoodAnchor() {
  for (const h2 of document.querySelectorAll('h2')) {
    if (h2.textContent.trimStart().startsWith('Neighborhood:')) return h2;
  }
  return null;
}

function waitForNeighborhoodSection(timeoutMs = 15000) {
  return new Promise(resolve => {
    const found = findNeighborhoodAnchor();
    if (found) { resolve(found); return; }

    const timer = setTimeout(() => { observer.disconnect(); resolve(null); }, timeoutMs);
    const observer = new MutationObserver(() => {
      const el = findNeighborhoodAnchor();
      if (el) { clearTimeout(timer); observer.disconnect(); resolve(el); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  });
}

// ── DOM injection ──────────────────────────────────────────────────────────

function injectMapContainer(anchor, zillow_id, backendBase) {
  if (document.getElementById(LQ_ROOT_ID)) return;

  const wrapper = document.createElement('div');
  wrapper.id = LQ_ROOT_ID;

  const viewUrl = backendBase
    ? `${backendBase}/zillow/${zillow_id}`
    : `http://localhost:5010/zillow/${zillow_id}`;

  wrapper.innerHTML = `
    <div id="lq-map-header">
      <span>🏘 Nearby Places (Le Quartier)</span>
      <span id="${LQ_STATUS_ID}" class="lq-status-text">Loading…</span>
      <a href="${viewUrl}" target="_blank" rel="noopener" class="lq-view-link">View full map ↗</a>
    </div>
    <div id="lq-map-el">
      <div id="lq-loading">
        <div class="lq-spinner"></div>
        <span id="lq-loading-text">Querying nearby places…</span>
      </div>
    </div>
    <div id="lq-cat-bar"></div>
  `;

  // Insert after the nearest section/article ancestor of the anchor
  const section = anchor.closest('section, article, [data-testid]') || anchor.parentElement;
  if (section && section.parentElement) {
    section.after(wrapper);
  } else {
    anchor.after(wrapper);
  }
}

// ── Leaflet asset injection ────────────────────────────────────────────────

function injectLeafletAssets() {
  return new Promise(resolve => {
    if (window.__lqLeafletReady) { resolve(); return; }

    const link  = document.createElement('link');
    link.rel    = 'stylesheet';
    link.href   = chrome.runtime.getURL('libs/leaflet.css');
    document.head.appendChild(link);

    const script   = document.createElement('script');
    script.src     = chrome.runtime.getURL('libs/leaflet.js');
    script.onload  = () => { window.__lqLeafletReady = true; resolve(); };
    script.onerror = () => resolve(); // fail gracefully
    document.head.appendChild(script);
  });
}

// ── Map initialization bridge ──────────────────────────────────────────────

function initMap(lat, lon, geojson, backendBase, zillow_id) {
  const loadingEl = document.getElementById('lq-loading');
  if (loadingEl) loadingEl.remove();

  const existing = document.getElementById(LQ_DATA_ID);
  if (existing) existing.remove();

  const dataEl       = document.createElement('script');
  dataEl.type        = 'application/json';
  dataEl.id          = LQ_DATA_ID;
  dataEl.textContent = JSON.stringify({ lat, lon, geojson, backendBase, zillow_id });
  document.head.appendChild(dataEl);

  const initScript = document.createElement('script');
  initScript.src   = chrome.runtime.getURL('map-init.js');
  document.head.appendChild(initScript);
}

// ── Main ───────────────────────────────────────────────────────────────────

async function run() {
  if (window.__lqRan) return;
  window.__lqRan = true;

  const zillow_id = extractZillowId();
  if (!zillow_id) return;

  const coords = extractCoordinates();
  if (!coords) {
    console.warn('[LeQuartier] Could not extract coordinates from this Zillow page.');
    return;
  }

  console.log('[LeQuartier] coords:', coords, 'zillow_id:', zillow_id);

  const anchor = await waitForNeighborhoodSection();
  if (!anchor) {
    console.warn('[LeQuartier] Neighborhood section not found within timeout.');
    return;
  }

  // Placeholder container while we fetch — backendBase unknown yet
  injectMapContainer(anchor, zillow_id, '');
  await injectLeafletAssets();

  const statusEl = document.getElementById(LQ_STATUS_ID);
  if (statusEl) statusEl.textContent = 'Fetching POIs…';

  chrome.runtime.sendMessage(
    { type: 'FETCH_POIS', lat: coords.lat, lon: coords.lon, zillow_id },
    response => {
      if (!response || response.error) {
        const loadingEl = document.getElementById('lq-loading');
        if (loadingEl) {
          loadingEl.innerHTML = `<span class="lq-error-text">⚠ ${response?.error || 'Unknown error'}</span>`;
        }
        if (statusEl) {
          statusEl.textContent = 'Error';
          statusEl.style.color = '#dc2626';
        }
        return;
      }

      const n = response.geojson?.features?.length ?? 0;
      if (statusEl) statusEl.textContent = `${n} place${n !== 1 ? 's' : ''}`;

      // Update view link now that we know the backend URL
      const link = document.querySelector('#lq-zillow-map-root .lq-view-link');
      if (link && response.backendBase) {
        link.href = `${response.backendBase}/zillow/${zillow_id}`;
      }

      initMap(coords.lat, coords.lon, response.geojson, response.backendBase, zillow_id);
    }
  );
}

// ── SPA navigation (Next.js pushState) ────────────────────────────────────

function onNavigate() {
  const existing = document.getElementById(LQ_ROOT_ID);
  if (existing) existing.remove();
  const dataEl = document.getElementById(LQ_DATA_ID);
  if (dataEl) dataEl.remove();
  delete window.__lqRan;
  delete window.__lqLeafletReady;
  setTimeout(run, 800);
}

const _origPush    = history.pushState.bind(history);
const _origReplace = history.replaceState.bind(history);
history.pushState    = (...args) => { _origPush(...args);    onNavigate(); };
history.replaceState = (...args) => { _origReplace(...args); onNavigate(); };
window.addEventListener('popstate', onNavigate);

run();
