#!/bin/bash
# Stops what deploy/up.sh started (listed in deploy/.state), and nothing else: services that were already running
# when up.sh ran are left alone. --all also stops the parts up.sh did not start (prv-gptoss120b, prv-gptoss20b, the Router).
set -uo pipefail
cd "$(dirname "$0")/.."
source deploy/common.sh
ALL=0; [ "${1:-}" = "--all" ] && ALL=1
had() { [ $ALL = 1 ] || grep -qx "$1" "$STATE_FILE" 2>/dev/null; }
[ $ALL = 1 ] || [ -s "$STATE_FILE" ] || { echo "nothing recorded in $STATE_FILE: up.sh started nothing (use --all to force)"; exit 0; }

if had server; then
  PID=$(ss -ltnp 2>/dev/null | grep -E "[:.]$PORT " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [ -n "$PID" ] && grep -q "app/server.py" "/proc/$PID/cmdline" 2>/dev/null; then
    kill "$PID" && echo "stopped the demo server (pid $PID)"
  else
    echo "demo server not running on :$PORT"
  fi
fi
if had router; then
  (cd deploy && "${SR[@]}" stop) && echo "stopped the Router" || echo "Router stop failed (see above)"
fi
for n in gptoss120b gptoss20b; do
  if had "$n"; then
    $DOCKER rm -f "prv-$n" >/dev/null 2>&1 && echo "stopped prv-$n" || echo "prv-$n was not running"
  fi
done
rm -f "$STATE_FILE"
