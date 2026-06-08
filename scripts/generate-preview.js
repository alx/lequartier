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

    // Inject "🌳🏠 Le Quartier" title overlay
    await page.evaluate(() => {
      const container = document.createElement('div');
      container.style.cssText = `
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
        pointer-events: none;
        font-family: system-ui, -apple-system, sans-serif;
      `;

      const titleCard = document.createElement('div');
      titleCard.style.cssText = `
        background: rgba(255, 255, 255, 0.9);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        padding: 20px 45px;
        border-radius: 100px;
        box-shadow: 0 12px 40px rgba(0,0,0,0.18);
        border: 1px solid rgba(255,255,255,0.4);
        display: flex;
        align-items: center;
        gap: 16px;
      `;

      titleCard.innerHTML = `
        <span style="font-size: 52px; line-height: 1;">🌳🏠</span>
        <h1 style="
          margin: 0;
          font-size: 72px;
          font-weight: 800;
          color: #1a6b3c;
          letter-spacing: -0.04em;
          line-height: 1;
        ">Le Quartier</h1>
      `;

      container.appendChild(titleCard);
      document.body.appendChild(container);
    });

    await page.screenshot({ path: outFile, type: 'jpeg', quality: 90 });
    console.log('  Saved');

  } catch (err) {
    console.error(`  FAILED: ${err.message}`);
    await browser.close();
    process.exit(1);
  }

  await browser.close();
})();
