#!/bin/bash
#
# run_vllm_gaudi.sh — Launch vLLM server for Llama-3.3-70B-Instruct on Intel Gaudi 2
#
# This script was designed through iterative debugging to work around several
# Gaudi 2 + vLLM compatibility issues encountered on the ASU Sol cluster:
#
# 1. User-local Python packages (especially transformers) shadow the Gaudi env,
#    causing import failures. Fixed with PYTHONNOUSERSITE=1.
#
# 2. The HuggingFace gated repo for Llama 3.3 returns 401 even with a valid
#    token in SLURM jobs. Fixed by pointing directly to the local snapshot path
#    instead of the model name.
#
# 3. The Habana graph compiler fails with "synStatus 26 [Generic failure]" when
#    GC_KERNEL_PATH is not set. The TPC kernel library exists on compute nodes
#    but is not exported by default. Fixed with auto-detection at runtime.
#
# 4. The 70B model OOMs on 8x Gaudi 2 (768 GB total HBM) without memory tuning.
#    Fixed by reserving graph memory (VLLM_GRAPH_RESERVED_MEM=0.1), enabling
#    fused SDPA, and reducing max-model-len to 2048.
#
# 5. Debug logging (LOG_LEVEL_ALL_PT=1) generated 37GB+ log files that filled
#    the home directory. Disabled after initial debugging.
#
# The server exposes an OpenAI-compatible API on port 8000, consumed by
# generate_ideas.py and run_novelty_boosting.py on separate compute nodes.
#
#SBATCH --job-name=scimon-llama70b
#SBATCH --partition=gaudi
#SBATCH --account=class_cse59827694spring2026
#SBATCH --qos=class_gaudi
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:hl225:8
#SBATCH --cpus-per-task=64
#SBATCH --mem=320G
#SBATCH --time=24:00:00
#SBATCH --output=logs/vllm_%j.log

mkdir -p logs

# Clean stale graph dumps
rm -rf .graph_dumps/

# Prevent user-local packages from shadowing the Gaudi env
export PYTHONNOUSERSITE=1

# Activate the ASU-provided Gaudi+vLLM environment
export PATH="/packages/envs/gaudi-pytorch-vllm/bin:$PATH"

# Set HuggingFace cache so model name resolution works
export TRANSFORMERS_CACHE=/data/datasets/community/huggingface/
export HF_HOME=/data/datasets/community/huggingface/

# --- FIX 1: GC_KERNEL_PATH (most common cause of synStatus 26) ---
# Auto-detect libtpc_kernels.so on the compute node
TPC_LIB=$(find /usr/lib /opt /packages/envs/gaudi-pytorch-vllm -name "libtpc_kernels.so" 2>/dev/null | head -1)
if [ -n "$TPC_LIB" ]; then
    export GC_KERNEL_PATH="$TPC_LIB"
    echo "GC_KERNEL_PATH set to: $GC_KERNEL_PATH"
else
    echo "WARNING: libtpc_kernels.so not found, GC_KERNEL_PATH unset"
fi

# --- FIX 2: Memory tuning for 70B (OOM can masquerade as graph compile failure) ---
export VLLM_GRAPH_RESERVED_MEM=0.1
export VLLM_PROMPT_USE_FUSEDSDPA=1
export PT_HPU_ENABLE_LAZY_COLLECTIVES=true
export VLLM_PROMPT_BS_BUCKET_MIN=1
export VLLM_PROMPT_BS_BUCKET_STEP=16
export VLLM_PROMPT_BS_BUCKET_MAX=16

# --- FIX 3: Use torch.compile mode (default, not eager) ---
# PT_HPU_LAZY_MODE=0 + no --enforce-eager = torch.compile mode
export PT_HPU_LAZY_MODE=0
export HABANA_VISIBLE_DEVICES=all

# Longer timeouts for large model init
export VLLM_ENGINE_ITERATION_TIMEOUT_S=3600
export VLLM_RPC_TIMEOUT=100000

# Debug logging (disabled — was generating 37GB+ logs)
# export LOG_LEVEL_ALL_PT=1
# export ENABLE_CONSOLE=true

# Model path (use direct snapshot path to avoid HF gated repo auth)
MODEL="/data/datasets/community/huggingface/models--meta-llama--Llama-3.3-70B-Instruct/snapshots/6f6073b423013f6a7d4d9f39144961bfbfbc386b"

echo "Starting vLLM server on $(hostname) at $(date)"
echo "Model: $MODEL"
echo "Port: 8000"
echo "Mode: torch.compile (PT_HPU_LAZY_MODE=0, no --enforce-eager)"

# Diagnostics
hl-smi 2>/dev/null || echo "hl-smi not available"
python -c "import habana_frameworks.torch; print('Habana version:', habana_frameworks.torch.__version__)" 2>/dev/null

# Start vLLM server with Llama 3.3-70B-Instruct on 8x Gaudi HL-225
# NOTE: --enforce-eager removed to use torch.compile mode instead
python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --dtype bfloat16 \
    --block-size 128 \
    --tensor-parallel-size 8 \
    --max-model-len 2048 \
    --max-num-seqs 16 \
    --port 8000 \
    --host 0.0.0.0
