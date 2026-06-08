// ==UserScript==
// @name         Le Quartier – Zillow Map
// @namespace    https://girard-davila.net
// @version      1.0.0
// @description  Embeds a neighbourhood POI map on Zillow property listings
// @author       Alexandre Girard-Davila
// @match        https://www.zillow.com/homedetails/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_getResourceText
// @grant        GM_addStyle
// @require      https://unpkg.com/leaflet@1.9.4/dist/leaflet.js
// @resource     leafletCSS  https://unpkg.com/leaflet@1.9.4/dist/leaflet.css
// @resource     faCSS       https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/fontawesome.min.css
// @resource     faSolidCSS  https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/solid.min.css
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const DEFAULT_BACKEND = 'https://lequartier.girard-davila.net';
  const LQ_ROOT_ID   = 'lq-map-root';
  const LQ_STATUS_ID = 'lq-status';

  // ── Inject CSS assets ────────────────────────────────────────────────────
  GM_addStyle(GM_getResourceText('leafletCSS'));
  GM_addStyle(GM_getResourceText('faCSS'));
  GM_addStyle(GM_getResourceText('faSolidCSS'));

  // Widget styles (equivalent to shared/styles.css)
  GM_addStyle(`
    #lq-map-root {
      margin: 20px 0 24px;
      font-family: system-ui, -apple-system, sans-serif;
      font-size: 14px;
      line-height: 1.4;
    }
    #lq-map-header {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
      background: #f0fdf4; border-left: 3px solid #1a6b3c;
      border-radius: 0 6px 0 0; padding: 8px 12px; margin-bottom: 0;
    }
    #lq-map-header > span:first-child { font-weight: 600; color: #14532d; }
    .lq-status-text { font-size: 0.82em; color: #6b7280; margin-left: auto; }
    .lq-view-link { font-size: 0.82em; color: #1a6b3c; text-decoration: none; white-space: nowrap; }
    .lq-view-link:hover { text-decoration: underline; }
    #lq-map-el {
      height: 400px; border: 1px solid #d1d5db; border-radius: 0 0 8px 8px;
      overflow: hidden; background: #f0fdf4; position: relative;
    }
    #lq-loading {
      position: absolute; inset: 0; display: flex; flex-direction: column;
      align-items: center; justify-content: center; gap: 12px;
      background: #f0fdf4; z-index: 10; color: #4b7a5e; font-size: 0.88em;
    }
    .lq-spinner {
      width: 32px; height: 32px; border: 3px solid #bbf7d0;
      border-top-color: #1a6b3c; border-radius: 50%;
      animation: lq-spin 0.8s linear infinite;
    }
    @keyframes lq-spin { to { transform: rotate(360deg); } }
    .lq-error-text { color: #dc2626; font-size: 0.88em; text-align: center; padding: 0 16px; }
    #lq-cat-bar { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 2px 0; }
    .lq-cat-btn {
      display: inline-flex; align-items: center; gap: 4px;
      background: var(--lq-cat-color, #6b7280); color: #fff; border: none;
      border-radius: 999px; padding: 3px 10px; font-size: 0.78em;
      cursor: pointer; transition: opacity 0.15s; font-family: inherit;
    }
    .lq-cat-btn:hover { opacity: 0.85; }
    .lq-cat-btn--off { opacity: 0.35; background: #9ca3af !important; color: #fff; }
    #lq-map-el .leaflet-top, #lq-map-el .leaflet-bottom { z-index: 1000; }
  `);

  // ── Config (backend URL stored via GM_getValue/GM_setValue) ───────────────
  function getBackendUrl() {
    return GM_getValue('backendUrl', DEFAULT_BACKEND);
  }

  // ── Listing ID + coord extraction ─────────────────────────────────────────
  function extractListingId() {
    const m = window.location.pathname.match(/\/homedetails\/(.+?)\/?$/);
    return m ? m[1] : null;
  }

  function isValidCoord(lat, lon) {
    return typeof lat === 'number' && typeof lon === 'number'
      && Math.abs(lat) <= 90 && Math.abs(lon) <= 180
      && (lat !== 0 || lon !== 0);
  }

  function findCoordsRecursive(obj, depth) {
    if (depth > 15 || !obj || typeof obj !== 'object') return null;
    if (Array.isArray(obj)) {
      for (const v of obj) { const r = findCoordsRecursive(v, depth + 1); if (r) return r; }
      return null;
    }
    const lat = obj.latitude ?? obj.lat;
    const lon = obj.longitude ?? obj.lng ?? obj.lon;
    if (isValidCoord(lat, lon)) return { lat: +lat, lon: +lon };
    for (const v of Object.values(obj)) { const r = findCoordsRecursive(v, depth + 1); if (r) return r; }
    return null;
  }

  function extractCoordinates() {
    const ndEl = document.getElementById('__NEXT_DATA__');
    if (ndEl) { try { const r = findCoordsRecursive(JSON.parse(ndEl.textContent), 0); if (r) return r; } catch {} }

    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const d = JSON.parse(s.textContent);
        const nodes = Array.isArray(d['@graph']) ? d['@graph'] : [d];
        for (const node of nodes) {
          const lat = node.geo?.latitude, lon = node.geo?.longitude;
          if (isValidCoord(+lat, +lon)) return { lat: +lat, lon: +lon };
        }
      } catch {}
    }

    const re = /"(?:latitude|lat)"\s*:\s*(-?\d{1,3}\.\d{4,})[^]*?"(?:longitude|lng|lon)"\s*:\s*(-?\d{1,3}\.\d{4,})/;
    for (const s of document.querySelectorAll('script:not([src])')) {
      const text = s.textContent || '';
      if (!text.includes('latitude') && !text.includes('"lat"')) continue;
      const m = re.exec(text);
      if (m) { const lat = parseFloat(m[1]), lon = parseFloat(m[2]); if (isValidCoord(lat, lon)) return { lat, lon }; }
    }
    return null;
  }

  // ── Anchor detection ──────────────────────────────────────────────────────
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
      const timer = setTimeout(() => { obs.disconnect(); resolve(null); }, timeoutMs);
      const obs = new MutationObserver(() => {
        const el = findAnchor();
        if (el) { clearTimeout(timer); obs.disconnect(); resolve(el); }
      });
      obs.observe(document.body, { childList: true, subtree: true });
    });
  }

  // ── DOM injection ─────────────────────────────────────────────────────────
  function injectMapContainer(anchor, listing_id, backendBase) {
    if (document.getElementById(LQ_ROOT_ID)) return;
    const wrapper = document.createElement('div');
    wrapper.id = LQ_ROOT_ID;
    const viewUrl = `${backendBase}/zillow/${listing_id}`;
    wrapper.innerHTML = `
      <div id="lq-map-header">
        <span>🏘 Nearby Places (Le Quartier)</span>
        <span id="${LQ_STATUS_ID}" class="lq-status-text">Loading…</span>
        <a href="${viewUrl}" target="_blank" rel="noopener" class="lq-view-link">View full map ↗</a>
      </div>
      <div id="lq-map-el">
        <div id="lq-loading"><div class="lq-spinner"></div><span>Querying nearby places…</span></div>
      </div>
      <div id="lq-cat-bar"></div>
    `;
    const section = anchor.closest('section, article, [data-testid]') || anchor.parentElement;
    (section?.parentElement ? section : anchor).after(wrapper);
  }

  // ── Map rendering (inline equivalent of shared/map-init.js) ───────────────
  function renderMap(centerLat, centerLon, geojson) {
    const mapEl = document.getElementById('lq-map-el');
    if (!mapEl || typeof L === 'undefined') return;

    const map = L.map('lq-map-el', { zoomControl: true }).setView([centerLat, centerLon], 15);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);

    const ACCENT = '#5b8dd9';
    [
      { r: 1200, fillOpacity: 0.05, label: '15min' },
      { r: 800,  fillOpacity: 0.09, label: '10min' },
      { r: 400,  fillOpacity: 0.15, label: '5min'  },
    ].forEach(c => {
      L.circle([centerLat, centerLon], {
        radius: c.r, color: ACCENT, weight: 1.5, opacity: 0.55,
        fillColor: ACCENT, fillOpacity: c.fillOpacity, interactive: false,
      }).addTo(map);
      L.marker([centerLat + c.r / 111320, centerLon], {
        icon: L.divIcon({
          html: '<div style="transform:translateX(-50%);display:inline-flex;align-items:center;gap:4px;background:rgba(255,255,255,0.88);border:1px solid #5b8dd9;border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;color:#5b8dd9;white-space:nowrap;backdrop-filter:blur(2px)"><i class="fa-solid fa-person-walking" style="font-size:10px"></i>' + c.label + '</div>',
          className: '', iconSize: [0, 0], iconAnchor: [0, 0],
        }),
        interactive: false,
      }).addTo(map);
    });

    L.marker([centerLat, centerLon], {
      icon: L.divIcon({
        html: '<span class="fa-stack" style="font-size:16px;filter:drop-shadow(0 1px 2px rgba(0,0,0,.5))">'
            + '<i class="fa-solid fa-circle fa-stack-2x" style="color:#b33f43"></i>'
            + '<i class="fa-solid fa-house fa-stack-1x" style="color:#CCC"></i></span>',
        className: '', iconSize: [32, 32], iconAnchor: [16, 16],
      }),
      zIndexOffset: 1000,
    }).bindPopup('<strong>This property</strong>').addTo(map);

    const PALETTE = ['#16a34a','#2563eb','#f97316','#9333ea','#dc2626',
                     '#0891b2','#ca8a04','#be185d','#15803d','#1d4ed8'];
    const catMeta   = (geojson._meta && geojson._meta.category_meta) || {};
    const catColors = {};
    let   palIdx    = 0;

    Object.entries(catMeta).forEach(([label, m]) => {
      catColors[label] = m.color || PALETTE[palIdx++ % PALETTE.length];
    });

    function colorFor(cat) {
      if (!catColors[cat]) catColors[cat] = PALETTE[palIdx++ % PALETTE.length];
      return catColors[cat];
    }

    function darken(hex, f) {
      f = f || 0.75;
      const r = Math.round(parseInt(hex.slice(1,3),16)*f);
      const g = Math.round(parseInt(hex.slice(3,5),16)*f);
      const b = Math.round(parseInt(hex.slice(5,7),16)*f);
      return '#' + [r,g,b].map(v => v.toString(16).padStart(2,'0')).join('');
    }

    const layerByCat = {};
    (geojson.features || []).forEach(feature => {
      const coords = feature.geometry && feature.geometry.coordinates;
      if (!coords) return;
      const [flon, flat] = coords;
      const p   = feature.properties || {};
      const cat = p.category || 'Other';
      if (!layerByCat[cat]) layerByCat[cat] = L.layerGroup().addTo(map);
      const c = colorFor(cat);
      const icon = L.divIcon({
        html: `<span style="display:block;width:12px;height:12px;border-radius:50%;background:${c};border:2px solid ${darken(c)};box-shadow:0 1px 3px rgba(0,0,0,.4);"></span>`,
        className: '', iconSize: [14, 14], iconAnchor: [7, 7],
      });
      const lines = [
        `<strong>${p.name || '?'}</strong>`,
        `<span style="color:#6b7280;font-size:0.82em;">${p.icon || ''} ${cat}</span>`,
        p.rating ? `<i class="fa-solid fa-star" style="color:#f59e0b"></i> ${p.rating.toFixed(1)}${p.user_rating_count ? ` (${p.user_rating_count})` : ''}` : '',
        (p.opening_hours && p.opening_hours.raw) ? `🕐 ${p.opening_hours.raw}` : '',
        p.website ? `<a href="${p.website}" target="_blank" rel="noopener" style="font-size:0.8em;">Website ↗</a>` : '',
      ].filter(Boolean).join('<br>');
      L.marker([flat, flon], { icon }).bindPopup(lines).addTo(layerByCat[cat]);
    });

    const catBar = document.getElementById('lq-cat-bar');
    if (catBar) {
      Object.entries(layerByCat).forEach(([cat, layer]) => {
        const count  = (geojson.features || []).filter(f => (f.properties || {}).category === cat).length;
        const faIcon = (catMeta[cat] && catMeta[cat].fa_icon) || 'fa-location-dot';
        const color  = colorFor(cat);
        const btn    = document.createElement('button');
        btn.className      = 'lq-cat-btn';
        btn.dataset.active = 'true';
        btn.style.setProperty('--lq-cat-color', color);
        btn.innerHTML = `<i class="fa-solid ${faIcon}"></i> ${cat} (${count})`;
        btn.addEventListener('click', () => {
          const active = btn.dataset.active === 'true';
          btn.dataset.active = active ? 'false' : 'true';
          btn.classList.toggle('lq-cat-btn--off', active);
          if (active) map.removeLayer(layer); else map.addLayer(layer);
        });
        catBar.appendChild(btn);
      });
    }
  }

  // ── Main ──────────────────────────────────────────────────────────────────
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

    const anchor = await waitForAnchor();
    if (!anchor) {
      console.warn('[LeQuartier] Neighborhood section not found within timeout.');
      return;
    }

    const backendBase = getBackendUrl();
    injectMapContainer(anchor, listing_id, backendBase);

    const statusEl = document.getElementById(LQ_STATUS_ID);
    if (statusEl) statusEl.textContent = 'Fetching POIs…';

    try {
      const url = new URL('/api/nearby', backendBase);
      url.searchParams.set('lat', coords.lat);
      url.searchParams.set('lon', coords.lon);
      url.searchParams.set('zillow_id', listing_id);

      const resp = await fetch(url.toString());
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const geojson = await resp.json();

      const n = geojson?.features?.length ?? 0;
      if (statusEl) statusEl.textContent = `${n} place${n !== 1 ? 's' : ''}`;

      document.getElementById('lq-loading')?.remove();
      renderMap(coords.lat, coords.lon, geojson);
    } catch (err) {
      const loadingEl = document.getElementById('lq-loading');
      if (loadingEl) loadingEl.innerHTML = `<span class="lq-error-text">⚠ ${err.message}</span>`;
      if (statusEl) { statusEl.textContent = 'Error'; statusEl.style.color = '#dc2626'; }
    }
  }

  // ── SPA navigation ────────────────────────────────────────────────────────
  function onNavigate() {
    document.getElementById(LQ_ROOT_ID)?.remove();
    delete window.__lqRan;
    setTimeout(run, 800);
  }

  const _origPush    = history.pushState.bind(history);
  const _origReplace = history.replaceState.bind(history);
  history.pushState    = (...args) => { _origPush(...args);    onNavigate(); };
  history.replaceState = (...args) => { _origReplace(...args); onNavigate(); };
  window.addEventListener('popstate', onNavigate);

  run();
})();
