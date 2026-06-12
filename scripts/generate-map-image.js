#!/usr/bin/env node
/**
 * scripts/generate-map-image.js
 *
 * Generates a 1200×800 PNG map export for a Host Map page (/p/<uuid>).
 * Screenshots /p/<uuid>?embed=1 using Playwright.
 *
 * Usage:
 *   node scripts/generate-map-image.js <uuid>
 *
 * Environment:
 *   PREVIEW_BASE_URL  Base URL of the running Flask app (default: http://127.0.0.1:5010)
 *
 * Output:
 *   src/web/static/img/maps/<uuid>_map.png
 */

const { chromium } = require('playwright');
const path = require('path');
const fs   = require('fs');

const mapUuid = process.argv[2];
if (!mapUuid) {
  console.error('Usage: node generate-map-image.js <uuid>');
  process.exit(1);
}

const BASE_URL  = process.env.PREVIEW_BASE_URL || 'http://127.0.0.1:5010';
const OUT_DIR   = path.resolve(__dirname, '..', 'src', 'web', 'static', 'img', 'maps');
const WIDTH     = 1200;
const HEIGHT    = 800;
const TILE_WAIT = 3000;

fs.mkdirSync(OUT_DIR, { recursive: true });

const url     = `${BASE_URL}/p/${mapUuid}?embed=1`;
const outFile = path.join(OUT_DIR, `${mapUuid}_map.png`);

console.log(`Generating map image for ${mapUuid}`);
console.log(`  URL : ${url}`);
console.log(`  OUT : ${outFile}`);

(async () => {
  const browser = await chromium.launch();
  const page    = await browser.newPage();

  try {
    await page.setViewportSize({ width: WIDTH, height: HEIGHT });
    await page.goto(url, { waitUntil: 'networkidle', timeout: 45_000 });

    try {
      await page.waitForSelector('.map-active', { timeout: 15_000 });
    } catch {
      console.log('  No .map-active — screenshotting as-is');
    }

    await page.waitForTimeout(TILE_WAIT);

    try {
      await page.waitForSelector('.leaflet-tile-loaded', { timeout: 8_000 });
      await page.waitForTimeout(1_000);
    } catch {
      console.log('  No Leaflet tiles detected — screenshotting as-is');
    }

    await page.screenshot({ path: outFile, type: 'png' });
    console.log('  Saved');

  } catch (err) {
    console.error(`  FAILED: ${err.message}`);
    await browser.close();
    process.exit(1);
  }

  await browser.close();
})();
