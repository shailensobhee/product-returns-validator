#!/bin/bash
# Re-measures this server's numbers for the comparison in the top bar and the "120B" markers. Run once on every new
# server (and again after changing the GPU, models, vLLM settings or the samples):
#
#   bench/baseline_27b.py   gpt-oss-120B alone runs every sample turn (policies, orders, chat and photo in one prompt)
#                           -> data/baseline_27b.json (time and tokens per turn, averages for typed-in turns)
#   bench/run_scenarios.py  the routed flow on every sample turn -> data/scenario_models.json (which gpt-oss model Vela
#                           picked for each turn: the "120B" markers in the left panel); fails if a turn's verdict changes
#
# Takes about 2 minutes. Nothing else should use the GPU meanwhile (that includes presenting the demo), or the times
# come out too high. The models must be up (deploy/up.sh). Restarts the demo server afterwards so it loads the files.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/common.sh

health=$(curl -s -m 60 "http://$HOST:$PORT/health" || true)
case "$health" in
  *'"ok": true'*|*'"ok":true'*) ;;
  "") echo "Demo server not answering on $HOST:$PORT; checking the Router directly." ;;
  *) die "not all parts are up: $health (run deploy/up.sh)" ;;
esac
curl -sf -m 3 "$ROUTER_URL/v1/models" >/dev/null || die "the Router does not answer at $ROUTER_URL (run deploy/up.sh)"

say "gpt-oss-120B alone on every sample turn (about 1 minute)"
.venv/bin/python -I bench/baseline_27b.py | tail -3

say "Routed flow on every sample turn (about 1 minute)"
.venv/bin/python -I bench/run_scenarios.py | tail -2

say "Done. Restarting the demo server so it loads the new numbers"
./start_server.sh --bg
