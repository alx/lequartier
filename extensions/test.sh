#!/usr/bin/env bash
# Pre-build validation: ESLint + manifest integrity checks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$SCRIPT_DIR"

PASS=0
FAIL=0

if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; BOLD='\033[1m'; RESET='\033[0m'
else
  RED=''; GREEN=''; BOLD=''; RESET=''
fi

pass() { printf "${GREEN}  PASS${RESET}  %s\n" "$1"; PASS=$((PASS+1)); }
fail() { printf "${RED}  FAIL${RESET}  %s\n" "$1"; FAIL=$((FAIL+1)); }

# ── Step 0: Bootstrap ESLint ──────────────────────────────────────────────────
ESLINT="$ROOT/node_modules/.bin/eslint"
if [ ! -x "$ESLINT" ]; then
  printf "${BOLD}Installing ESLint (first run)…${RESET}\n"
  if [ -f "$ROOT/package-lock.json" ]; then
    npm ci --prefix "$ROOT" --silent
  else
    npm install --prefix "$ROOT" --silent
  fi
fi

# ── Step 1: ESLint — browser-ext / shared / sites ─────────────────────────────
printf "\n${BOLD}1/3  ESLint: browser-ext / shared / sites${RESET}\n"
if "$ESLINT" \
     --config "$ROOT/eslint.config.mjs" \
     "$ROOT/browser-ext/"*.js \
     "$ROOT/shared/"*.js \
     "$ROOT/sites/"*.js; then
  pass "ESLint clean (browser-ext, shared, sites)"
else
  fail "ESLint errors in browser-ext / shared / sites"
fi

# ── Step 2: ESLint — userscripts ──────────────────────────────────────────────
printf "\n${BOLD}2/3  ESLint: userscripts${RESET}\n"
if "$ESLINT" \
     --config "$ROOT/eslint.config.mjs" \
     "$ROOT/userscripts/"*.js; then
  pass "ESLint clean (userscripts)"
else
  fail "ESLint errors in userscripts"
fi

# ── Step 3: Manifest JSON + file-reference checks ─────────────────────────────
printf "\n${BOLD}3/3  Manifest JSON + file-reference checks${RESET}\n"

for PLATFORM in chrome firefox; do
  for SITE in airbnb zillow; do
    MANIFEST="$ROOT/$PLATFORM/$SITE/manifest.json"
    LABEL="$PLATFORM/$SITE/manifest.json"

    # 3a: JSON well-formedness
    if node - "$MANIFEST" <<'JSEOF'
const fs = require('fs');
const manifestPath = process.argv[2];
try {
  JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  process.exit(0);
} catch (e) {
  process.stderr.write(e.message + '\n');
  process.exit(1);
}
JSEOF
    then
      pass "JSON valid: $LABEL"
    else
      fail "JSON invalid: $LABEL"
      continue
    fi

    # 3b: All files referenced in manifest exist in the source tree
    if node - "$MANIFEST" "$SITE" "$ROOT" <<'JSEOF'
const fs = require('fs'), path = require('path');
const [,, manifestPath, site, root] = process.argv;
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

const refs = new Set();
if (manifest.background && manifest.background.service_worker)
  refs.add(manifest.background.service_worker);
if (manifest.action && manifest.action.default_popup)
  refs.add(manifest.action.default_popup);
for (const cs of manifest.content_scripts || []) {
  for (const f of (cs.js || [])) refs.add(f);
  for (const f of (cs.css || [])) refs.add(f);
}
for (const group of manifest.web_accessible_resources || []) {
  for (const f of (group.resources || [])) refs.add(f);
}

function srcFor(name) {
  if (name === 'background.js') return 'browser-ext/background.js';
  if (name === 'popup.html')    return 'browser-ext/popup.html';
  if (name === 'popup.js')      return 'browser-ext/popup.js';
  if (name === 'map-init.js')   return 'shared/map-init.js';
  if (name === 'styles.css')    return 'shared/styles.css';
  if (name === site + '.js')    return 'sites/' + site + '.js';
  if (name.startsWith('libs/')) return 'shared/' + name;
  return null;
}

let ok = true;
for (const ref of refs) {
  const rel = srcFor(ref);
  if (!rel) continue;
  const abs = path.join(root, rel);
  if (!fs.existsSync(abs)) {
    process.stderr.write('  missing: ' + ref + ' -> ' + rel + '\n');
    ok = false;
  }
}
process.exit(ok ? 0 : 1);
JSEOF
    then
      pass "File refs OK: $LABEL"
    else
      fail "Missing files referenced by: $LABEL"
    fi

  done
done

# ── Summary ───────────────────────────────────────────────────────────────────
printf "\n──────────────────────────────────────\n"
if [ "$FAIL" -eq 0 ]; then
  printf "${GREEN}${BOLD}All checks passed${RESET} ($PASS passed, 0 failed)\n"
else
  printf "${RED}${BOLD}$FAIL check(s) failed${RESET} ($PASS passed, $FAIL failed)\n"
  exit 1
fi
