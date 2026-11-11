#!/bin/bash
# Brings the whole demo up on one AMD Instinct GPU: gpt-oss-120B and gpt-oss-20B (vLLM containers), the vllm-sr Router with
# d3-mini and Vela 2.0, then the demo server (./start_server.sh --bg). Run from anywhere; takes ~5-15 minutes, most of
# it model loading. Re-running is safe: parts that are already up and healthy are left alone.
#
# Environment (all optional): DOCKER ("sudo docker" where Docker needs sudo), VLLM_SR (vllm-sr CLI path),
# GPU (GPU index, default 0), HF_HOME, HOST/PORT (demo server, default 127.0.0.1:8602), GPU_USD_PER_HOUR,
# plus GPU_UTIL_120B / GPU_UTIL_20B (vLLM memory shares, default 0.62 / 0.13).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/common.sh
UTIL_120B="${GPU_UTIL_120B:-0.55}"
UTIL_20B="${GPU_UTIL_20B:-0.14}"
SR_GIB=28   # rough GPU memory for the Router's model runtime: d3-mini (~8.5 GB) and Vela 2.0 0.8B plus working memory

say "Checks"
command -v rocm-smi >/dev/null || die "rocm-smi not found: install ROCm (the AMD GPU driver stack) first"
$DOCKER info >/dev/null 2>&1 || die "'$DOCKER info' failed: install Docker, or set DOCKER=\"sudo docker\" if it needs sudo"
command -v "$VLLM_SR" >/dev/null || die "vllm-sr CLI not found at $VLLM_SR (see README.md: python3 -m venv .venv-sr ...), or set VLLM_SR"
[ -x .venv/bin/python ] || die ".venv missing: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub"
for m in openai--gpt-oss-120b openai--gpt-oss-20b; do
  [ -d "$HUB/models--$m" ] || die "model $m not in $HUB: run deploy/download_models.sh"
done
touch "$STATE_FILE"
started() { grep -qx "$1" "$STATE_FILE" || echo "$1" >> "$STATE_FILE"; }

need_120b=1; need_20b=1; need_router=1
curl -sf -m 3 http://127.0.0.1:8102/health >/dev/null && need_120b=0 && echo "gpt-oss-120B already answers on :8102, leaving it"
curl -sf -m 3 http://127.0.0.1:8103/health >/dev/null && need_20b=0 && echo "gpt-oss-20B already answers on :8103, leaving it"
curl -sf -m 3 "$ROUTER_URL/v1/models" >/dev/null && need_router=0 && echo "Router already answers at $ROUTER_URL, leaving it"
[ $need_120b = 1 ] && port_busy 8102 && die "port 8102 is in use by something that is not a healthy gpt-oss-120B"
[ $need_20b = 1 ] && port_busy 8103 && die "port 8103 is in use by something that is not a healthy gpt-oss-20B"
if [ $need_router = 1 ]; then
  port_busy 8899 && die "port 8899 is in use by something that is not a healthy Router"
  port_busy 8080 && die "port 8080 (the Router's management API) is in use by something else"
  container_exists "$ROUTER_CONTAINER" && die "a vllm-sr Router stack ($ROUTER_CONTAINER) already exists on this host; \
vllm-sr runs one per host. Stop it ('$VLLM_SR stop') if it is not needed, or reuse it with deploy/router.yaml's \
deployments, models, entry point and recipe added to its config."
fi

# Free GPU memory: vLLM's --gpu-memory-utilization is a share of the whole GPU, and the Router needs room for its models.
read -r total free < <(gpu_mem) || die "could not read GPU $GPU memory from rocm-smi"
need=$(awk -v t="$total" -v a="$UTIL_120B" -v b="$UTIL_20B" -v n27="$need_120b" -v n9="$need_20b" -v nr="$need_router" -v d="$SR_GIB" \
  'BEGIN{printf "%.0f", t*(a*n27 + b*n9) + nr*d*2^30}')
echo "GPU $GPU: $((free >> 30)) GiB free of $((total >> 30)) GiB, about $((need >> 30)) GiB needed"
[ "$free" -ge "$need" ] || die "not enough free GPU memory. Stop other GPU work, or lower GPU_UTIL_120B/GPU_UTIL_20B (less KV cache)."

# The Router reaches the vLLM containers by name on this network; vllm-sr reuses it when it exists.
$DOCKER network inspect vllm-sr-network >/dev/null 2>&1 || $DOCKER network create vllm-sr-network >/dev/null

# One vLLM server at a time: each measures the GPU's free memory while it loads, so two loading together on one GPU
# can leave the second with no room for its KV cache ("Engine core initialization failed").
if [ $need_120b = 1 ]; then
  say "Starting gpt-oss-120B (prv-gptoss120b, :8102; ~3-5 minutes, the first start compiles kernels and takes longer)"
  GPU_UTIL="$UTIL_120B" deploy/serve_gptoss.sh gptoss120b; started gptoss120b
  wait_http http://127.0.0.1:8102/health 1800 prv-gptoss120b GPU_UTIL_120B || die "gpt-oss-120B did not come up"
fi
echo "gpt-oss-120B ready"
if [ $need_20b = 1 ]; then
  say "Starting gpt-oss-20B (prv-gptoss20b, :8103; ~2-3 minutes)"
  need_free "$(awk -v t="$total" -v b="$UTIL_20B" -v nr="$need_router" -v d="$SR_GIB" 'BEGIN{printf "%.0f", t*b + nr*d*2^30}')" \
    "gpt-oss-20B$([ "$need_router" = 1 ] && echo " and the Router's models")"
  GPU_UTIL="$UTIL_20B" deploy/serve_gptoss.sh gptoss20b; started gptoss20b
  wait_http http://127.0.0.1:8103/health 1800 prv-gptoss20b GPU_UTIL_20B || die "gpt-oss-20B did not come up"
fi
echo "gpt-oss-20B ready"

if [ $need_router = 1 ]; then
  say "Starting the vllm-sr Router with d3-mini and Vela 2.0 (:8899, management API :8080; the first start downloads \
and compiles, up to ~30 minutes)"
  need_free $((SR_GIB << 30)) "the Router's d3-mini and Vela 2.0"
  # Run from deploy/: vllm-sr keeps its state (.vllm-sr/) and model cache (models/) next to the config file.
  (cd deploy && "${SR[@]}" serve --config router.yaml --platform rocm --minimal --replace-active-config)
  started router
fi
wait_http "$ROUTER_URL/v1/models" 300 || die "the Router does not answer at $ROUTER_URL"
echo "Router ready"

say "Starting the demo server"
curl -sf -m 3 "http://$HOST:$PORT/health" >/dev/null && echo "demo server already running on $HOST:$PORT (restarting it)"
./start_server.sh --bg >/dev/null
started server
for _ in $(seq 12); do curl -s -m 3 "http://$HOST:$PORT/" >/dev/null && break; sleep 2; done
# /health probes every part the way the flow calls it (the routing preview for returns/auto, D3, both gpt-oss models);
# allow a few rounds while the Router's models warm up.
health=""
for _ in $(seq 24); do
  health=$(curl -s -m 60 "http://$HOST:$PORT/health" || true)
  case "$health" in *'"ok": true'*|*'"ok":true'*) break ;; esac
  sleep 5
done
echo "health: $health"
case "$health" in *'"ok": true'*|*'"ok":true'*) ;; *) die "health is not ok: the false entry names the part that is down (detail says why)" ;; esac

say "Up. Open http://$HOST:$PORT (from a laptop: ssh -L $PORT:localhost:$PORT <user>@<this server>)"
echo "First start on this server? Run deploy/measure.sh once (~2 min) so the comparison uses this GPU's numbers."
echo "Next: deploy/smoke_test.sh"
