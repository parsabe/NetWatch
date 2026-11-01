#!/bin/bash
# Horizon test campaign as a SLURM job.   sbatch cluster/horizon_campaign.sbatch
# Prepare once on the LOGIN node (internet):  ./run_campaign.sh --prepare
# The job pauses cleanly 15 min before its time limit and queues the next part itself, until the campaign is done.
#SBATCH --job-name=horizon
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --signal=B:TERM@900
#SBATCH --output=horizon-%j.out
# Uncomment / adapt for your university's cluster:
##SBATCH --partition=gpu
##SBATCH --account=<your-project>
##SBATCH --gres=gpu:a100:1

set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")/..}"
# module load cuda             # if your cluster needs it for the GPU driver libraries
export HF_HUB_OFFLINE=1         # compute nodes usually have no internet: everything was downloaded by --prepare
# export HF_HOME=/scratch/$USER/hf  OLLAMA_MODELS=/scratch/$USER/ollama   # if --prepare used shared scratch

nvidia-smi --query-gpu=name,memory.total --format=csv || true
./run_campaign.sh --profile "${PROFILE:-bounded}" "$@" &
PID=$!
trap 'kill -TERM "$PID" 2>/dev/null' TERM
wait "$PID"; rc=$?
if kill -0 "$PID" 2>/dev/null; then wait "$PID"; rc=$?; fi
if [ "$rc" -eq 3 ]; then
  echo "paused at the time limit → submitting the next part"
  sbatch cluster/horizon_campaign.sbatch "$@"
fi
exit "$rc"
