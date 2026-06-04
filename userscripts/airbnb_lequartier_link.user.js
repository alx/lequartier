// ==UserScript==
// @name         Le Quartier – Airbnb Link
// @namespace    https://girard-davila.net
// @version      1.0.0
// @description  Adds a "Le Quartier" link on Airbnb listing pages that opens the neighbourhood view on lequartier.girard-davila.net
// @author       Alexandre Girard-Davila
// @match        https://www.airbnb.fr/rooms/*
// @match        https://www.airbnb.com/rooms/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  // ─── Extract the listing ID from the current URL ───────────────────────────
  function getListingId() {
    const match = window.location.pathname.match(/\/rooms\/(\d+)/);
    return match ? match[1] : null;
  }

  // ─── Build the destination URL ────────────────────────────────────────────
  function buildLeQuartierUrl(listingId) {
    return `https://lequartier.girard-davila.net/airbnb/${listingId}`;
  }

  // ─── Find the "Partager" button container ─────────────────────────────────
  // Airbnb renders the share button as a <button> containing a div whose text
  // includes "Partager" (FR) or "Share" (COM). We walk up to the wrapping <div>
  // that sits alongside the save button so we can insert our link before it.
  function findShareContainer() {
    // Look for any button whose visible text contains "Partager" or "Share"
    const buttons = document.querySelectorAll('button');
    for (const btn of buttons) {
      const text = btn.innerText || btn.textContent || '';
      if (/partager|share/i.test(text.trim())) {
        // The button is typically wrapped in a <div>; return that wrapper
        return btn.parentElement || btn;
      }
    }
    return null;
  }

  // ─── Create the Le Quartier button ────────────────────────────────────────
  function createLeQuartierButton(url) {
    // Mirror Airbnb's own share-button wrapper structure so it fits naturally
    const wrapper = document.createElement('div');
    wrapper.className = 'lq-wrapper';
    wrapper.style.cssText = 'display:inline-flex;align-items:center;';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.title = 'Voir le quartier';
    // Borrow Airbnb's own button class for visual consistency
    btn.className = 'b1iktwwp cn6bjj0 dir dir-ltr';
    btn.style.cssText = 'display:inline-flex;align-items:center;gap:6px;padding:8px 12px;background:transparent;border:none;cursor:pointer;font-size:14px;font-weight:600;text-decoration:none;color:inherit;position:relative;top:-2px;';

    btn.innerHTML = `<span style="font-size:16px;text-decoration:none;display:inline-block;" aria-hidden="true">🗺️</span><span style="text-decoration:underline;">Le Quartier</span>`;

    btn.addEventListener('click', () => {
      window.open(url, '_blank', 'noopener,noreferrer');
    });

    wrapper.appendChild(btn);
    return wrapper;
  }

  // ─── Inject the button ────────────────────────────────────────────────────
  function inject() {
    const listingId = getListingId();
    if (!listingId) return;

    // Bail if already injected
    if (document.querySelector('.lq-wrapper')) return;

    const shareContainer = findShareContainer();
    if (!shareContainer) return;

    const url = buildLeQuartierUrl(listingId);
    const lqButton = createLeQuartierButton(url);

    shareContainer.insertAdjacentElement('beforebegin', lqButton);
    console.log('[LeQuartier] Injected for listing', listingId, '→', url);
  }

  // ─── Watch for DOM changes (Airbnb is a SPA) ──────────────────────────────
  // Airbnb loads content dynamically; we observe the DOM until the share
  // button appears, then inject once and disconnect.
  let attempts = 0;
  const MAX_ATTEMPTS = 40; // ~10 seconds at 250 ms intervals

  const observer = new MutationObserver(() => {
    attempts++;
    if (attempts > MAX_ATTEMPTS) {
      observer.disconnect();
      return;
    }
    const shareContainer = findShareContainer();
    if (shareContainer) {
      inject();
      // Keep observing in case of SPA navigation to another listing
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });

  // Also handle Airbnb's client-side navigation (pushState / replaceState)
  const _origPushState = history.pushState.bind(history);
  const _origReplaceState = history.replaceState.bind(history);

  function onNavigate() {
    // Remove stale button so it gets re-injected for the new listing
    const existing = document.querySelector('.lq-wrapper');
    if (existing) existing.remove();
    attempts = 0; // reset attempt counter
  }

  history.pushState = function (...args) {
    _origPushState(...args);
    onNavigate();
  };

  history.replaceState = function (...args) {
    _origReplaceState(...args);
    onNavigate();
  };

  window.addEventListener('popstate', onNavigate);

  // Initial run for direct page loads
  inject();
})();