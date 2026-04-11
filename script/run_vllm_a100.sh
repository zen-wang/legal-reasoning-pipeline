#!/bin/bash
#
# run_vllm_a100.sh — Launch vLLM on A100 GPUs for legal analysis pipeline
#
# Requires 4x A100-80GB (320GB total for 70B model + KV cache).
# No warmup delay — CUDA uses pre-compiled kernels.
#
# Usage on Sol:
#   sbatch script/run_vllm_a100.sh
#   squeue -u $USER                      # find the node hostname
#   # Then from another A100 node:
#   PYTHONNOUSERSITE=1 python -m script.analyze_case \
#       --db data/private_10b5_sample_416.db \
#       --docket-id 6135547 \
#       --llm-url http://<hostname>:8000 \
#       --neo4j-uri none
#
# Interactive mode (for testing):
#   salloc -c 32 -N 1 -t 0-02:00 -p general -q class \
#       -A class_cse57388551fall2025 --mem=128G --gres=gpu:a100:4
#   conda activate legal
#   bash script/run_vllm_a100.sh    # run directly (not sbatch)
#
#SBATCH --job-name=legal-llama70b-a100
#SBATCH --partition=general
#SBATCH --account=class_cse57388551fall2025
#SBATCH --qos=class
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:a100:4
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=02:00:00
#SBATCH --output=logs/vllm_a100_%j.log

mkdir -p logs

export PYTHONNOUSERSITE=1
export TRANSFORMERS_CACHE=/data/datasets/community/huggingface/
export HF_HOME=/data/datasets/community/huggingface/

MODEL="/data/datasets/community/huggingface/models--meta-llama--Llama-3.3-70B-Instruct/snapshots/6f6073b423013f6a7d4d9f39144961bfbfbc386b"

echo "Starting vLLM (A100) on $(hostname) at $(date)"
echo "Model: $MODEL"
echo "GPUs: 4x A100-80GB | Context: 8192 tokens | Max seqs: 4"
echo "Port: 8000"

nvidia-smi

python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --dtype bfloat16 \
    --tensor-parallel-size 4 \
    --max-model-len 8192 \
    --max-num-seqs 4 \
    --port 8000 \
    --host 0.0.0.0
