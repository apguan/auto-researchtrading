#!/usr/bin/env python3
"""Detect divergence between live and dry-run trading bots, alert on Telegram,
and optionally launch a headless Claude Code session to investigate.

Designed to run as a Railway cron service every hour. Single-shot: queries
Supabase, computes metrics, alerts/triggers if anything is over threshold,
exits.

Usage:
    python scripts/monitor_divergence.py                    # check + maybe alert
    python scripts/monitor_divergence.py --dry-run          # compute, print, no alerts
    python scripts/monitor_divergence.py --window-hours 2   # custom window
    python scripts/monitor_divergence.py --no-claude        # alert only, skip Claude

Required env vars:
    SUPABASE_DB_URL       — Postgres connection string
    TELEGRAM_BOT_TOKEN    — for alerts
    TELEGRAM_CHAT_ID      — for alerts
    ANTHROPIC_API_KEY     — for headless Claude (optional, only if --no-claude not set)

The script writes a structured incident report to /tmp/incident.md (or
$INCIDENT_PATH) before invoking Claude, so the headless session has the full
context without re-querying everything.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore
import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
INCIDENT_PATH = Path(os.environ.get("INCIDENT_PATH", "/tmp/incident.md"))


# ────────────────────────────────────────────────────────────────────
# Thresholds — what counts as "diverged enough to alert"
# ────────────────────────────────────────────────────────────────────


@dataclass
class Thresholds:
    # Symbol overlap: fraction of symbols traded by BOTH live and dry.
    # Below this means they're trading different things.
    min_symbol_overlap: float = 0.5

    # Trade count ratio: live_trades / dry_trades. Outside [low, high] = diverged.
    trade_count_ratio_low: float = 0.5
    trade_count_ratio_high: float = 2.0

    # Sync-clear duplicate rate: fraction of entries that are consecutive
    # entries (no exit between them) in the live bot. Above this means
    # the sync race is back.
    max_live_sync_clear_rate: float = 0.10

    # Direction agreement: when both bots traded the same symbol, fraction
    # of bars where they agreed on direction. Below this is divergence.
    min_direction_agreement: float = 0.7


# ────────────────────────────────────────────────────────────────────
# Metrics computation
# ────────────────────────────────────────────────────────────────────


@dataclass
class Metrics:
    window_hours: float
    window_start: datetime
    window_end: datetime
    live_trade_count: int = 0
    dry_trade_count: int = 0
    live_entries: int = 0
    dry_entries: int = 0
    live_exits: int = 0
    dry_exits: int = 0
    live_sync_clears: int = 0  # consecutive entries
    dry_sync_clears: int = 0
    live_realized_pnl: float = 0.0
    dry_realized_pnl: float = 0.0
    live_fees: float = 0.0
    dry_fees: float = 0.0
    live_symbols: list = field(default_factory=list)
    dry_symbols: list = field(default_factory=list)
    overlap_symbols: list = field(default_factory=list)
    symbol_overlap_pct: float = 0.0
    trade_count_ratio: float = 0.0
    live_sync_clear_rate: float = 0.0
    direction_agreement: float = 1.0
    direction_disagreements: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["window_start"] = self.window_start.isoformat()
        d["window_end"] = self.window_end.isoformat()
        return d


def _consec_entry_count(trades: list) -> int:
    """Count entries immediately followed by another entry (no exit in between)
    for the same symbol. Each such pair indicates a sync clear / lost-position event."""
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t["symbol"]].append(t)
    consec = 0
    for sym in by_sym:
        ts = by_sym[sym]
        for i in range(len(ts) - 1):
            if ts[i]["pnl"] is None and ts[i + 1]["pnl"] is None:
                consec += 1
    return consec


async def _fetch_trades(conn: asyncpg.Connection, since: datetime) -> tuple[list, list]:
    rows = await conn.fetch(
        """
        SELECT timestamp, symbol, side, size, price, fee, pnl, order_id
        FROM trades
        WHERE timestamp >= $1
        ORDER BY timestamp ASC
        """,
        since,
    )
    live, dry = [], []
    for r in rows:
        record = {
            "timestamp": r["timestamp"],
            "symbol": r["symbol"],
            "side": r["side"],
            "size": float(r["size"]),
            "price": float(r["price"]),
            "fee": float(r["fee"]),
            "pnl": float(r["pnl"]) if r["pnl"] is not None else None,
            "order_id": r["order_id"],
        }
        if r["order_id"] and str(r["order_id"]).startswith("dry-"):
            dry.append(record)
        else:
            live.append(record)
    return live, dry


def _compute_direction_agreement(live: list, dry: list) -> tuple[float, list]:
    """For symbols traded by both bots, what fraction of bars agreed on direction?

    Buckets trades into 1-hour windows; for each (window, symbol), takes the
    last entry's direction (long=+1 if buy, short=-1 if sell). Compares.
    """
    def bucket(t: dict) -> tuple[str, str]:
        ts = t["timestamp"]
        return (ts.strftime("%Y-%m-%d %H:00"), t["symbol"])

    def direction(t: dict) -> int:
        if t["pnl"] is not None:
            return 0  # exits don't count for direction
        return 1 if t["side"] == "buy" else -1

    live_dirs: dict[tuple[str, str], int] = {}
    for t in live:
        d = direction(t)
        if d != 0:
            live_dirs[bucket(t)] = d

    dry_dirs: dict[tuple[str, str], int] = {}
    for t in dry:
        d = direction(t)
        if d != 0:
            dry_dirs[bucket(t)] = d

    common_keys = set(live_dirs.keys()) & set(dry_dirs.keys())
    if not common_keys:
        return 1.0, []  # nothing to compare → no disagreement

    agree = 0
    disagreements = []
    for k in sorted(common_keys):
        if live_dirs[k] == dry_dirs[k]:
            agree += 1
        else:
            disagreements.append(
                {
                    "window": k[0],
                    "symbol": k[1],
                    "live_dir": "long" if live_dirs[k] > 0 else "short",
                    "dry_dir": "long" if dry_dirs[k] > 0 else "short",
                }
            )

    return agree / len(common_keys), disagreements[:10]  # cap to 10 examples


async def compute_metrics(window_hours: float) -> Metrics:
    db_url = os.environ["SUPABASE_DB_URL"]
    conn = await asyncpg.connect(db_url)
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=window_hours)

        live, dry = await _fetch_trades(conn, start)

        m = Metrics(window_hours=window_hours, window_start=start, window_end=end)
        m.live_trade_count = len(live)
        m.dry_trade_count = len(dry)
        m.live_entries = sum(1 for t in live if t["pnl"] is None)
        m.dry_entries = sum(1 for t in dry if t["pnl"] is None)
        m.live_exits = m.live_trade_count - m.live_entries
        m.dry_exits = m.dry_trade_count - m.dry_entries
        m.live_sync_clears = _consec_entry_count(live)
        m.dry_sync_clears = _consec_entry_count(dry)
        m.live_realized_pnl = sum(t["pnl"] for t in live if t["pnl"] is not None)
        m.dry_realized_pnl = sum(t["pnl"] for t in dry if t["pnl"] is not None)
        m.live_fees = sum(t["fee"] for t in live)
        m.dry_fees = sum(t["fee"] for t in dry)

        live_syms = {t["symbol"] for t in live}
        dry_syms = {t["symbol"] for t in dry}
        m.live_symbols = sorted(live_syms)
        m.dry_symbols = sorted(dry_syms)
        m.overlap_symbols = sorted(live_syms & dry_syms)

        union = live_syms | dry_syms
        m.symbol_overlap_pct = len(live_syms & dry_syms) / len(union) if union else 1.0
        m.trade_count_ratio = (
            m.live_trade_count / m.dry_trade_count if m.dry_trade_count > 0 else 0.0
        )
        m.live_sync_clear_rate = (
            m.live_sync_clears / m.live_entries if m.live_entries > 0 else 0.0
        )
        m.direction_agreement, m.direction_disagreements = _compute_direction_agreement(
            live, dry
        )

        return m
    finally:
        await conn.close()


# ────────────────────────────────────────────────────────────────────
# Threshold checks
# ────────────────────────────────────────────────────────────────────


@dataclass
class Issue:
    code: str
    severity: str  # "warn" or "alert"
    message: str


def evaluate(m: Metrics, t: Thresholds) -> list[Issue]:
    issues = []

    # Skip checks if there's basically no data
    if m.live_trade_count == 0 and m.dry_trade_count == 0:
        return issues

    if m.symbol_overlap_pct < t.min_symbol_overlap:
        issues.append(
            Issue(
                code="SYMBOL_OVERLAP_LOW",
                severity="alert",
                message=(
                    f"Live and dry traded different symbols. "
                    f"Overlap: {m.symbol_overlap_pct:.0%} (threshold: {t.min_symbol_overlap:.0%}). "
                    f"Live: {m.live_symbols}. Dry: {m.dry_symbols}."
                ),
            )
        )

    if m.dry_trade_count > 0:
        if (
            m.trade_count_ratio < t.trade_count_ratio_low
            or m.trade_count_ratio > t.trade_count_ratio_high
        ):
            issues.append(
                Issue(
                    code="TRADE_COUNT_DIVERGED",
                    severity="warn" if 0.3 < m.trade_count_ratio < 3 else "alert",
                    message=(
                        f"Live trade count {m.live_trade_count} vs dry {m.dry_trade_count} "
                        f"(ratio {m.trade_count_ratio:.2f}, expected "
                        f"{t.trade_count_ratio_low:.1f}–{t.trade_count_ratio_high:.1f})."
                    ),
                )
            )

    if m.live_entries > 0 and m.live_sync_clear_rate > t.max_live_sync_clear_rate:
        issues.append(
            Issue(
                code="SYNC_CLEAR_REGRESSION",
                severity="alert",
                message=(
                    f"Live sync-clear rate {m.live_sync_clear_rate:.0%} "
                    f"({m.live_sync_clears}/{m.live_entries} entries) exceeds "
                    f"threshold {t.max_live_sync_clear_rate:.0%}. "
                    "The position sync race condition may have regressed."
                ),
            )
        )

    if m.direction_disagreements and m.direction_agreement < t.min_direction_agreement:
        issues.append(
            Issue(
                code="DIRECTION_DISAGREEMENT",
                severity="alert",
                message=(
                    f"Live and dry disagreed on direction in "
                    f"{1 - m.direction_agreement:.0%} of overlapping bars. "
                    f"First few: {m.direction_disagreements[:3]}"
                ),
            )
        )

    return issues


# ────────────────────────────────────────────────────────────────────
# Output: incident report, telegram alert, headless claude
# ────────────────────────────────────────────────────────────────────


def write_incident_report(m: Metrics, issues: list[Issue]) -> str:
    """Write a markdown report to INCIDENT_PATH and return its content."""
    lines = [
        f"# Trading bot divergence incident",
        "",
        f"**Detected:** {datetime.now(timezone.utc).isoformat()}",
        f"**Window:** {m.window_start.isoformat()} → {m.window_end.isoformat()} ({m.window_hours:.1f}h)",
        "",
        "## Issues",
        "",
    ]
    for i in issues:
        lines.append(f"- **[{i.severity.upper()}] {i.code}**: {i.message}")
    lines += [
        "",
        "## Metrics",
        "",
        "| Metric | Live | Dry |",
        "| --- | --- | --- |",
        f"| Trades | {m.live_trade_count} | {m.dry_trade_count} |",
        f"| Entries | {m.live_entries} | {m.dry_entries} |",
        f"| Exits | {m.live_exits} | {m.dry_exits} |",
        f"| Sync clears (consec entries) | {m.live_sync_clears} | {m.dry_sync_clears} |",
        f"| Realized PnL (gross) | {m.live_realized_pnl:+.2f} | {m.dry_realized_pnl:+.2f} |",
        f"| Fees | {m.live_fees:.2f} | {m.dry_fees:.2f} |",
        "",
        f"**Symbol overlap:** {m.symbol_overlap_pct:.0%}",
        f"- Live symbols: {m.live_symbols}",
        f"- Dry symbols: {m.dry_symbols}",
        f"- Overlap: {m.overlap_symbols}",
        "",
        f"**Live sync-clear rate:** {m.live_sync_clear_rate:.0%}",
        f"**Direction agreement (overlapping bars):** {m.direction_agreement:.0%}",
        "",
    ]
    if m.direction_disagreements:
        lines += [
            "### Direction disagreements (sample)",
            "",
            "| Hour bucket | Symbol | Live | Dry |",
            "| --- | --- | --- | --- |",
        ]
        for d in m.direction_disagreements:
            lines.append(
                f"| {d['window']} | {d['symbol']} | {d['live_dir']} | {d['dry_dir']} |"
            )
        lines.append("")

    lines += [
        "## Investigation",
        "",
        "See `live_trading_bot/.claude/memory/playbook_divergence.md` for the",
        "step-by-step investigation playbook this incident should follow.",
        "",
    ]

    content = "\n".join(lines)
    INCIDENT_PATH.write_text(content)
    return content


async def send_telegram_alert(report: str, issues: list[Issue]) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured, skipping alert", file=sys.stderr)
        return False

    severity = "alert" if any(i.severity == "alert" for i in issues) else "warn"
    icon = "🚨" if severity == "alert" else "⚠️"
    summary_lines = [f"{icon} <b>Trading bot divergence detected</b>", ""]
    for i in issues[:5]:
        summary_lines.append(f"• [{i.severity.upper()}] {i.code}")
    summary_lines += ["", "<i>Full report and Claude investigation incoming.</i>"]
    summary = "\n".join(summary_lines)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                url,
                json={"chat_id": chat_id, "text": summary, "parse_mode": "HTML"},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"Telegram alert failed: {e}", file=sys.stderr)
            return False


def invoke_headless_claude(report: str) -> int:
    """Spawn `claude --bare -p` to investigate. Returns exit code."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set, skipping Claude invocation", file=sys.stderr)
        return 1

    # Find the claude binary. Prefer absolute path if installed via npm/pnpm.
    claude_bin = os.environ.get("CLAUDE_BIN", "claude")

    prompt = f"""You are investigating a trading bot divergence incident.

The Railway cron monitor detected divergence between the live and dry-run
trading bots. The full report is at {INCIDENT_PATH}. Read it first.

Then load the investigation playbook at:
  live_trading_bot/.claude/memory/playbook_divergence.md

Follow the playbook step by step. The playbook documents the SQL queries,
log filters, and common failure modes for divergence incidents in this repo.

Your goals (in order):
1. Identify the root cause
2. Write a minimal fix on a new branch named `auto-fix/incident-<short-summary>`
3. Run the relevant tests
4. Open a PR targeting the `harness` branch with a clear description
5. Notify via Telegram (use `scripts/_send_telegram.py` if available, otherwise
   write a short Telegram message via the bot token in env)

DO NOT:
- Auto-merge the PR. The human reviews and merges.
- Push changes to `main` or `harness` directly.
- Make changes outside the scope of the incident.

If you can't identify the root cause from the playbook + a reasonable
investigation, file the report as-is in the PR with your hypotheses and stop.
The human will take over.
"""

    cmd = [
        claude_bin,
        "--bare",
        "-p",
        prompt,
        "--allowedTools",
        "Read,Bash,Edit,Write,Glob,Grep",
        "--output-format",
        "text",
    ]

    print(f"Invoking: {' '.join(cmd[:4])} ...", file=sys.stderr)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            timeout=600,  # 10 min cap
            check=False,
        )
        return result.returncode
    except FileNotFoundError:
        print(f"claude binary not found at '{claude_bin}'", file=sys.stderr)
        return 127
    except subprocess.TimeoutExpired:
        print("claude headless invocation timed out (>10min)", file=sys.stderr)
        return 124


# ────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-hours", type=float, default=2.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute metrics and print, no alerts or Claude invocation",
    )
    parser.add_argument(
        "--no-claude",
        action="store_true",
        help="Send Telegram alert but don't invoke headless Claude",
    )
    parser.add_argument(
        "--force-alert",
        action="store_true",
        help="Send alert and invoke Claude even if no thresholds are crossed (testing)",
    )
    args = parser.parse_args()

    metrics = await compute_metrics(args.window_hours)
    issues = evaluate(metrics, Thresholds())

    print(json.dumps(metrics.to_dict(), default=str, indent=2))
    if issues:
        print("\nIssues detected:")
        for i in issues:
            print(f"  [{i.severity}] {i.code}: {i.message}")
    else:
        print("\nNo divergence detected.")

    if args.dry_run:
        return 0

    if not issues and not args.force_alert:
        return 0

    report = write_incident_report(metrics, issues)
    print(f"\nIncident report written to {INCIDENT_PATH}")

    await send_telegram_alert(report, issues)

    if args.no_claude:
        return 0

    return invoke_headless_claude(report)


if __name__ == "__main__":
    # Auto-load .env if SUPABASE_DB_URL not set (for local testing)
    if "SUPABASE_DB_URL" not in os.environ:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            from _env import load_env  # type: ignore

            load_env()
        except Exception:
            pass
    sys.exit(asyncio.run(main()) or 0)
