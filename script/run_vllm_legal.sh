#!/bin/bash
#
# run_vllm_legal.sh — Launch vLLM for legal IRAC extraction pipeline
#
# Based on doc/llama-gaudi-reference/run_vllm_gaudi.sh with two changes:
#   --max-model-len 16384  (was 2048 — opinions average 50K chars / ~12K tokens)
#   --max-num-seqs 4       (was 16 — reduced to compensate memory for longer context)
#
# Usage on Sol:
#   sbatch script/run_vllm_legal.sh
#   squeue -u $USER                      # find the node hostname
#   # Then from another node:
#   python3 -m script.lift_opinions --db data/private_10b5_sample_416.db --llm-url http://<hostname>:8000 --limit 5
#
#SBATCH --job-name=legal-llama70b
#SBATCH --partition=gaudi
#SBATCH --account=class_cse59827694spring2026
#SBATCH --qos=class_gaudi
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:hl225:8
#SBATCH --cpus-per-task=64
#SBATCH --mem=320G
#SBATCH --time=24:00:00
#SBATCH --output=logs/vllm_legal_%j.log

mkdir -p logs
rm -rf .graph_dumps/

export PYTHONNOUSERSITE=1
export PATH="/packages/envs/gaudi-pytorch-vllm/bin:$PATH"
export TRANSFORMERS_CACHE=/data/datasets/community/huggingface/
export HF_HOME=/data/datasets/community/huggingface/

# GC_KERNEL_PATH auto-detect
TPC_LIB=$(find /usr/lib /opt /packages/envs/gaudi-pytorch-vllm -name "libtpc_kernels.so" 2>/dev/null | head -1)
if [ -n "$TPC_LIB" ]; then
    export GC_KERNEL_PATH="$TPC_LIB"
    echo "GC_KERNEL_PATH set to: $GC_KERNEL_PATH"
fi

# Memory tuning
export VLLM_GRAPH_RESERVED_MEM=0.1
export VLLM_PROMPT_USE_FUSEDSDPA=1
export PT_HPU_ENABLE_LAZY_COLLECTIVES=true
export VLLM_PROMPT_BS_BUCKET_MIN=1
export VLLM_PROMPT_BS_BUCKET_STEP=16
export VLLM_PROMPT_BS_BUCKET_MAX=16
export PT_HPU_LAZY_MODE=0
export HABANA_VISIBLE_DEVICES=all
export VLLM_ENGINE_ITERATION_TIMEOUT_S=3600
export VLLM_RPC_TIMEOUT=100000

MODEL="/data/datasets/community/huggingface/models--meta-llama--Llama-3.3-70B-Instruct/snapshots/6f6073b423013f6a7d4d9f39144961bfbfbc386b"

echo "Starting vLLM (legal pipeline) on $(hostname) at $(date)"
echo "Model: $MODEL"
echo "Context: 8192 tokens | Max seqs: 16"
echo "Port: 8000"

hl-smi 2>/dev/null || echo "hl-smi not available"

python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --dtype bfloat16 \
    --block-size 128 \
    --tensor-parallel-size 8 \
    --max-model-len 8192 \
    --max-num-seqs 16 \
    --port 8000 \
    --host 0.0.0.0
