'use strict';

const LQ_ROOT_ID   = 'lq-map-root';
const LQ_DATA_ID   = 'lq-geojson-data';
const LQ_STATUS_ID = 'lq-status';

// ── Asset URL resolver (overridden by userscripts) ─────────────────────────
function assetURL(path) {
  return chrome.runtime.getURL(path);
}

// ── POI fetcher (overridden by userscripts) ────────────────────────────────
function fetchPOIs(lat, lon, listing_id) {
  return new Promise(resolve => {
    chrome.runtime.sendMessage(
      { type: 'FETCH_POIS', lat, lon, zillow_id: listing_id },
      resolve
    );
  });
}

// ── Extract Zillow ID from URL ─────────────────────────────────────────────
function extractListingId() {
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

function findAnchor() {
  for (const h2 of document.querySelectorAll('h2')) {
    if (h2.textContent.trimStart().startsWith('Neighborhood:')) return h2;
  }
  return null;
}

function waitForAnchor(timeoutMs = 15000) {
  return new Promise(resolve => {
    const found = findAnchor();
    if (found) { resolve(found); return; }

    const timer = setTimeout(() => { observer.disconnect(); resolve(null); }, timeoutMs);
    const observer = new MutationObserver(() => {
      const el = findAnchor();
      if (el) { clearTimeout(timer); observer.disconnect(); resolve(el); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  });
}

// ── DOM injection ──────────────────────────────────────────────────────────

function injectMapContainer(anchor, listing_id, backendBase) {
  if (document.getElementById(LQ_ROOT_ID)) return;

  const wrapper = document.createElement('div');
  wrapper.id = LQ_ROOT_ID;

  const viewUrl = backendBase
    ? `${backendBase}/zillow/${listing_id}`
    : `http://localhost:5010/zillow/${listing_id}`;

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

  const section = anchor.closest('section, article, [data-testid]') || anchor.parentElement;
  if (section && section.parentElement) {
    section.after(wrapper);
  } else {
    anchor.after(wrapper);
  }
}

// ── Font Awesome asset injection ───────────────────────────────────────────

function injectFontAwesomeAssets() {
  if (window.__lqFaReady) return;
  ['libs/fontawesome/css/fontawesome.min.css', 'libs/fontawesome/css/solid.min.css'].forEach(path => {
    const link = document.createElement('link');
    link.rel  = 'stylesheet';
    link.href = assetURL(path);
    document.head.appendChild(link);
  });
  window.__lqFaReady = true;
}

// ── Leaflet asset injection ────────────────────────────────────────────────

function injectLeafletAssets() {
  return new Promise(resolve => {
    if (window.__lqLeafletReady) { resolve(); return; }

    const link  = document.createElement('link');
    link.rel    = 'stylesheet';
    link.href   = assetURL('libs/leaflet.css');
    document.head.appendChild(link);

    const script   = document.createElement('script');
    script.src     = assetURL('libs/leaflet.js');
    script.onload  = () => { window.__lqLeafletReady = true; resolve(); };
    script.onerror = () => resolve();
    document.head.appendChild(script);
  });
}

// ── Map initialization bridge ──────────────────────────────────────────────

function initMap(lat, lon, geojson, backendBase, listing_id) {
  const loadingEl = document.getElementById('lq-loading');
  if (loadingEl) loadingEl.remove();

  const existing = document.getElementById(LQ_DATA_ID);
  if (existing) existing.remove();

  const dataEl       = document.createElement('script');
  dataEl.type        = 'application/json';
  dataEl.id          = LQ_DATA_ID;
  dataEl.textContent = JSON.stringify({ lat, lon, geojson, backendBase, listing_id });
  document.head.appendChild(dataEl);

  const initScript = document.createElement('script');
  initScript.src   = assetURL('map-init.js');
  document.head.appendChild(initScript);
}

// ── Main ───────────────────────────────────────────────────────────────────

async function run() {
  if (window.__lqRan) return;
  window.__lqRan = true;

  const listing_id = extractListingId();
  if (!listing_id) return;

  const coords = extractCoordinates();
  if (!coords) {
    console.warn('[LeQuartier] Could not extract coordinates from this Zillow page.');
    return;
  }

  console.log('[LeQuartier] coords:', coords, 'listing_id:', listing_id);

  const anchor = await waitForAnchor();
  if (!anchor) {
    console.warn('[LeQuartier] Neighborhood section not found within timeout.');
    return;
  }

  injectMapContainer(anchor, listing_id, '');
  injectFontAwesomeAssets();
  await injectLeafletAssets();

  const statusEl = document.getElementById(LQ_STATUS_ID);
  if (statusEl) statusEl.textContent = 'Fetching POIs…';

  const response = await fetchPOIs(coords.lat, coords.lon, listing_id);

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

  const link = document.querySelector(`#${LQ_ROOT_ID} .lq-view-link`);
  if (link && response.backendBase) {
    link.href = `${response.backendBase}/zillow/${listing_id}`;
  }

  initMap(coords.lat, coords.lon, response.geojson, response.backendBase, listing_id);
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
