(function () {
  'use strict';

  const dataEl = document.getElementById('lq-geojson-data');
  if (!dataEl) return;

  let payload;
  try { payload = JSON.parse(dataEl.textContent); } catch { return; }

  const { lat: centerLat, lon: centerLon, geojson } = payload;
  const mapEl = document.getElementById('lq-map-el');
  if (!mapEl || typeof L === 'undefined') return;

  // ── Base map ─────────────────────────────────────────────────────────────
  const map = L.map('lq-map-el', { zoomControl: true }).setView([centerLat, centerLon], 15);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  // ── Walking-distance rings ────────────────────────────────────────────────
  const ACCENT = '#5b8dd9';
  [
    { r: 1200, fillOpacity: 0.05 },
    { r: 800,  fillOpacity: 0.09 },
    { r: 400,  fillOpacity: 0.15 },
  ].forEach(c => {
    L.circle([centerLat, centerLon], {
      radius: c.r, color: ACCENT, weight: 1.5, opacity: 0.55,
      fillColor: ACCENT, fillOpacity: c.fillOpacity, interactive: false,
    }).addTo(map);
  });

  // ── Home marker ───────────────────────────────────────────────────────────
  const homeIcon = L.divIcon({
    html: '<div style="font-size:20px;line-height:1;filter:drop-shadow(0 1px 2px rgba(0,0,0,.5))">🏠</div>',
    className: '', iconSize: [24, 24], iconAnchor: [12, 12],
  });
  L.marker([centerLat, centerLon], { icon: homeIcon, zIndexOffset: 1000 })
   .bindPopup('<strong>This property</strong>')
   .addTo(map);

  // ── Category metadata ─────────────────────────────────────────────────────
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

  function makeDotIcon(cat) {
    const c = colorFor(cat);
    return L.divIcon({
      html: `<span style="display:block;width:12px;height:12px;border-radius:50%;`
          + `background:${c};border:2px solid ${darken(c)};`
          + `box-shadow:0 1px 3px rgba(0,0,0,.4);"></span>`,
      className: '', iconSize: [14, 14], iconAnchor: [7, 7],
    });
  }

  // ── POI markers ──────────────────────────────────────────────────────────
  const layerByCat = {};

  (geojson.features || []).forEach(feature => {
    const coords = feature.geometry && feature.geometry.coordinates;
    if (!coords) return;
    const [flon, flat] = coords;
    const p   = feature.properties || {};
    const cat = p.category || 'Other';

    if (!layerByCat[cat]) layerByCat[cat] = L.layerGroup().addTo(map);

    const lines = [
      `<strong>${p.name || '?'}</strong>`,
      `<span style="color:#6b7280;font-size:0.82em;">${p.icon || ''} ${cat}</span>`,
      p.rating ? `⭐ ${p.rating.toFixed(1)}${p.user_rating_count ? ` (${p.user_rating_count})` : ''}` : '',
      (p.opening_hours && p.opening_hours.raw) ? `🕐 ${p.opening_hours.raw}` : '',
      p.website ? `<a href="${p.website}" target="_blank" rel="noopener" style="font-size:0.8em;">Website ↗</a>` : '',
    ].filter(Boolean).join('<br>');

    L.marker([flat, flon], { icon: makeDotIcon(cat) })
     .bindPopup(lines)
     .addTo(layerByCat[cat]);
  });

  // ── Category filter bar ────────────────────────────────────────────────────
  const catBar = document.getElementById('lq-cat-bar');
  if (catBar) {
    Object.entries(layerByCat).forEach(([cat, layer]) => {
      const count = (geojson.features || []).filter(f => (f.properties || {}).category === cat).length;
      const icon  = (catMeta[cat] && catMeta[cat].icon) || '';
      const color = colorFor(cat);

      const btn = document.createElement('button');
      btn.className      = 'lq-cat-btn';
      btn.dataset.active = 'true';
      btn.style.setProperty('--lq-cat-color', color);
      btn.textContent    = `${icon} ${cat} (${count})`;

      btn.addEventListener('click', () => {
        const active = btn.dataset.active === 'true';
        btn.dataset.active = active ? 'false' : 'true';
        btn.classList.toggle('lq-cat-btn--off', active);
        if (active) map.removeLayer(layer); else map.addLayer(layer);
      });

      catBar.appendChild(btn);
    });
  }
})();
