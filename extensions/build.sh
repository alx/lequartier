#!/usr/bin/env bash
# Assembles browser extension packages from shared + site + platform source files.
# Output: extensions/dist/{platform}-{site}/ (unpacked) and .zip (store-ready)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$SCRIPT_DIR"
DIST="$ROOT/dist"

echo "Running pre-build checks…"
bash "$ROOT/test.sh"
echo ""

for PLATFORM in chrome firefox; do
  for SITE in zillow airbnb; do
    TARGET="$DIST/$PLATFORM-$SITE"
    echo "Building $PLATFORM-$SITE → $TARGET"

    rm -rf "$TARGET"
    mkdir -p "$TARGET"

    # Platform + site manifest
    cp "$ROOT/$PLATFORM/$SITE/manifest.json" "$TARGET/"

    # Site content script (named after the site for clarity in devtools)
    cp "$ROOT/sites/$SITE.js" "$TARGET/"

    # Shared map renderer and styles
    cp "$ROOT/shared/map-init.js" "$TARGET/"
    cp "$ROOT/shared/styles.css"  "$TARGET/"

    # Shared asset libs (Leaflet + FontAwesome)
    cp -r "$ROOT/shared/libs" "$TARGET/"

    # Browser-extension shared files (background worker + popup)
    cp "$ROOT/browser-ext/background.js" "$TARGET/"
    cp "$ROOT/browser-ext/popup.html"    "$TARGET/"
    cp "$ROOT/browser-ext/popup.js"      "$TARGET/"

    # Zip for store upload
    (cd "$DIST" && zip -qr "$PLATFORM-$SITE.zip" "$PLATFORM-$SITE/")
    echo "  ✓ $PLATFORM-$SITE.zip"
  done
done

echo ""
echo "Done. Packages in $DIST/"
