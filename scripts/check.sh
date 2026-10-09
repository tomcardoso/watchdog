#!/usr/bin/env bash
# Every check a change needs, the Python and app halves at the same time:
#   scripts/check.sh           everything (lint, tests, typecheck, unit tests, build, end-to-end)
#   scripts/check.sh py        Python only: ruff and the test suite
#   scripts/check.sh app       app only: typecheck, unit tests, build, end-to-end
# PYTHON names the interpreter whose environment has Watchdog's dependencies (default: python3).
# The test suite runs on every core when pytest-xdist is installed (the `dev` extra has it).
set -u
cd "$(dirname "$0")/.."
ROOT=$PWD
PY=${PYTHON:-python3}
BIN=$(dirname "$("$PY" -c 'import sys; print(sys.executable)')")
export PYTHONPATH="$ROOT/src"
WHAT=${1:-all}
LOGS=$(mktemp -d)

xvfb_wrap() { if [ -z "${DISPLAY:-}" ] && command -v xvfb-run >/dev/null; then xvfb-run -a "$@"; else "$@"; fi; }
py_checks() {
  local n=""
  "$PY" -c "import xdist" 2>/dev/null && n="-n auto"
  "$BIN/ruff" check src tests && "$PY" -m pytest -q $n -p no:cacheprovider
}
app_checks() {
  cd gui && npm run --silent typecheck && npm run --silent test:unit >/dev/null && npx electron-vite build >/dev/null \
    && WATCHDOG_PYTHON="$PY" xvfb_wrap npx playwright test
}

status=0
if [ "$WHAT" = all ] || [ "$WHAT" = py ]; then py_checks >"$LOGS/py.log" 2>&1 & PYPID=$!; fi
if [ "$WHAT" = all ] || [ "$WHAT" = app ]; then app_checks >"$LOGS/app.log" 2>&1 & APPPID=$!; fi
if [ -n "${PYPID:-}" ]; then
  if wait "$PYPID"; then echo "python: ok  ($(tail -1 "$LOGS/py.log"))"; else echo "python: FAILED"; tail -40 "$LOGS/py.log"; status=1; fi
fi
if [ -n "${APPPID:-}" ]; then
  if wait "$APPPID"; then echo "app: ok  ($(grep -E '[0-9]+ (passed|failed)' "$LOGS/app.log" | tail -1 | sed 's/^ *//'))"; else echo "app: FAILED"; tail -60 "$LOGS/app.log"; status=1; fi
fi
rm -rf "$LOGS"
exit $status
