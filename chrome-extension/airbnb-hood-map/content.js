'use strict';

const LQ_ROOT_ID  = 'lq-airbnb-map-root';
const LQ_BTN_CLS  = 'lq-airbnb-top-btn';
const BACKEND     = 'https://lequartier.girard-davila.net';

function getListingId() {
  const m = location.pathname.match(/\/rooms\/(\d+)/);
  return m ? m[1] : null;
}

function findShareContainer() {
  for (const btn of document.querySelectorAll('button')) {
    if (/partager|share/i.test((btn.innerText || btn.textContent || '').trim()))
      return btn.parentElement || btn;
  }
  return null;
}

// Airbnb renders "What this place offers" / "Ce que propose ce logement"
// as an h2 inside a section. We inject the map after that section.
function findAmenitiesSection() {
  for (const el of document.querySelectorAll('h2, h3')) {
    if (/what this place offers|ce que propose/i.test((el.innerText || el.textContent || '').trim()))
      return el.closest('section') || el.parentElement;
  }
  return null;
}

function inject() {
  const listingId = getListingId();
  if (!listingId) return;

  // ── Top-bar anchor button ──────────────────────────────────────────────────
  if (!document.querySelector(`.${LQ_BTN_CLS}`)) {
    const shareContainer = findShareContainer();
    if (shareContainer) {
      const wrapper = document.createElement('div');
      wrapper.className = LQ_BTN_CLS;

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.title = 'Le Quartier – carte du quartier';
      btn.innerHTML = `<span aria-hidden="true">🗺️</span><span>Le Quartier</span>`;
      btn.addEventListener('click', () => {
        document.getElementById(LQ_ROOT_ID)
          ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });

      wrapper.appendChild(btn);
      shareContainer.insertAdjacentElement('beforebegin', wrapper);
    }
  }

  // ── Inline map iframe ──────────────────────────────────────────────────────
  if (!document.getElementById(LQ_ROOT_ID)) {
    const mapUrl = `${BACKEND}/airbnb/${listingId}`;

    const container = document.createElement('div');
    container.id = LQ_ROOT_ID;
    container.innerHTML = `
      <div class="lq-map-header">
        <span class="lq-map-title">🏘 Le Quartier</span>
        <a href="${mapUrl}" target="_blank" rel="noopener noreferrer" class="lq-full-map-link">Voir la carte complète ↗</a>
      </div>
      <iframe
        src="${mapUrl}"
        title="Carte du quartier"
        loading="lazy"
        allowfullscreen
      ></iframe>
    `;

    const anchor = findAmenitiesSection();
    if (anchor?.parentElement) {
      anchor.after(container);
    } else {
      (document.querySelector('main') || document.body).appendChild(container);
    }
  }
}

function cleanup() {
  document.getElementById(LQ_ROOT_ID)?.remove();
  document.querySelector(`.${LQ_BTN_CLS}`)?.remove();
}

// ── Bootstrap: wait for share button via MutationObserver ─────────────────
let attempts = 0;
const MAX_ATTEMPTS = 60;

const observer = new MutationObserver(() => {
  if (++attempts > MAX_ATTEMPTS) return; // reset on navigation
  inject();
});
observer.observe(document.body, { childList: true, subtree: true });

// ── SPA navigation (Airbnb is a React SPA) ────────────────────────────────
const _origPush    = history.pushState.bind(history);
const _origReplace = history.replaceState.bind(history);
history.pushState    = (...args) => { _origPush(...args);    cleanup(); attempts = 0; };
history.replaceState = (...args) => { _origReplace(...args); cleanup(); attempts = 0; };
window.addEventListener('popstate', () => { cleanup(); attempts = 0; });

inject();
