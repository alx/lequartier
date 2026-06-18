/* map-core.js — shared Leaflet rendering primitives for Landing Map and Host Map.
 * Exposes window.LQ. Templates inject page-specific data and call LQ.* for all
 * map rendering; page-specific orchestration (polling, form handling, etc.) stays
 * in each template.
 */
(function () {
  'use strict';

  // ── Constants ──────────────────────────────────────────────────────────────
  var POI_SIZE    = 32;
  var RENTAL_SIZE = 40;
  var ACCENT      = '#1a6b3c';

  var DEFAULT_CATS = { 'Supermarket': true, 'Park': true, 'Playground': true };

  var CIRCLE_STYLES = [
    { radiusM: 400,  label: '5 min' },
    { radiusM: 800,  label: '10 min' },
    { radiusM: 1200, label: '15 min' },
  ];

  var FA_ICONS = {
    'park': 'fa-tree', 'train_station': 'fa-train', 'transit': 'fa-bus',
    'airport': 'fa-plane', 'museum': 'fa-landmark', 'monument': 'fa-monument',
    'university': 'fa-graduation-cap', 'stadium': 'fa-futbol',
    'market': 'fa-store', 'beach': 'fa-umbrella-beach',
    'restaurant': 'fa-utensils', 'culture': 'fa-masks-theater',
    'Park': 'fa-tree', 'Transit': 'fa-bus', 'Restaurant': 'fa-utensils',
    'Market': 'fa-store', 'Supermarket': 'fa-cart-shopping',
    'Bakery & Food': 'fa-cookie-bite', 'Bike Share': 'fa-bicycle',
    'Health': 'fa-kit-medical', 'Playground': 'fa-child-reaching',
    'Activity': 'fa-person-running', 'Culture': 'fa-masks-theater',
    'Wellness': 'fa-spa',
    'Night Shop': 'fa-moon', 'Dog Park': 'fa-dog',
  };

  var CATEGORY_COLORS = {
    'Park': '#16a34a', 'Transit': '#1d4ed8', 'Restaurant': '#b45309',
    'Market': '#b45309', 'Supermarket': '#0f766e', 'Bakery & Food': '#b45309',
    'Bike Share': '#0891b2', 'Health': '#dc2626', 'Playground': '#7c3aed',
    'Activity': '#0369a1', 'Culture': '#be123c', 'Wellness': '#059669',
    'park': '#16a34a', 'transit': '#1d4ed8', 'restaurant': '#b45309',
    'market': '#b45309', 'museum': '#b45309', 'culture': '#be123c',
  };

  // ── Shared state ───────────────────────────────────────────────────────────
  var map           = null;
  var ringLayer     = null;
  var poiLayer      = null;
  var rentalMarker  = null;
  var lockedMarkerId  = null;
  var lockedMarkerRef = null;
  var _idSeq        = 0;
  var _catMarkers   = {};
  var _hiddenCats   = {};
  var _secondaryMarkers = [];
  var _secondaryVisible = false;

  // ── Internal helpers ───────────────────────────────────────────────────────
  function _lockPopup(marker) {
    if (lockedMarkerRef && lockedMarkerRef !== marker) lockedMarkerRef.closePopup();
    map.closePopup();
    lockedMarkerId = marker._poiId;
    lockedMarkerRef = marker;
    marker.openPopup();
  }

  function _unlockPopup(marker) {
    lockedMarkerId = null;
    lockedMarkerRef = null;
    marker.closePopup();
  }

  // ── Public functions ───────────────────────────────────────────────────────
  function haversineM(lat1, lon1, lat2, lon2) {
    var R = 6371000;
    var dLat = (lat2 - lat1) * Math.PI / 180;
    var dLon = (lon2 - lon1) * Math.PI / 180;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function poiPopupHtml(f) {
    var props = f.properties;
    var cat   = props.category || '';
    var icon  = FA_ICONS[cat] || 'fa-location-dot';
    var coords = f.geometry.coordinates;
    var html  = '';

    if (props.wikipedia_thumb || props.wikipedia_summary) {
      html += '<div style="display:flex;gap:8px;margin-bottom:8px;align-items:flex-start;">';
      if (props.wikipedia_thumb)
        html += '<img src="' + props.wikipedia_thumb + '" style="width:56px;height:56px;object-fit:cover;border-radius:6px;flex-shrink:0;" alt="">';
      if (props.wikipedia_summary)
        html += '<p style="font-size:0.78em;color:#4b5563;line-height:1.45;margin:0;">' + props.wikipedia_summary + '</p>';
      html += '</div>';
    }

    html += '<strong>' + props.name + '</strong><br>';
    html += '<span style="font-size:0.82em;color:#6b7280;"><i class="fa-solid ' + icon + '"></i> ' + cat.replace(/_/g, ' ') + '</span>';

    if ((cat === 'train_station' || cat === 'transit' || cat === 'transport') && props.transit_url)
      html += '<br><a href="' + props.transit_url + '" target="_blank" rel="noopener"><i class="fa-solid fa-train"></i> Lines &amp; schedules</a>';
    if (cat === 'museum' && props.ticket_url)
      html += '<br><a href="' + props.ticket_url + '" target="_blank" rel="noopener"><i class="fa-solid fa-ticket"></i> Buy tickets</a>';
    if (props.wikipedia_url)
      html += '<br><a href="' + props.wikipedia_url + '" target="_blank" rel="noopener"><i class="fa-brands fa-wikipedia-w"></i> Wikipedia</a>';
    if (cat === 'airport') {
      var adsbUrl = 'https://globe.adsbexchange.com/?lat=' + coords[1].toFixed(4) + '&lon=' + coords[0].toFixed(4) + '&zoom=12';
      html += '<br><a href="' + adsbUrl + '" target="_blank" rel="noopener"><i class="fa-solid fa-plane-departure"></i> Live flights nearby</a>';
    }
    if (cat === 'university' && props.courses_url)
      html += '<br><a href="' + props.courses_url + '" target="_blank" rel="noopener"><i class="fa-solid fa-graduation-cap"></i> Courses</a>';
    var mapsUrl = 'https://www.google.com/maps?q='
      + coords[1].toFixed(6) + ',' + coords[0].toFixed(6);
    var bottomLinks = [];
    if (props.website)
      bottomLinks.push('<a href="' + props.website + '" target="_blank" rel="noopener"><i class="fa-solid fa-arrow-up-right-from-square"></i> Website</a>');
    bottomLinks.push('<a href="' + mapsUrl + '" target="_blank" rel="noopener"><i class="fa-brands fa-google"></i> Google Maps</a>');
    html += '<br>' + bottomLinks.join(' · ');

    if (props.video_url)
      html += '<br><iframe src="' + props.video_url + '" style="width:100%;aspect-ratio:16/9;border:0;border-radius:4px;margin-top:6px;display:block;" allowfullscreen></iframe>';

    return html;
  }

  function drawRings(lat, lon) {
    if (ringLayer) map.removeLayer(ringLayer);
    ringLayer = L.layerGroup();
    CIRCLE_STYLES.forEach(function (c) {
      L.circle([lat, lon], {
        radius: c.radiusM,
        color: ACCENT, weight: 1.5, dashArray: '5 4',
        fill: false, opacity: 0.6, interactive: false,
      }).addTo(ringLayer);
      var labelLat = lat + (c.radiusM / 111320);
      L.marker([labelLat, lon], {
        interactive: false,
        icon: L.divIcon({
          className: '',
          html: '<div style="display:inline-flex;align-items:center;gap:3px;white-space:nowrap;' +
                'font-size:10px;font-weight:600;color:' + ACCENT + ';' +
                'background:rgba(255,255,255,0.88);padding:2px 7px;border-radius:20px;' +
                'box-shadow:0 1px 4px rgba(0,0,0,.15);transform:translate(-50%,-100%);">' +
                '<i class="fa-solid fa-person-walking" style="font-size:9px;"></i>' + c.label + '</div>',
          iconSize: [0, 0], iconAnchor: [0, 0],
        }),
      }).addTo(ringLayer);
    });
    ringLayer.addTo(map);
  }

  function placeRentalMarker(lat, lon) {
    if (rentalMarker) map.removeLayer(rentalMarker);
    rentalMarker = L.marker([lat, lon], {
      interactive: false,
      icon: L.divIcon({
        className: '',
        html: '<div style="position:relative;">' +
              '<div style="width:' + RENTAL_SIZE + 'px;height:' + RENTAL_SIZE + 'px;border-radius:50%;background:' + ACCENT + ';' +
              'display:flex;align-items:center;justify-content:center;' +
              'border:2.5px solid rgba(255,255,255,.85);box-shadow:0 2px 8px rgba(0,0,0,.45);">' +
              '<i class="fa-solid fa-house" style="color:#fff;font-size:' + Math.round(RENTAL_SIZE * 0.43) + 'px;"></i></div>' +
              '<div style="position:absolute;top:' + (RENTAL_SIZE + 4) + 'px;left:50%;transform:translateX(-50%);' +
              'background:' + ACCENT + ';color:#fff;font-size:10px;font-weight:700;' +
              'padding:2px 8px;border-radius:10px;white-space:nowrap;' +
              'box-shadow:0 1px 4px rgba(0,0,0,.3);">your rental</div>' +
              '</div>',
        iconSize: [RENTAL_SIZE, RENTAL_SIZE],
        iconAnchor: [RENTAL_SIZE / 2, RENTAL_SIZE / 2],
      }),
    }).addTo(map);
  }

  function makePOIMarker(f) {
    var props  = f.properties;
    var coords = f.geometry.coordinates;
    var cat    = props.category || '';
    var icon   = FA_ICONS[cat] || 'fa-location-dot';
    var color  = props.color || CATEGORY_COLORS[cat] || '#6b7280';
    var id     = ++_idSeq;

    var placeLabel = '';
    if (props.name && props.status !== 'secondary') {
      placeLabel = '<div style="position:absolute;top:' + (POI_SIZE + 4) + 'px;left:50%;' +
        'transform:translateX(-50%);max-width:80px;width:max-content;' +
        'font-size:9px;font-weight:600;color:#1a1a1a;text-align:center;line-height:1.3;' +
        'background:rgba(255,255,255,0.92);padding:2px 6px;border-radius:10px;' +
        'box-shadow:0 1px 4px rgba(0,0,0,.18);pointer-events:none;word-break:break-word;">' +
        props.name + '</div>';
    }

    var m = L.marker([coords[1], coords[0]], {
      icon: L.divIcon({
        className: '',
        html: '<div style="position:relative;width:' + POI_SIZE + 'px;height:' + POI_SIZE + 'px;overflow:visible;">' +
              '<div style="width:' + POI_SIZE + 'px;height:' + POI_SIZE + 'px;border-radius:50%;background:' + color +
              ';display:flex;align-items:center;justify-content:center;' +
              'border:2px solid rgba(255,255,255,1);box-shadow:0 1px 5px rgba(0,0,0,.4);cursor:pointer;">' +
              '<i class="fa-solid ' + icon + '" style="color:#fff;font-size:' + Math.round(POI_SIZE * 0.4) + 'px;"></i></div>' +
              placeLabel +
              '</div>',
        iconSize: [POI_SIZE, POI_SIZE], iconAnchor: [POI_SIZE / 2, POI_SIZE / 2],
      }),
    });

    m._poiId = id;
    m._cat   = cat;
    m.bindPopup(poiPopupHtml(f), { maxWidth: 320, autoPan: false });

    m.on('mouseover', function () {
      if (lockedMarkerId === this._poiId) return;
      _lockPopup(this);
    });
    m.on('click', function (e) {
      L.DomEvent.stopPropagation(e);
      if (lockedMarkerId === this._poiId) _unlockPopup(this);
      else _lockPopup(this);
    });

    return m;
  }

  // Returns the new hidden state for the category (true = now hidden).
  // Callers are responsible for updating their own pill DOM.
  function toggleCat(cat) {
    _hiddenCats[cat] = !_hiddenCats[cat];
    var markers = _catMarkers[cat] || [];
    markers.forEach(function (m) {
      if (_hiddenCats[cat]) poiLayer.removeLayer(m);
      else poiLayer.addLayer(m);
    });
    return _hiddenCats[cat];
  }

  // Returns the new visibility state (true = secondary markers now visible).
  // Callers are responsible for updating their own show-more DOM.
  function toggleSecondary() {
    _secondaryVisible = !_secondaryVisible;
    _secondaryMarkers.forEach(function (m) {
      if (_secondaryVisible) {
        if (!_hiddenCats[m._cat]) poiLayer.addLayer(m);
      } else {
        poiLayer.removeLayer(m);
      }
    });
    return _secondaryVisible;
  }

  function initMap(options) {
    options = options || {};
    var leafletOptions = { zoomControl: true, attributionControl: false };
    if (options.keepBuffer) leafletOptions.keepBuffer = options.keepBuffer;
    map = L.map('map', leafletOptions);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    }).addTo(map);
    map.on('click', function () {
      if (lockedMarkerRef) { lockedMarkerRef.closePopup(); lockedMarkerRef = null; }
      lockedMarkerId = null;
    });
    return map;
  }

  function resetState() {
    _catMarkers       = {};
    _hiddenCats       = {};
    _secondaryMarkers = [];
    _secondaryVisible = false;
    lockedMarkerId    = null;
    lockedMarkerRef   = null;
  }

  // ── Public API (window.LQ) ─────────────────────────────────────────────────
  window.LQ = {
    // Constants
    POI_SIZE:        POI_SIZE,
    RENTAL_SIZE:     RENTAL_SIZE,
    ACCENT:          ACCENT,
    DEFAULT_CATS:    DEFAULT_CATS,
    FA_ICONS:        FA_ICONS,
    CATEGORY_COLORS: CATEGORY_COLORS,
    CIRCLE_STYLES:   CIRCLE_STYLES,

    // State — primitive fields use getters/setters; object/array fields are
    // returned by reference so callers can mutate them directly (e.g. push, [key]=).
    get map()              { return map; },
    set map(v)             { map = v; },
    get poiLayer()         { return poiLayer; },
    set poiLayer(v)        { poiLayer = v; },
    get ringLayer()        { return ringLayer; },
    set ringLayer(v)       { ringLayer = v; },
    get rentalMarker()     { return rentalMarker; },
    set rentalMarker(v)    { rentalMarker = v; },
    get lockedMarkerId()   { return lockedMarkerId; },
    set lockedMarkerId(v)  { lockedMarkerId = v; },
    get lockedMarkerRef()  { return lockedMarkerRef; },
    set lockedMarkerRef(v) { lockedMarkerRef = v; },
    get catMarkers()       { return _catMarkers; },
    get hiddenCats()       { return _hiddenCats; },
    get secondaryMarkers() { return _secondaryMarkers; },
    get secondaryVisible() { return _secondaryVisible; },
    set secondaryVisible(v){ _secondaryVisible = v; },

    // Functions
    haversineM:        haversineM,
    poiPopupHtml:      poiPopupHtml,
    drawRings:         drawRings,
    placeRentalMarker: placeRentalMarker,
    makePOIMarker:     makePOIMarker,
    toggleCat:         toggleCat,
    toggleSecondary:   toggleSecondary,
    initMap:           initMap,
    resetState:        resetState,
  };
})();
