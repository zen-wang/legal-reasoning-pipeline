#!/bin/bash
#SBATCH --job-name=scimon-llama70b-sif
#SBATCH --partition=gaudi
#SBATCH --account=class_cse59827694spring2026
#SBATCH --qos=class_gaudi
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:hl225:8
#SBATCH --cpus-per-task=64
#SBATCH --mem=320G
#SBATCH --time=24:00:00
#SBATCH --output=logs/vllm_sif_%j.log
#SBATCH --exclude=gaudi003,gaudi005

mkdir -p logs

# Clean stale graph dumps
rm -rf .graph_dumps/

MODEL="/data/datasets/community/huggingface/models--meta-llama--Llama-3.3-70B-Instruct/snapshots/6f6073b423013f6a7d4d9f39144961bfbfbc386b"
SIF="/packages/apps/simg/vllm-nightly-26.03.19.sif"

echo "Starting vLLM via Singularity on $(hostname) at $(date)"
echo "Container: $SIF"
echo "Model: $MODEL"
echo "Port: 8000"

# Run vLLM inside the singularity container with proper device binds
apptainer exec \
    --bind /dev:/dev \
    --bind /sys/class/accel:/sys/class/accel \
    --bind /sys/kernel/debug:/sys/kernel/debug \
    --bind /data:/data \
    --bind /packages:/packages \
    "$SIF" \
    bash -c "
        export PYTHONNOUSERSITE=1
        export HABANA_VISIBLE_DEVICES=all
        export PT_HPU_LAZY_MODE=0
        export VLLM_GRAPH_RESERVED_MEM=0.1
        export VLLM_PROMPT_USE_FUSEDSDPA=1
        export PT_HPU_ENABLE_LAZY_COLLECTIVES=true
        export VLLM_ENGINE_ITERATION_TIMEOUT_S=3600
        export VLLM_RPC_TIMEOUT=100000
        export TRANSFORMERS_CACHE=/data/datasets/community/huggingface/
        export HF_HOME=/data/datasets/community/huggingface/

        echo 'GC_KERNEL_PATH='\$GC_KERNEL_PATH
        hl-smi 2>/dev/null || echo 'hl-smi not available'

        python -m vllm.entrypoints.openai.api_server \
            --model '$MODEL' \
            --dtype bfloat16 \
            --block-size 128 \
            --tensor-parallel-size 8 \
            --max-model-len 2048 \
            --max-num-seqs 16 \
            --port 8000 \
            --host 0.0.0.0
    "
