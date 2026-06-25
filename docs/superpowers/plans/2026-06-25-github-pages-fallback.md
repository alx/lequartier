# GitHub Pages Fallback Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy a standalone static fallback page to `gh-pages` that explains Le Quartier, shows a map screenshot, and lets visitors request access via a Formspree contact form.

**Architecture:** Single `index.html` on a dedicated `gh-pages` branch. All styling is inline in a `<style>` block. Formspree Vanilla JS Ajax loaded via CDN handles contact form state. Google Analytics 4 snippet in `<head>`. No build step.

**Tech Stack:** Plain HTML5, CSS (no framework), Formspree `@formspree/ajax@1` (unpkg CDN), Google Analytics 4 (`gtag.js`)

## Global Constraints

- Branch: `gh-pages` — isolated from `main`, only `index.html` + `minutepapillons-current.png` at root
- Formspree form ID: `mnjkjern` (endpoint: `https://formspree.io/f/mnjkjern`)
- GA4 Measurement ID: `G-LQT7ER03KR`
- Contact email: `girard.davila@gmail.com`
- Accent color: `#2d6a4f` (deep green)
- Background: `#f9f9f7`, text: `#1a1a1a`
- Max content width: `760px`, centered
- Screenshot file: `minutepapillons-current.png` (607 KB, already in repo root on `main`)
- No dark mode, no animations beyond button CSS transitions

---

### Task 1: Create `gh-pages` branch and scaffold `index.html` with GA

**Files:**
- Create: `index.html` (on `gh-pages` branch root)

**Interfaces:**
- Produces: A deployable branch with a bare HTML page that loads GA and has the correct `<head>` metadata. All subsequent tasks add sections into the `<main>` element.

- [ ] **Step 1: Create and switch to `gh-pages` branch (orphan — no shared history with `main`)**

```bash
git checkout --orphan gh-pages
git rm -rf .
```

- [ ] **Step 2: Create `index.html` with full document shell, GA snippet, and reset CSS**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Le Quartier — Neighbourhood map for Airbnb listings</title>
  <meta name="description" content="Le Quartier generates an interactive walkability map for your Airbnb listing. Share it with guests so they know what's nearby before they ask." />

  <!-- Google Analytics -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-LQT7ER03KR"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){dataLayer.push(arguments);}
    gtag('js', new Date());
    gtag('config', 'G-LQT7ER03KR');
  </script>

  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: #f9f9f7;
      color: #1a1a1a;
      line-height: 1.6;
    }

    .container {
      max-width: 760px;
      margin: 0 auto;
      padding: 0 1.25rem;
    }

    /* ── Hero ── */
    .hero {
      padding: 4rem 0 2.5rem;
      text-align: center;
    }
    .hero-logo {
      font-size: 1rem;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: #2d6a4f;
      margin-bottom: 1rem;
    }
    .hero h1 {
      font-size: clamp(1.75rem, 4vw, 2.5rem);
      font-weight: 800;
      line-height: 1.2;
      margin-bottom: 0.75rem;
      color: #1a1a1a;
    }
    .hero p {
      font-size: 1.1rem;
      color: #555;
      margin-bottom: 1.5rem;
    }
    .notice {
      background: #fef9c3;
      border: 1px solid #f7d842;
      border-radius: 8px;
      padding: 0.85rem 1.1rem;
      font-size: 0.92rem;
      color: #7a5c00;
      margin-bottom: 1.75rem;
    }
    .btn-primary {
      display: inline-block;
      background: #2d6a4f;
      color: #fff;
      text-decoration: none;
      padding: 0.75rem 2rem;
      border-radius: 6px;
      font-size: 1rem;
      font-weight: 600;
      transition: background 0.15s;
    }
    .btn-primary:hover { background: #245a42; }

    /* ── Section shared ── */
    section { padding: 3rem 0; }
    section + section { border-top: 1px solid #e5e5e3; }

    h2 {
      font-size: 1.5rem;
      font-weight: 700;
      margin-bottom: 1rem;
      color: #1a1a1a;
    }
    p + p { margin-top: 0.6rem; }

    /* ── Screenshot ── */
    .screenshot-wrap {
      margin-top: 1.75rem;
      border-radius: 10px;
      overflow: hidden;
      border: 1px solid #ddd;
      box-shadow: 0 4px 20px rgba(0,0,0,0.1);
    }
    .screenshot-wrap img {
      width: 100%;
      display: block;
    }

    /* ── How it works ── */
    .steps {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1.5rem;
      margin-top: 1.5rem;
    }
    .step {
      background: #fff;
      border: 1px solid #e5e5e3;
      border-radius: 10px;
      padding: 1.25rem 1rem;
    }
    .step-num {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 2rem;
      height: 2rem;
      background: #2d6a4f;
      color: #fff;
      border-radius: 50%;
      font-weight: 700;
      font-size: 0.9rem;
      margin-bottom: 0.6rem;
    }
    .step h3 { font-size: 0.95rem; font-weight: 700; margin-bottom: 0.3rem; }
    .step p  { font-size: 0.88rem; color: #555; }

    /* ── Features ── */
    .features {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
      margin-top: 1.5rem;
    }
    @media (max-width: 480px) { .features { grid-template-columns: 1fr; } }
    .feature {
      background: #fff;
      border: 1px solid #e5e5e3;
      border-radius: 10px;
      padding: 1.1rem 1rem;
    }
    .feature-icon {
      font-size: 1.4rem;
      margin-bottom: 0.4rem;
    }
    .feature h3 { font-size: 0.95rem; font-weight: 700; margin-bottom: 0.2rem; }
    .feature p  { font-size: 0.85rem; color: #555; }

    /* ── Contact form ── */
    .contact-form {
      margin-top: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .form-field {
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .form-field label {
      font-size: 0.9rem;
      font-weight: 600;
      color: #333;
    }
    .form-field input,
    .form-field textarea {
      padding: 0.65rem 0.85rem;
      border: 1px solid #ccc;
      border-radius: 6px;
      font-size: 0.95rem;
      font-family: inherit;
      background: #fff;
      transition: border-color 0.15s;
    }
    .form-field input:focus,
    .form-field textarea:focus {
      outline: none;
      border-color: #2d6a4f;
    }
    .form-field input[aria-invalid="true"],
    .form-field textarea[aria-invalid="true"] {
      border-color: #c0392b;
    }
    .form-field textarea { resize: vertical; min-height: 120px; }
    .fs-error-msg {
      font-size: 0.8rem;
      color: #c0392b;
      min-height: 1rem;
    }
    .fs-success {
      display: none;
      background: #d4edda;
      border: 1px solid #c3e6cb;
      border-radius: 8px;
      padding: 1rem;
      color: #155724;
      font-size: 0.95rem;
      margin-bottom: 1rem;
    }
    .fs-form-error {
      display: none;
      background: #f8d7da;
      border: 1px solid #f5c6cb;
      border-radius: 8px;
      padding: 0.85rem 1rem;
      color: #721c24;
      font-size: 0.9rem;
      margin-bottom: 0.75rem;
    }
    .btn-submit {
      align-self: flex-start;
      background: #2d6a4f;
      color: #fff;
      border: none;
      padding: 0.75rem 2rem;
      border-radius: 6px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s, opacity 0.15s;
    }
    .btn-submit:hover:not(:disabled) { background: #245a42; }
    .btn-submit:disabled { opacity: 0.6; cursor: not-allowed; }

    /* ── Footer ── */
    footer {
      border-top: 1px solid #e5e5e3;
      padding: 2rem 0;
      text-align: center;
      font-size: 0.85rem;
      color: #888;
    }
    footer a { color: #2d6a4f; text-decoration: none; }
    footer a:hover { text-decoration: underline; }
  </style>
</head>
<body>

  <!-- HERO -->
  <div class="container">
    <section class="hero">
      <div class="hero-logo">Le Quartier</div>
      <h1>Your Airbnb listing, mapped.<br>Guests see what's walkable — before they ask.</h1>
      <p>Generate a shareable neighbourhood map for your rental in minutes.</p>
      <div class="notice">
        ⚠️ The live demo is temporarily unavailable. Fill in the form below to request access or learn more about the project.
      </div>
      <a href="#contact" class="btn-primary">Get in touch</a>
    </section>
  </div>

  <!-- WHAT IT DOES -->
  <div class="container">
    <section id="about">
      <h2>What is Le Quartier?</h2>
      <p>Le Quartier takes any Airbnb listing URL and automatically finds every walkable point of interest nearby — supermarkets, parks, restaurants, transit stops, and more. The result is an interactive map your guests can explore before they even book.</p>
      <p>Hosts get a permanent shareable link and a downloadable QR code they can drop into their listing description, welcome book, or check-in message. No design skills required.</p>
      <div class="screenshot-wrap">
        <img src="minutepapillons-current.png" alt="Le Quartier neighbourhood map showing walkable POIs around a rental listing" />
      </div>
    </section>
  </div>

  <!-- HOW IT WORKS -->
  <div class="container">
    <section id="how">
      <h2>How it works</h2>
      <div class="steps">
        <div class="step">
          <div class="step-num">1</div>
          <h3>Paste your listing URL</h3>
          <p>Drop in your Airbnb listing URL. Le Quartier supports Airbnb and Zillow listings.</p>
        </div>
        <div class="step">
          <div class="step-num">2</div>
          <h3>We map what's nearby</h3>
          <p>Our pipeline finds every walkable place — supermarkets, parks, transit, bakeries, restaurants, and more.</p>
        </div>
        <div class="step">
          <div class="step-num">3</div>
          <h3>Share with your guests</h3>
          <p>Get a permanent shareable link and a QR code PNG ready to embed in your listing or welcome message.</p>
        </div>
      </div>
    </section>
  </div>

  <!-- KEY FEATURES -->
  <div class="container">
    <section id="features">
      <h2>Key features</h2>
      <div class="features">
        <div class="feature">
          <div class="feature-icon">🗺️</div>
          <h3>Interactive walkability map</h3>
          <p>Guests can explore nearby POIs by category — toggle supermarkets, parks, transit, restaurants, and more.</p>
        </div>
        <div class="feature">
          <div class="feature-icon">🖼️</div>
          <h3>Downloadable map PNG</h3>
          <p>Export a 1200×800 px map image ready to embed in your listing or print for a welcome book.</p>
        </div>
        <div class="feature">
          <div class="feature-icon">📱</div>
          <h3>QR code for guests</h3>
          <p>A QR code that links directly to your map — scan and go, no app required.</p>
        </div>
        <div class="feature">
          <div class="feature-icon">🏠</div>
          <h3>Airbnb &amp; Zillow support</h3>
          <p>Works with listings from both Airbnb and Zillow. Paste the URL and the rest is automatic.</p>
        </div>
      </div>
    </section>
  </div>

  <!-- CONTACT FORM -->
  <div class="container">
    <section id="contact">
      <h2>Request access or get in touch</h2>
      <p>The demo is temporarily offline. Leave your email and a message and we'll get back to you.</p>

      <div data-fs-success class="fs-success">
        Thanks for reaching out! We'll get back to you soon.
      </div>
      <div data-fs-error class="fs-form-error"></div>

      <form id="contact-form" class="contact-form">
        <div class="form-field">
          <label for="name">Name</label>
          <input type="text" id="name" name="name" placeholder="Your name" />
        </div>
        <div class="form-field">
          <label for="email">Email <span style="color:#c0392b">*</span></label>
          <input type="email" id="email" name="email" required placeholder="you@example.com" data-fs-field />
          <span data-fs-error="email" class="fs-error-msg"></span>
        </div>
        <div class="form-field">
          <label for="message">Message <span style="color:#c0392b">*</span></label>
          <textarea id="message" name="message" required placeholder="Tell us about your listing or what you'd like to know…" data-fs-field></textarea>
          <span data-fs-error="message" class="fs-error-msg"></span>
        </div>
        <button type="submit" class="btn-submit" data-fs-submit-btn>Send message</button>
      </form>
    </section>
  </div>

  <!-- FOOTER -->
  <footer>
    <div class="container">
      <p>© Le Quartier &nbsp;·&nbsp; <a href="mailto:girard.davila@gmail.com">girard.davila@gmail.com</a></p>
    </div>
  </footer>

  <!-- Formspree Vanilla JS Ajax -->
  <script>
    window.formspree = window.formspree || function () {
      (formspree.q = formspree.q || []).push(arguments);
    };
    formspree('initForm', { formElement: '#contact-form', formId: 'mnjkjern' });
  </script>
  <script src="https://unpkg.com/@formspree/ajax@1" defer></script>

</body>
</html>
```

- [ ] **Step 3: Verify the file was created**

```bash
ls -la index.html
```

Expected output: `index.html` listed with a non-zero size (~10 KB).

- [ ] **Step 4: Commit scaffold**

```bash
git add index.html
git commit -m "feat: add GitHub Pages fallback page with all sections"
```

---

### Task 2: Copy screenshot and verify page renders correctly

**Files:**
- Create: `minutepapillons-current.png` (on `gh-pages` branch root, copied from `main`)

**Interfaces:**
- Consumes: `index.html` from Task 1 (references `minutepapillons-current.png` as a relative path)
- Produces: A fully self-contained `gh-pages` branch ready to push

- [ ] **Step 1: Copy the screenshot from `main` using `git show`**

```bash
git show main:minutepapillons-current.png > minutepapillons-current.png
```

- [ ] **Step 2: Verify the file copied correctly**

```bash
ls -lh minutepapillons-current.png
```

Expected: file ~593 KB (607125 bytes).

- [ ] **Step 3: Open the page in a browser for a local sanity check**

```bash
python3 -m http.server 8099
```

Open `http://localhost:8099` in a browser. Verify:
- Hero section loads with tagline and amber notice banner
- "Get in touch" button scrolls smoothly to the contact form
- Screenshot image renders (not broken)
- All three How it works steps display in a row on desktop
- Feature cards appear in a 2×2 grid on desktop
- Contact form fields are visible
- Footer shows email link

Stop the server with `Ctrl+C` when done.

- [ ] **Step 4: Commit screenshot**

```bash
git add minutepapillons-current.png
git commit -m "feat: add map screenshot for fallback page"
```

---

### Task 3: Test contact form and push to GitHub Pages

**Files:**
- No new files. Verify Formspree integration end-to-end.

**Interfaces:**
- Consumes: `index.html` from Task 1 with Formspree form ID `mnjkjern`
- Produces: Live GitHub Pages URL with working contact form

- [ ] **Step 1: Submit a real test message through the form**

With the local server still running (`python3 -m http.server 8099`), fill in and submit the contact form at `http://localhost:8099`:
- Name: `Test`
- Email: `girard.davila@gmail.com`
- Message: `Test submission from local dev`

Expected: inline success message appears ("Thanks for reaching out!"), form fields are hidden, no page redirect.

- [ ] **Step 2: Check Formspree dashboard for the test submission**

Log in at `https://formspree.io` and confirm the submission arrived in the `mnjkjern` form inbox.

- [ ] **Step 3: Verify mobile layout**

In the browser, open DevTools (`F12`) → toggle device toolbar → select a 375px-wide viewport (iPhone SE). Verify:
- How it works steps stack vertically
- Feature cards stack to a single column
- Contact form fields are full width
- No horizontal overflow

- [ ] **Step 4: Push `gh-pages` branch to GitHub**

```bash
git push origin gh-pages
```

- [ ] **Step 5: Confirm GitHub Pages is enabled and note the live URL**

Go to the repository settings on GitHub → Pages → confirm source branch is `gh-pages`, root folder `/`. The live URL will be:
`https://<your-github-username>.github.io/<repo-name>/`

Open the live URL and verify the page loads, the screenshot displays, and the contact form is functional.

- [ ] **Step 6: Return to `main` branch**

```bash
git checkout main
```
