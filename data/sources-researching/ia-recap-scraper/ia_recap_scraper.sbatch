#!/bin/bash

#SBATCH --account=class_cse57388551fall2025
#SBATCH -N 1                # number of nodes
#SBATCH -c 1                # number of cores
#SBATCH -t 0-03:00:00       # time in d-hh:mm:ss
#SBATCH -p class            # partition
#SBATCH -q class            # QOS
#SBATCH --mem=8G            # memory (no GPU needed, just network I/O)
#SBATCH -o slurm.%j.out     # file to save job's STDOUT (%j = JobId)
#SBATCH -e slurm.%j.err     # file to save job's STDERR (%j = JobId)
#SBATCH --mail-type=ALL     # Send an e-mail when a job starts, stops, or fails
#SBATCH --mail-user="wwang360@asu.edu"
#SBATCH --export=NONE       # Purge the job-submitting shell environment

# Load required software
module load mamba/latest

source activate tau

echo "Starting at $(date)"
echo "Python: $(python --version)"
echo "curl: $(curl --version | head -1)"
python -c "import sqlite3; print('sqlite3 OK')"

# Scrape: broad securities search, 200 results per query, 3 workers
python ia_recap_scraper.py scrape --all --max 200 --workers 3

# Show status
python ia_recap_scraper.py status

# Export both formats
python ia_recap_scraper.py export --format both -o ia_recap_cases

echo "Completed at $(date)"
