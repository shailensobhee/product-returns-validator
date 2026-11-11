#!/bin/bash
# Starts one gpt-oss vLLM server (ROCm, Docker) for the returns validator demo. Usage: deploy/serve_gptoss.sh <gptoss120b|gptoss20b>
#
#   gptoss120b  openai/gpt-oss-120b  127.0.0.1:8102  hard turns (disputes, several issues in one turn, upset customers)
#   gptoss20b   openai/gpt-oss-20b   127.0.0.1:8103  claim extraction and normal replies
#
# Both are OpenAI open-weight models (Apache-2.0, MXFP4), text-only, served through one AMD Instinct MI300X. The
# container joins the vllm-sr-network Docker network when it exists, so the Router reaches it by container name
# (prv-gptoss120b / prv-gptoss20b, as in deploy/router.yaml). Models are read from the Hugging Face cache, offline
# (run deploy/download_models.sh first).
#
# Environment: DOCKER (default \"docker\"), HF_HOME (default \$HOME/.cache/huggingface), GPU_UTIL (vLLM memory share,
#   default 0.55 for gptoss120b, 0.14 for gptoss20b), GPU (HIP device, default 0), AITER (AMD AITER kernels, default 1),
#   CPUSET (optional --cpuset-cpus), IMAGE (vLLM image, default vllm/vllm-openai-rocm:v0.29.0).
set -euo pipefail
DOCKER=\"\${DOCKER:-docker}\"
HF_HOME=\"\${HF_HOME:-\$HOME/.cache/huggingface}\"
IMAGE=\"\${IMAGE:-vllm/vllm-openai-rocm:v0.29.0}\"
NETWORK=vllm-sr-network

case \"\${1:-}\" in
  gptoss120b) MODEL=openai/gpt-oss-120b; PORT=8102; UTIL=\"\${GPU_UTIL:-0.55}\" ;;
  gptoss20b)  MODEL=openai/gpt-oss-20b;  PORT=8103; UTIL=\"\${GPU_UTIL:-0.14}\" ;;
  *) echo \"usage: \$0 <gptoss120b|gptoss20b>\" >&2; exit 2 ;;
esac
NAME=\"prv-\$1\"

[ -d \"\$HF_HOME/hub\" ] || { echo \"no Hugging Face cache at \$HF_HOME/hub; run deploy/download_models.sh first\" >&2; exit 1; }
NET=()
if \$DOCKER network inspect \"\$NETWORK\" >/dev/null 2>&1; then NET=(--network \"\$NETWORK\"); fi
CPU=()
if [ -n \"\${CPUSET:-}\" ]; then CPU=(--cpuset-cpus \"\$CPUSET\"); fi

\$DOCKER rm -f \"\$NAME\" >/dev/null 2>&1 || true
\$DOCKER run -d --name \"\$NAME\" \\
  --device /dev/kfd --device /dev/dri --group-add video --ipc host \\
  --security-opt seccomp=unconfined --security-opt label=disable \\
  \"\${CPU[@]}\" \"\${NET[@]}\" \\
  -e HF_HUB_OFFLINE=1 -e VLLM_ROCM_USE_AITER=\"\${AITER:-1}\" -e HIP_VISIBLE_DEVICES=\"\${GPU:-0}\" \\
  -v \"\$HF_HOME:/root/.cache/huggingface:ro\" \\
  -p \"127.0.0.1:\$PORT:8000\" \\
  \"\$IMAGE\" \"\$MODEL\" \\
  --served-model-name \"\$1\" --max-model-len 32768 --max-num-seqs 16 \\
  --gpu-memory-utilization \"\$UTIL\" --seed 0 >/dev/null
echo \"started \$NAME (\$MODEL) on 127.0.0.1:\$PORT, gpu-memory-utilization \$UTIL\${NET:+, on \$NETWORK}\"
