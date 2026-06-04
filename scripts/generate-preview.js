#!/usr/bin/env node
/**
 * scripts/generate-preview.js
 *
 * Generates a 1200×630 JPEG map preview for an Airbnb listing.
 * Screenshots /airbnb/<listing_id>/edit?embed=1 using Playwright.
 *
 * Usage:
 *   node scripts/generate-preview.js <listing_id>
 *
 * Environment:
 *   PREVIEW_BASE_URL  Base URL of the running Flask app (default: http://127.0.0.1:5010)
 *
 * Output:
 *   src/web/static/img/previews/<listing_id>.jpg
 */

const { chromium } = require('playwright');
const path = require('path');
const fs   = require('fs');

const listingId = process.argv[2];
if (!listingId) {
  console.error('Usage: node generate-preview.js <listing_id>');
  process.exit(1);
}

const BASE_URL  = process.env.PREVIEW_BASE_URL || 'http://127.0.0.1:5010';
const OUT_DIR   = path.resolve(__dirname, '..', 'src', 'web', 'static', 'img', 'previews');
const OG_WIDTH  = 1200;
const OG_HEIGHT = 630;
const TILE_WAIT = 3000;

fs.mkdirSync(OUT_DIR, { recursive: true });

const url     = `${BASE_URL}/airbnb/${listingId}/edit?embed=1`;
const outFile = path.join(OUT_DIR, `${listingId}.jpg`);

console.log(`Generating preview for ${listingId}`);
console.log(`  URL : ${url}`);
console.log(`  OUT : ${outFile}`);

(async () => {
  const browser = await chromium.launch();
  const page    = await browser.newPage();

  try {
    await page.setViewportSize({ width: OG_WIDTH, height: OG_HEIGHT });

    await page.goto(url, { waitUntil: 'networkidle', timeout: 45_000 });

    await page.waitForTimeout(TILE_WAIT);

    try {
      await page.waitForSelector('.leaflet-tile-loaded', { timeout: 8_000 });
      await page.waitForTimeout(1_000);
    } catch {
      console.log('  No Leaflet tiles detected — screenshotting as-is');
    }

    await page.screenshot({ path: outFile, type: 'jpeg', quality: 90 });
    console.log('  Saved');

  } catch (err) {
    console.error(`  FAILED: ${err.message}`);
    await browser.close();
    process.exit(1);
  }

  await browser.close();
})();
