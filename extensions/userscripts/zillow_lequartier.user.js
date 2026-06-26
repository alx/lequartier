// ==UserScript==
// @name         Le Quartier – Zillow Map
// @namespace    https://girard-davila.net
// @version      3.0.0
// @description  Embeds a neighbourhood POI map on Zillow property listings
// @author       Alexandre Girard-Davila
// @match        https://www.zillow.com/homedetails/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_getResourceText
// @grant        GM_addStyle
// @resource     leafletJS   https://unpkg.com/leaflet@1.9.4/dist/leaflet.js
// @resource     leafletCSS  https://unpkg.com/leaflet@1.9.4/dist/leaflet.css
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const DEFAULT_BACKEND = 'https://lequartier.girard-davila.net';
  const LQ_ROOT_ID   = 'lq-map-root';

  // ── Load Leaflet (strip sourceMappingURL comment to avoid DevTools 404) ──
  // (0, eval) is an indirect eval that runs in the global scope, same as @require
  ;(0, eval)(GM_getResourceText('leafletJS').replace(/\/\/# sourceMappingURL=\S+[ \t]*$/m, ''));

  // ── Inject CSS assets ────────────────────────────────────────────────────
  GM_addStyle(GM_getResourceText('leafletCSS'));

  const faLink = document.createElement('link');
  faLink.rel = 'stylesheet';
  faLink.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css';
  document.head.appendChild(faLink);

  GM_addStyle(`
    #lq-map-root {
      font-family: system-ui, -apple-system, sans-serif;
      font-size: 14px;
      line-height: 1.4;
      overflow: hidden;
    }
    .lq-status-text { font-size: 0.82em; color: #6b7280; margin-left: auto; }
    .lq-view-link { font-size: 0.82em; color: #1a6b3c; text-decoration: none; white-space: nowrap; }
    .lq-view-link:hover { text-decoration: underline; }
    #lq-map-el {
      height: 400px; border-top: 1px solid #d1d5db;
      overflow: hidden; background: #f0fdf4; position: relative; margin-top: 5px;
    }
    #lq-loading {
      position: absolute; inset: 0; display: flex; flex-direction: column;
      align-items: center; justify-content: center; gap: 10px;
      background: #f0fdf4; z-index: 10; color: #4b7a5e; font-size: 0.88em;
      padding: 16px;
    }
    .lq-spinner {
      width: 32px; height: 32px; border: 3px solid #bbf7d0;
      border-top-color: #1a6b3c; border-radius: 50%;
      animation: lq-spin 0.8s linear infinite;
    }
    @keyframes lq-spin { to { transform: rotate(360deg); } }
    .lq-error-text { color: #dc2626; font-size: 0.88em; text-align: center; padding: 0 16px; }
    .lq-progress-outer {
      width: 220px; height: 6px; background: #e5e7eb;
      border-radius: 99px; overflow: hidden;
    }
    .lq-progress-inner {
      height: 100%; background: #1a6b3c; border-radius: 99px;
      transition: width 0.4s ease;
      background-image: linear-gradient(
        45deg, rgba(255,255,255,.18) 25%, transparent 25%, transparent 50%,
        rgba(255,255,255,.18) 50%, rgba(255,255,255,.18) 75%, transparent 75%, transparent
      );
      background-size: 1rem 1rem;
      animation: lq-progress-stripes 1s linear infinite;
    }
    @keyframes lq-progress-stripes { from { background-position: 1rem 0; } to { background-position: 0 0; } }
    .lq-activity-log {
      font-size: 0.72em; color: #6b7280; font-family: monospace;
      margin-top: 2px; text-align: left; max-height: 4.5em; overflow: hidden; width: 220px;
    }
    #lq-cat-bar { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 12px 12px; }
    .lq-cat-btn {
      display: inline-flex; align-items: center; gap: 4px;
      background: var(--lq-cat-color, #6b7280); color: #fff; border: none;
      border-radius: 999px; padding: 3px 10px; font-size: 0.78em;
      cursor: pointer; transition: opacity 0.15s; font-family: inherit;
      text-decoration: none;
    }
    .lq-cat-btn:hover { opacity: 0.85; }
    .lq-cat-btn--off { background: #e5e7eb !important; color: #374151 !important; }
    #lq-map-el .leaflet-top, #lq-map-el .leaflet-bottom { z-index: 1000; }
    #lq-title-link button {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 6px 10px; background: #f0fdf4; border: 1px solid #d1fae5;
      border-radius: 999px; cursor: pointer; font-size: 13px; font-weight: 600;
      color: #1a6b3c; white-space: nowrap;
    }
    #lq-title-link button:hover { background: #dcfce7; }
  `);

  // ── Config ────────────────────────────────────────────────────────────────
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

  // ── Sidebar container detection ───────────────────────────────────────────
  function findSidebarContainer() {
    const upsell = document.getElementById('upsell-container');
    if (upsell?.parentElement) return upsell.parentElement;
    const claim = document.getElementById('claim-cta');
    if (claim?.parentElement) return claim.parentElement;
    return document.querySelector('.nfs-d_flex.nfs-flex-d_column.nfs-jc_center');
  }

  function waitForSidebarContainer(timeoutMs = 15000) {
    return new Promise(resolve => {
      const found = findSidebarContainer();
      if (found) { resolve(found); return; }
      const timer = setTimeout(() => { obs.disconnect(); resolve(null); }, timeoutMs);
      const obs = new MutationObserver(() => {
        const el = findSidebarContainer();
        if (el) { clearTimeout(timer); obs.disconnect(); resolve(el); }
      });
      obs.observe(document.body, { childList: true, subtree: true });
    });
  }

  // ── DOM injection ─────────────────────────────────────────────────────────
  let mapWrapper = null;

  function ensureMapInSidebar(container) {
    if (!mapWrapper) {
      mapWrapper = document.createElement('div');
      mapWrapper.id = LQ_ROOT_ID;
      mapWrapper.innerHTML = `
        <div id="lq-cat-bar"></div>
        <div id="lq-map-el">
          <div id="lq-loading"><div class="lq-spinner"></div><span>Querying nearby places…</span></div>
        </div>
      `;

      const refCard = document.getElementById('upsell-container') || document.getElementById('claim-cta');
      if (refCard) {
        const s = window.getComputedStyle(refCard);
        mapWrapper.style.border = s.border;
        mapWrapper.style.borderRadius = s.borderRadius;
        mapWrapper.style.marginTop = s.marginTop;
      } else {
        mapWrapper.style.cssText += 'border:1px solid #d1d5db;border-radius:8px;margin-top:16px;';
      }
    }
    if (!container.contains(mapWrapper)) {
      container.insertBefore(mapWrapper, container.firstChild);
    }
  }

  // ── Title link ────────────────────────────────────────────────────────────
  function findSaveButton() {
    const byTestId = document.querySelector('[data-testid*="save"i], [data-testid*="favorite"i]');
    if (byTestId) return byTestId.parentElement || byTestId;
    const byAria = document.querySelector('[aria-label*="Save"i], [aria-label*="Favorite"i]');
    if (byAria) return byAria.parentElement || byAria;
    for (const btn of document.querySelectorAll('button')) {
      const text = (btn.innerText || btn.textContent || '').trim();
      if (/^save$/i.test(text)) return btn.parentElement || btn;
    }
    return document.querySelector('h1');
  }

  function waitForSaveButton(timeoutMs = 10000) {
    return new Promise(resolve => {
      const found = findSaveButton();
      if (found) { resolve(found); return; }
      const timer = setTimeout(() => { obs.disconnect(); resolve(null); }, timeoutMs);
      const obs = new MutationObserver(() => {
        const el = findSaveButton();
        if (el) { clearTimeout(timer); obs.disconnect(); resolve(el); }
      });
      obs.observe(document.body, { childList: true, subtree: true });
    });
  }

  async function injectTitleLink() {
    if (document.getElementById('lq-title-link')) return;
    const listing_id = extractListingId();
    if (!listing_id) return;

    const anchor = await waitForSaveButton();
    if (!anchor || document.getElementById('lq-title-link')) return;

    const externalUrl = `${getBackendUrl()}/zillow/${listing_id}`;

    const wrapper = document.createElement('div');
    wrapper.id = 'lq-title-link';
    wrapper.style.cssText = 'display:inline-flex;align-items:center;';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.title = 'Le Quartier';
    btn.innerHTML = '<span aria-hidden="true">🌳🏠</span><span>Le Quartier</span>';
    btn.addEventListener('click', () => {
      const mapEl = document.getElementById(LQ_ROOT_ID);
      if (mapEl) mapEl.scrollIntoView({ behavior: 'smooth' });
      else window.open(externalUrl, '_blank', 'noopener,noreferrer');
    });

    wrapper.appendChild(btn);
    anchor.insertAdjacentElement('beforebegin', wrapper);
  }

  // ── URL validation helper ─────────────────────────────────────────────────
  function buildValidatedUrl(baseUrl, taskId) {
    try {
      // Minimal path validation
      if (baseUrl.includes('/../') || /\/%2e%2e\//i.test(baseUrl)) {
        throw new Error('Invalid path');
      }
      
      const url = new URL(baseUrl);
      
      // Protocol + host checks
      const allowedDomains = ['lequartier.girard-davila.net'];
      if (!allowedDomains.includes(url.hostname)) {
        throw new Error('Invalid host');
      }
      if (!['http:', 'https:'].includes(url.protocol)) {
        throw new Error('Invalid protocol');
      }
      
      // Validate path parameters
      if (!/^[A-Za-z0-9_-]+$/.test(taskId)) {
        throw new Error('Invalid parameter');
      }
      
      // Rebuild pathname from fixed literals + validated segments
      url.pathname = `/tasks/${taskId}/map-state`;
      
      return url.href;
    } catch {
      throw new Error('Invalid URL');
    }
  }

  // ── Generation with live progress ─────────────────────────────────────────
  async function generateAndPoll(listing_id, lat, lon, backendBase) {
    const loadingEl = document.getElementById('lq-loading');
    if (loadingEl) {
      loadingEl.innerHTML = `
        <div class="lq-progress-outer"><div class="lq-progress-inner" id="lq-prog-inner" style="width:5%"></div></div>
        <div class="lq-activity-log" id="lq-activity-log"></div>
      `;
    }

    let taskId;
    try {
      const resp = await fetch(`${backendBase}/api/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ site: 'zillow', listing_id, lat, lon }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      taskId = (await resp.json()).task_id;
    } catch (err) {
      if (loadingEl) loadingEl.innerHTML = `<span class="lq-error-text">⚠ ${err.message}</span>`;
      return null;
    }

    return taskId;
  }

  async function pollUntilDone(taskId, backendBase) {
    let lastMsg = '';
    let polling = true;

    const autoIncr = setInterval(() => {
      const inner = document.getElementById('lq-prog-inner');
      if (!inner || !polling) { clearInterval(autoIncr); return; }
      const cur = parseFloat(inner.style.width) || 5;
      if (cur < 88) inner.style.width = (cur + 1.5) + '%';
    }, 3000);

    while (polling) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const r = await fetch(buildValidatedUrl(backendBase, taskId)).then(res => res.json());

        const inner = document.getElementById('lq-prog-inner');
        if (inner) inner.style.width = (r.progress_pct || 5) + '%';

        if (r.progress && r.progress !== lastMsg) {
          lastMsg = r.progress;
          const log = document.getElementById('lq-activity-log');
          if (log) {
            const line = document.createElement('div');
            line.textContent = r.progress;
            log.insertBefore(line, log.firstChild);
            while (log.children.length > 5) log.removeChild(log.lastChild);
          }
        }

        if (r.error) {
          polling = false;
          clearInterval(autoIncr);
          const loadingEl = document.getElementById('lq-loading');
          if (loadingEl) loadingEl.innerHTML = `<span class="lq-error-text">⚠ ${r.error}</span>`;
          return false;
        }

        if (r.done) {
          polling = false;
          clearInterval(autoIncr);
          return true;
        }
      } catch (_) {}
    }
    return false;
  }

  // ── Map rendering (inline equivalent of shared/map-init.js) ───────────────
  function renderMap(centerLat, centerLon, geojson, listing_id) {
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
      const DEFAULT_VISIBLE = ['Supermarket', 'Bakery & Food', 'Market'];
      Object.entries(layerByCat).forEach(([cat, layer]) => {
        const count = (geojson.features || []).filter(f => (f.properties || {}).category === cat).length;
        const color = colorFor(cat);
        const isActive = DEFAULT_VISIBLE.includes(cat);
        const btn = document.createElement('button');
        btn.className = 'lq-cat-btn';
        if (!isActive) {
          btn.classList.add('lq-cat-btn--off');
          map.removeLayer(layer);
        }
        btn.dataset.active = isActive ? 'true' : 'false';
        btn.style.setProperty('--lq-cat-color', color);
        btn.textContent = `${cat} (${count})`;
        btn.addEventListener('click', () => {
          const active = btn.dataset.active === 'true';
          btn.dataset.active = active ? 'false' : 'true';
          btn.classList.toggle('lq-cat-btn--off', active);
          if (active) map.removeLayer(layer); else map.addLayer(layer);
        });
        catBar.appendChild(btn);
      });

      const openBtn = document.createElement('a');
      openBtn.className = 'lq-cat-btn';
      openBtn.href = `${getBackendUrl()}/zillow/${listing_id}`;
      openBtn.target = '_blank';
      openBtn.rel = 'noopener';
      openBtn.style.setProperty('--lq-cat-color', '#1a6b3c');
      openBtn.innerHTML = '<i class="fa-solid fa-map-marked-alt"></i> Open in Le Quartier';
      catBar.appendChild(openBtn);
    }
  }

  // ── Main ──────────────────────────────────────────────────────────────────
  async function run() {
    if (window.__lqRan) return;
    window.__lqRan = true;

    const listing_id = extractListingId();
    if (!listing_id) return;

    injectTitleLink();

    const coords = extractCoordinates();
    if (!coords) {
      console.warn('[LeQuartier] Could not extract coordinates from this Zillow page.');
      return;
    }

    const backendBase = getBackendUrl();

    const container = await waitForSidebarContainer();
    if (!container) {
      console.warn('[LeQuartier] Sidebar container not found within timeout.');
      return;
    }

    ensureMapInSidebar(container);

    new MutationObserver(() => {
      const c = findSidebarContainer();
      if (c) ensureMapInSidebar(c);
    }).observe(document.body, { childList: true, subtree: true });

    try {
      const url = `${backendBase}/zillow/${listing_id}.geojson`;
      const resp = await fetch(url);

      if (resp.status === 404) {
        const taskId = await generateAndPoll(listing_id, coords.lat, coords.lon, backendBase);
        if (!taskId) return;
        const ok = await pollUntilDone(taskId, backendBase);
        if (!ok) return;
        const geojsonResp = await fetch(url);
        if (!geojsonResp.ok) throw new Error(`HTTP ${geojsonResp.status}`);
        const geojson = await geojsonResp.json();
        document.getElementById('lq-loading')?.remove();
        renderMap(coords.lat, coords.lon, geojson, listing_id);
        return;
      }

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const geojson = await resp.json();
      document.getElementById('lq-loading')?.remove();
      renderMap(coords.lat, coords.lon, geojson, listing_id);
    } catch (err) {
      const loadingEl = document.getElementById('lq-loading');
      if (loadingEl) loadingEl.innerHTML = `<span class="lq-error-text">⚠ ${err.message}</span>`;
    }
  }

  // ── SPA navigation ────────────────────────────────────────────────────────
  let _activePath = window.location.pathname;

  function onNavigate() {
    const newPath = window.location.pathname;
    if (newPath === _activePath) return;
    _activePath = newPath;
    document.getElementById(LQ_ROOT_ID)?.remove();
    mapWrapper = null;
    document.getElementById('lq-title-link')?.remove();
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
