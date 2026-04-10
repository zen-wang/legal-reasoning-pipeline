#!/bin/bash
#SBATCH --job-name=scimon-gen
#SBATCH --partition=public
#SBATCH --account=class_cse59827694spring2026
#SBATCH --qos=class_general
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=6:00:00
#SBATCH --output=logs/generate_%j.log

# Usage: sbatch scripts/run_generate.sh <gaudi_host> <run_id>
# Example: sbatch scripts/run_generate.sh gaudi004 1

VLLM_HOST=${1:?"Usage: sbatch scripts/run_generate.sh <gaudi_host> <run_id>"}
RUN_ID=${2:?"Usage: sbatch scripts/run_generate.sh <gaudi_host> <run_id>"}

mkdir -p logs

echo "=== Generation job started ==="
echo "Host: $VLLM_HOST, Run ID: $RUN_ID"
echo "Time: $(date)"
echo "Node: $(hostname)"

# Use scimon-retrieval env (has openai + tqdm, lighter than gaudi env)
export PATH="/scratch/wwang360/envs/scimon-retrieval/bin:$PATH"
export PYTHONNOUSERSITE=1

cd /home/wwang360/CSE598/project2/SCIMON/scimon

# Wait for vLLM server to be ready
echo "Waiting for vLLM server on $VLLM_HOST:8000 ..."
for i in $(seq 1 120); do
    if curl -s --connect-timeout 5 "http://${VLLM_HOST}:8000/v1/models" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        echo "Server ready after ${i} attempts"
        break
    fi
    if [ $i -eq 120 ]; then
        echo "ERROR: Server not ready after 120 attempts (20 min), giving up"
        exit 1
    fi
    sleep 10
done

# Run baseline and semantic_neighbors in parallel
echo "=== Starting baseline run${RUN_ID} on ${VLLM_HOST} ==="
python3 scripts/generate_ideas.py --mode baseline --vllm-host "$VLLM_HOST" --run-id "$RUN_ID" &
PID_BASE=$!

echo "=== Starting semantic_neighbors run${RUN_ID} on ${VLLM_HOST} ==="
python3 scripts/generate_ideas.py --mode semantic_neighbors --vllm-host "$VLLM_HOST" --run-id "$RUN_ID" &
PID_SN=$!

echo "Baseline PID: $PID_BASE, SN PID: $PID_SN"

# Wait for both
wait $PID_BASE
RC_BASE=$?
echo "Baseline run${RUN_ID} finished with exit code $RC_BASE"

wait $PID_SN
RC_SN=$?
echo "SN run${RUN_ID} finished with exit code $RC_SN"

echo "=== All done at $(date) ==="
echo "Results:"
ls -la results_llama70b/baseline/run${RUN_ID}/
ls -la results_llama70b/semantic_neighbors/run${RUN_ID}/

exit $((RC_BASE + RC_SN))
