#!/bin/bash
# daily_autoresearch.sh — Run Karpathy-style autoresearch experiments daily
#
# Usage:
#   ./scripts/daily_autoresearch.sh              # default: 100 experiments in 10 batches
#   ./scripts/daily_autoresearch.sh 50           # custom total
#   ./scripts/daily_autoresearch.sh 100 5        # 100 experiments, batch size 5
#
# Agent harness (set via env or .env):
#   AGENT_HARNESS=opencode  ./scripts/daily_autoresearch.sh   # default
#   AGENT_HARNESS=claude    ./scripts/daily_autoresearch.sh
#   AGENT_HARNESS=codex     ./scripts/daily_autoresearch.sh
#   AGENT_HARNESS=cursor    ./scripts/daily_autoresearch.sh
#   AGENT_HARNESS=mock      ./scripts/daily_autoresearch.sh   # fake agent, tests pipeline
#
# Dry-run (prints what would execute, no agent calls):
#   DRY_RUN=true ./scripts/daily_autoresearch.sh 5 2
#
# Cron example (run at 6am UTC daily):
#   0 6 * * * cd /path/to/auto-researchtrading && AGENT_HARNESS=claude ./scripts/daily_autoresearch.sh >> data_pipeline/logs/daily_auto.log 2>&1

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Load .env if present
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

TOTAL=${1:-100}
BATCH_SIZE=${2:-10}
NUM_BATCHES=$(( (TOTAL + BATCH_SIZE - 1) / BATCH_SIZE ))

AGENT_HARNESS="${AGENT_HARNESS:-opencode}"
DRY_RUN="${DRY_RUN:-false}"

SUPPORTED_HARNESSES="opencode claude codex cursor mock"
if ! echo "$SUPPORTED_HARNESSES" | grep -qw "$AGENT_HARNESS"; then
    echo "ERROR: Unsupported AGENT_HARNESS='$AGENT_HARNESS'. Supported: $SUPPORTED_HARNESSES" >&2
    exit 1
fi

LOG_DIR="$REPO_ROOT/data_pipeline/logs"
mkdir -p "$LOG_DIR"
DATE_STR=$(date -u +%Y%m%d)
LOG_FILE="$LOG_DIR/daily_autoresearch_${DATE_STR}.log"

log() {
    echo "[$(date -u +%Y%m%dT%H%M%S)] $*" | tee -a "$LOG_FILE"
}

# ── Agent harness dispatch ─────────────────────────────────
agent_run() {
    local prompt="$1"
    if [ "$DRY_RUN" = "true" ] || [ "$AGENT_HARNESS" = "mock" ]; then
        log "[DRY-RUN/$AGENT_HARNESS] agent_run (batch start): ${prompt:0:100}..."
        return 0
    fi
    case "$AGENT_HARNESS" in
        opencode) opencode run "$prompt" ;;
        claude)    claude --bare -p "$prompt" --allowedTools "Bash,Read,Edit" ;;
        codex)     codex exec --full-auto "$prompt" ;;
        cursor)    cursor agent -p --force "$prompt" ;;
    esac
}

agent_continue() {
    local prompt="$1"
    if [ "$DRY_RUN" = "true" ] || [ "$AGENT_HARNESS" = "mock" ]; then
        log "[DRY-RUN/$AGENT_HARNESS] agent_continue (batch resume): ${prompt:0:100}..."
        return 0
    fi
    case "$AGENT_HARNESS" in
        opencode) opencode run -c "$prompt" ;;
        claude)    claude -c -p "$prompt" --allowedTools "Bash,Read,Edit" ;;
        codex)     codex exec resume --last "$prompt" ;;
        cursor)    cursor agent --resume --force -p "$prompt" ;;
    esac
}

# ── 1. Fresh data ──────────────────────────────────────────────
log "=== DAILY AUTORESEARCH START ==="
log "Config: total=$TOTAL, batch_size=$BATCH_SIZE, batches=$NUM_BATCHES, harness=$AGENT_HARNESS, dry_run=$DRY_RUN"

log "Downloading fresh data (6 months)..."
rm -rf ~/.cache/autotrader/data/
uv run python scripts/download_daily_data.py >> "$LOG_FILE" 2>&1
export DAILY_MODE=1
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

# Always start with fresh results.tsv — never accumulate across runs
echo -e "commit\tscore\tsharpe\tmax_dd\tstatus\tdescription" > results.tsv

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
- ALWAYS save to DB (both wins and losses):
  uv run python scripts/save_to_db.py run.log '<description>' PASS
  (use PASS if score improved over baseline, FAIL otherwise)
- ALWAYS revert after saving:
  git reset --hard HEAD~1
  (strategy.py must return to harness state for next experiment)
- Do NOT stop until you have completed $BATCH_COUNT experiments

Run $BATCH_COUNT experiments now. Do not ask questions. Be autonomous."
        agent_run "$PROMPT" >> "$LOG_FILE" 2>&1 || log "WARNING: Batch $batch exited with non-zero code"
    else
        PROMPT="Continue the autoresearch loop. Run $BATCH_COUNT more experiments.
Same rules — save EVERY experiment to DB, then ALWAYS revert.
Do not stop until $BATCH_COUNT experiments are done."
        agent_continue "$PROMPT" >> "$LOG_FILE" 2>&1 || log "WARNING: Batch $batch exited with non-zero code"
    fi

    log "Batch $batch done."
done

# ── 4. Promote best experiment ─────────────────────────────────
log "Promoting best PASS experiment to active..."
uv run python scripts/promote_best.py >> "$LOG_FILE" 2>&1 || log "WARNING: promote_best.py failed"

# ── 5. Summary ─────────────────────────────────────────────────
BEST=$(awk -F'\t' 'NR>1 && $5=="PASS"' results.tsv | sort -t$'\t' -k2 -rn | head -1)
log "=== DAILY AUTORESEARCH COMPLETE ==="
log "Best result: $BEST"
log "Branch: $BRANCH"
log "Results file: results.tsv"
