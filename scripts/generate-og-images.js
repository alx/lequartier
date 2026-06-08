#!/usr/bin/env node
/**
 * scripts/generate-og-images.js
 *
 * Generates 1200×630 PNG OG images for curated Airbnb/Zillow listings.
 *
 * Layout:
 *   Left  588px — listing cover photo (platform logo if unavailable)
 *   Center 24px — white stripe
 *   Right 588px — Leaflet map with "primary" POIs only
 *
 * Usage:
 *   node scripts/generate-og-images.js [listing_id ...]
 *   (no args = all files in src/web/curated/)
 *
 * Output: src/web/static/img/og/<listing_id>.png
 */

const { chromium } = require('playwright');
const path = require('path');
const fs   = require('fs');
const os   = require('os');

const CURATED_DIR = path.resolve(__dirname, '..', 'src', 'web', 'curated');
const OUT_DIR     = path.resolve(__dirname, '..', 'src', 'web', 'static', 'img', 'og');
const OG_W        = 1200;
const OG_H        = 630;
const PANEL_W     = 588;   // (1200 - 24) / 2
const STRIPE_W    = 24;
const TILE_EXTRA  = 3000;  // ms after leaflet-tile-loaded to allow full render

const AIRBNB_LOGO = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 60"><rect width="200" height="60" fill="#FF5A5F" rx="6"/><text x="100" y="42" font-family="Arial,Helvetica,sans-serif" font-size="28" font-weight="700" fill="#fff" text-anchor="middle">airbnb</text></svg>`;
const ZILLOW_LOGO = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 60"><rect width="200" height="60" fill="#1277E1" rx="6"/><text x="100" y="42" font-family="Arial,Helvetica,sans-serif" font-size="28" font-weight="700" fill="#fff" text-anchor="middle">zillow</text></svg>`;

function buildHtml(data) {
  const r          = data.result;
  const lat        = r.lat;
  const lon        = r.lon;
  const photoUrl   = r.listing_photo || '';
  const logoSvg    = r.airbnb_url ? AIRBNB_LOGO : ZILLOW_LOGO;

  const primaryPois = (r.geojson?.features || [])
    .filter(f => f.properties?.status === 'primary')
    .map(f => f.geometry.coordinates); // [lon, lat]

  const imgHtml      = photoUrl
    ? `<img id="cover-img" src="${photoUrl}"
         onerror="this.style.display='none';document.getElementById('fallback').style.display='flex';"
         style="width:100%;height:100%;object-fit:cover;display:block;">`
    : '';
  const fallbackDisp = photoUrl ? 'none' : 'flex';

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:${OG_W}px;height:${OG_H}px;overflow:hidden;background:#fff}
#layout{display:flex;width:${OG_W}px;height:${OG_H}px}
#photo{width:${PANEL_W}px;height:${OG_H}px;flex-shrink:0;overflow:hidden;background:#f3f4f6;position:relative}
#fallback{position:absolute;inset:0;display:${fallbackDisp};align-items:center;justify-content:center}
#sep{width:${STRIPE_W}px;height:${OG_H}px;background:#fff;flex-shrink:0}
#map{width:${PANEL_W}px;height:${OG_H}px;flex-shrink:0}
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="">
</head>
<body>
<div id="layout">
  <div id="photo">
    ${imgHtml}
    <div id="fallback">${logoSvg}</div>
  </div>
  <div id="sep"></div>
  <div id="map"></div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
<script>
var CENTER=[${lat},${lon}];
var POIS=${JSON.stringify(primaryPois)};

var map=L.map('map',{
  zoomControl:false,attributionControl:false,
  dragging:false,scrollWheelZoom:false,
  doubleClickZoom:false,touchZoom:false,keyboard:false
});
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);

var homeIcon=L.divIcon({
  html:'<div style="width:14px;height:14px;background:#b33f43;border-radius:50%;border:2.5px solid #fff;box-shadow:0 2px 5px rgba(0,0,0,.45)"></div>',
  className:'',iconSize:[14,14],iconAnchor:[7,7]
});
L.marker(CENTER,{icon:homeIcon}).addTo(map);

var poiIcon=L.divIcon({
  html:'<div style="width:10px;height:10px;background:#2563eb;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.3)"></div>',
  className:'',iconSize:[10,10],iconAnchor:[5,5]
});
POIS.forEach(function(c){L.marker([c[1],c[0]],{icon:poiIcon}).addTo(map);});

var pts=[CENTER].concat(POIS.map(function(c){return[c[1],c[0]];}));
if(pts.length>1){
  map.fitBounds(L.latLngBounds(pts),{padding:[30,30]});
}else{
  map.setView(CENTER,15);
}
</script>
</body>
</html>`;
}

async function screenshotListing(id) {
  const jsonPath = path.join(CURATED_DIR, `${id}.json`);
  if (!fs.existsSync(jsonPath)) {
    throw new Error(`curated JSON not found: ${jsonPath}`);
  }

  let data;
  try {
    data = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  } catch (e) {
    throw new Error(`failed to parse JSON: ${e.message}`);
  }

  const primaryCount = (data.result?.geojson?.features || [])
    .filter(f => f.properties?.status === 'primary').length;
  if (!primaryCount) {
    console.warn(`  [${id}] no primary POIs — map will be centered at zoom 15`);
  }

  const html    = buildHtml(data);
  const tmpFile = path.join(os.tmpdir(), `og-${id}.html`);
  fs.writeFileSync(tmpFile, html, 'utf8');

  const browser = await chromium.launch();
  const page    = await browser.newPage();

  try {
    await page.setViewportSize({ width: OG_W, height: OG_H });
    await page.goto(`file://${tmpFile}`, { waitUntil: 'domcontentloaded', timeout: 30_000 });

    // Wait for cover image to finish loading or error out
    await page.waitForFunction(() => {
      const img = document.getElementById('cover-img');
      if (!img) return true;
      return img.complete || document.getElementById('fallback').style.display !== 'none';
    }, { timeout: 15_000 }).catch(() => {});

    // Wait for Leaflet tiles to render
    try {
      await page.waitForSelector('.leaflet-tile-loaded', { timeout: 10_000 });
      await page.waitForTimeout(TILE_EXTRA);
    } catch {
      console.warn(`  [${id}] no Leaflet tiles detected — screenshotting as-is`);
      await page.waitForTimeout(2_000);
    }

    const outFile = path.join(OUT_DIR, `${id}.png`);
    await page.screenshot({ path: outFile, type: 'png' });
    console.log(`  saved → ${path.relative(process.cwd(), outFile)}`);
  } finally {
    await browser.close();
    try { fs.unlinkSync(tmpFile); } catch {}
  }
}

(async () => {
  let ids = process.argv.slice(2);
  if (!ids.length) {
    ids = fs.readdirSync(CURATED_DIR)
      .filter(f => f.endsWith('.json'))
      .map(f => path.basename(f, '.json'));
  }

  if (!ids.length) {
    console.error('No curated listing IDs found.');
    process.exit(1);
  }

  fs.mkdirSync(OUT_DIR, { recursive: true });
  console.log(`Generating OG images for: ${ids.join(', ')}\n`);

  for (const id of ids) {
    console.log(`[${id}]`);
    try {
      await screenshotListing(id);
    } catch (err) {
      console.error(`  FAILED: ${err.message}`);
      process.exitCode = 1;
    }
  }
})();
