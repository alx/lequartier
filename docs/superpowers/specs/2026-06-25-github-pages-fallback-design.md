# GitHub Pages Fallback Page — Design Spec

**Date:** 2026-06-25
**Status:** Approved

## Overview

A standalone static HTML page deployed to a `gh-pages` branch of the Le Quartier repository. Serves as a fallback when the live demo at `lequartier.girard-davila.net` is unavailable. Explains the product, shows a screenshot, and lets visitors request more information via a contact form.

## Hosting

- **Branch:** `gh-pages` (new branch, isolated from `main`)
- **Files:** Single `index.html` at the branch root; references `minutepapillons-current.png` (copied from repo root) as the map screenshot
- **No build step** — plain HTML/CSS/JS, no bundler, no framework

## Analytics

- Google Analytics 4 via the standard `gtag.js` snippet
- Measurement ID: `G-LQT7ER03KR`
- Snippet placed in `<head>` before closing `</head>`

## Page Sections (top to bottom)

### 1. Hero
- Product name: **Le Quartier**
- Tagline: "Your Airbnb listing, mapped. Guests see what's walkable — before they ask."
- Notice banner (styled distinctly, e.g. amber/yellow tint): "Demo temporarily unavailable — fill in the form below to request access or more information."
- CTA button: "Get in touch" — smooth-scrolls to the contact section

### 2. What it does
- 2–3 sentence product description covering: paste a listing URL → backend finds walkable POIs → host gets a shareable map + QR code for guests
- Full-width map screenshot (`minutepapillons-current.png`) with a subtle border/shadow

### 3. How it works
Three numbered steps displayed horizontally (stacked on mobile):
1. Paste your Airbnb listing URL
2. We find every walkable place nearby — supermarkets, parks, restaurants, transit, and more
3. Share the interactive map link or QR code with your guests

### 4. Key features
Four feature cards in a 2×2 grid (stacked on mobile):
- Interactive walkability map
- Downloadable map PNG (1200×800)
- QR code for guests
- Works with Airbnb & Zillow listings

### 5. Contact form
- Fields: Name (text), Email (email, required), Message (textarea, required)
- Integration: Formspree Vanilla JS Ajax CDN (`@formspree/ajax@1` via unpkg)
- Endpoint: `https://formspree.io/f/mnjkjern`
- Inline success message on submission (no page redirect)
- Inline field-level error messages on failure
- Submit button disabled during submission

### 6. Footer
- "© Le Quartier"
- Contact email: `girard.davila@gmail.com` (mailto link)

## Visual Style

- Standalone look, does not inherit the Flask app's CSS
- Off-white background (`#f9f9f7`), dark text (`#1a1a1a`), one accent color (deep green `#2d6a4f` — evokes maps/nature)
- Clean sans-serif: system font stack
- Max content width: 760px, centered, generous padding
- Responsive: single-column on mobile, horizontal layouts on ≥640px

## File Layout (on `gh-pages` branch)

```
index.html
minutepapillons-current.png
```

## Out of Scope

- No server-side logic
- No CDN or custom domain configuration (GitHub Pages default URL is fine)
- No dark mode toggle
- No animations beyond CSS transitions on the contact form button
