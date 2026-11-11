#!/bin/bash
# Downloads the demo's models (resumable; run again after an interruption):
#   openai/gpt-oss-120b (~63 GB) and openai/gpt-oss-20b (~13 GB) into the Hugging Face cache, read by deploy/serve_gptoss.sh
#   vllm-sr/d3-mini (~8.5 GB) and vllm-sr/Vela-2.0-0.8B (~2 GB) into deploy/models/model-runtime, the cache the
#     Router's model runtime reads (the Router downloads them there itself on first start if they are missing;
#     fetching them here just makes that start quicker)
#
# Needs the `hf` CLI (installed into .venv by requirements.txt). If a model is gated on the hub, accept its terms
# there and export HF_TOKEN=<your token> (or run `hf auth login`) first. Never commit the token.
#
# Environment: HF_HOME (default $HOME/.cache/huggingface), HF (path of the hf CLI, default .venv/bin/hf or hf on PATH).
set -euo pipefail
cd "$(dirname "$0")/.."
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export HF_HUB_DISABLE_XET=1 HF_HUB_DOWNLOAD_TIMEOUT=60   # plain HTTP downloads; Xet transfers stalled on our hosts
unset HF_HUB_OFFLINE
HF="${HF:-}"
if [ -z "$HF" ]; then
  if [ -x .venv/bin/hf ]; then HF=.venv/bin/hf; elif command -v hf >/dev/null; then HF=hf; fi
fi
[ -n "$HF" ] || { echo "the hf CLI is missing: .venv/bin/pip install -r requirements.txt" >&2; exit 1; }

for m in openai/gpt-oss-120b openai/gpt-oss-20b; do
  echo "== $m -> $HF_HOME/hub"
  "$HF" download "$m"
done
mkdir -p deploy/models/model-runtime
for m in vllm-sr/d3-mini vllm-sr/Vela-2.0-0.8B; do
  echo "== $m -> deploy/models/model-runtime"
  "$HF" download "$m" --cache-dir deploy/models/model-runtime
done
echo "done. Disk: $(du -sh "$HF_HOME/hub" | cut -f1) in $HF_HOME/hub, $(du -sh deploy/models | cut -f1) in deploy/models"
