#!/bin/bash
# daily_autoresearch.sh — Run 100 Karpathy-style autoresearch experiments daily via OpenCode
#
# Usage:
#   ./scripts/daily_autoresearch.sh              # default: 100 experiments in 10 batches
#   ./scripts/daily_autoresearch.sh 50           # custom total
#   ./scripts/daily_autoresearch.sh 100 5        # 100 experiments, batch size 5
#
# Cron example (run at 6am UTC daily):
#   0 6 * * * cd /path/to/auto-researchtrading && ./scripts/daily_autoresearch.sh >> data_pipeline/logs/daily_auto.log 2>&1

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

TOTAL=${1:-100}
BATCH_SIZE=${2:-10}
NUM_BATCHES=$(( (TOTAL + BATCH_SIZE - 1) / BATCH_SIZE ))

LOG_DIR="$REPO_ROOT/data_pipeline/logs"
mkdir -p "$LOG_DIR"
DATE_STR=$(date -u +%Y%m%d)
LOG_FILE="$LOG_DIR/daily_autoresearch_${DATE_STR}.log"

log() {
    echo "[$(date -u +%Y%m%dT%H%M%S)] $*" | tee -a "$LOG_FILE"
}

# ── 1. Fresh data ──────────────────────────────────────────────
log "=== DAILY AUTORESEARCH START ==="
log "Config: total=$TOTAL, batch_size=$BATCH_SIZE, batches=$NUM_BATCHES"

log "Downloading fresh data..."
rm -rf ~/.cache/autotrader/data/
uv run prepare.py >> "$LOG_FILE" 2>&1
log "Data ready."

# ── 2. Git setup ───────────────────────────────────────────────
DATE_TAG=$(date -u +%b%d | tr '[:upper:]' '[:lower:]')
BRANCH="autotrader/${DATE_TAG}"

HARNESS_BRANCH="harness"

if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
    log "Branch $BRANCH exists — checking out."
    git checkout "$BRANCH" >> "$LOG_FILE" 2>&1
else
    log "Creating branch $BRANCH from $HARNESS_BRANCH."
    git checkout -b "$BRANCH" "$HARNESS_BRANCH" >> "$LOG_FILE" 2>&1
fi

if [ ! -f results.tsv ]; then
    echo -e "commit\tscore\tsharpe\tmax_dd\tstatus\tdescription" > results.tsv
fi

# ── 3. Run autoresearch in batches ────────────────────────────
for batch in $(seq 1 $NUM_BATCHES); do
    BATCH_START=$(( (batch - 1) * BATCH_SIZE + 1 ))
    BATCH_END=$(( batch * BATCH_SIZE ))
    # Last batch may be smaller
    if [ "$BATCH_END" -gt "$TOTAL" ]; then
        BATCH_END=$TOTAL
    fi
    BATCH_COUNT=$(( BATCH_END - BATCH_START + 1 ))

    log "Batch $batch/$NUM_BATCHES (experiments $BATCH_START-$BATCH_END, count=$BATCH_COUNT)..."

    if [ "$batch" -eq 1 ]; then
        PROMPT="You are running the daily autoresearch loop.

Read program.md for full instructions on the experiment loop.

Rules:
- Only edit strategy.py
- Run 'uv run backtest.py > run.log 2>&1' and parse results from run.log
- After each experiment, record in results.tsv
- If score IMPROVES (higher than best so far): keep the commit AND save to DB by running: uv run python scripts/save_to_db.py run.log '<description of change>'
- If score is equal or worse: git reset --hard HEAD~1
- Do NOT stop until you have completed $BATCH_COUNT experiments

Run $BATCH_COUNT experiments now. Do not ask questions. Be autonomous."
        opencode run "$PROMPT" >> "$LOG_FILE" 2>&1 || log "WARNING: Batch $batch exited with non-zero code"
    else
        PROMPT="Continue the autoresearch loop. Run $BATCH_COUNT more experiments.
Same rules as before — save to DB after each improvement via: uv run python scripts/save_to_db.py run.log '<description>'
Do not stop until $BATCH_COUNT experiments are done."
        opencode run -c "$PROMPT" >> "$LOG_FILE" 2>&1 || log "WARNING: Batch $batch exited with non-zero code"
    fi

    log "Batch $batch done."
done

# ── 4. Summary ─────────────────────────────────────────────────
BEST=$(tail -n +2 results.tsv | sort -t$'\t' -k2 -rn | head -1)
log "=== DAILY AUTORESEARCH COMPLETE ==="
log "Best result: $BEST"
log "Branch: $BRANCH"
log "Results file: results.tsv"
