"""Interactive Telegram command bot for the live trading bot.

Runs alongside the trading loop and handles incoming user commands by
long-polling Telegram's getUpdates endpoint. Provides on-demand status
(/pnl, /account), help (/help) and mute control (/mute, /unmute).

Mute affects only AUTOMATED alerts (those routed through Alerter.send_alert).
On-demand replies sent from this module use a separate httpx client and
bypass the mute check by construction.
"""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

import httpx

from ..config import get_telegram_chat_id, get_telegram_token
from .alerts import _format_remaining
from .logger import get_logger

if TYPE_CHECKING:
    from ..exchange.interface import Exchange
    from ..storage.repository import Repository
    from .alerts import Alerter
    from .metrics import MetricsTracker


logger = get_logger(__name__)

# Telegram long-poll: getUpdates blocks until either a message arrives or
# this many seconds elapse. The HTTP client timeout must exceed this so
# httpx doesn't abort the request mid-poll.
LONG_POLL_TIMEOUT_S = 25
HTTP_TIMEOUT_S = LONG_POLL_TIMEOUT_S + 10

DURATION_RE = re.compile(r"^(\d+)\s*(s|m|h|d)$", re.IGNORECASE)


def parse_duration(text: str) -> Optional[timedelta]:
    """Parse strings like '15m', '1h', '2d' into a timedelta."""
    m = DURATION_RE.match(text.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    if unit == "d":
        return timedelta(days=n)
    return None


HELP_TEXT = (
    "<b>Available commands</b>\n\n"
    "/help — show this menu\n"
    "/pnl — daily P&amp;L (realized + unrealized)\n"
    "/account — equity, wallet, trades today, P&amp;L, positions\n"
    "/mute — show mute menu (durations + indefinite)\n"
    "/mute &lt;dur&gt; — mute alerts for a duration "
    "(e.g. <code>/mute 30m</code>, <code>/mute 1h</code>, <code>/mute 2d</code>)\n"
    "/unmute — resume automated alerts\n\n"
    "Mute affects automated alerts only. On-demand commands always work."
)


MUTE_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "15m", "callback_data": "mute:15m"},
            {"text": "1h", "callback_data": "mute:1h"},
            {"text": "4h", "callback_data": "mute:4h"},
        ],
        [
            {"text": "8h", "callback_data": "mute:8h"},
            {"text": "24h", "callback_data": "mute:24h"},
        ],
        [
            {"text": "Indefinite", "callback_data": "mute:inf"},
            {"text": "Unmute", "callback_data": "unmute"},
        ],
    ]
}


class TelegramCommandBot:
    """Long-polling Telegram command listener.

    Wires incoming commands to the live trading bot's data sources:
    - Alerter (mute control)
    - MetricsTracker (in-process trade counts)
    - Exchange (account state, wallet address, unrealized PnL)
    - Repository (authoritative realized daily PnL)

    Authorization: only messages from the configured TELEGRAM_CHAT_ID are
    accepted. Anything else is silently ignored.
    """

    def __init__(
        self,
        alerter: "Alerter",
        metrics: "MetricsTracker",
        client: "Exchange",
        db: "Repository",
    ):
        self.alerter = alerter
        self.metrics = metrics
        self.client = client
        self.db = db

        self.token = get_telegram_token()
        self.chat_id = get_telegram_chat_id()

        self._http: Optional[httpx.AsyncClient] = None
        self._offset: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def start(self) -> None:
        if not self.enabled:
            logger.info(
                "Telegram command bot disabled (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)"
            )
            return
        self._http = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram command bot started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("Telegram command bot stopped")

    # ----- Polling loop -----

    async def _poll_loop(self) -> None:
        assert self._http is not None
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        backoff = 1.0
        while self._running:
            try:
                params: dict = {
                    "timeout": LONG_POLL_TIMEOUT_S,
                    "allowed_updates": '["message","callback_query"]',
                }
                if self._offset is not None:
                    params["offset"] = self._offset
                resp = await self._http.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                backoff = 1.0
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Don't tight-loop on persistent errors (e.g. network down,
                # 401 from a bad token). Cap exponential backoff at 30s.
                logger.warning(
                    "Telegram polling error", extra={"error": str(e), "backoff_s": backoff}
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _handle_update(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                await self._handle_callback(update["callback_query"])
            elif "message" in update:
                await self._handle_message(update["message"])
        except Exception as e:
            logger.error(
                "Telegram update handler failed",
                extra={"error": str(e), "update_id": update.get("update_id")},
            )

    def _is_authorized(self, chat_id: Optional[int]) -> bool:
        if chat_id is None or not self.chat_id:
            return False
        return str(chat_id) == str(self.chat_id)

    async def _handle_message(self, message: dict) -> None:
        chat_id = message.get("chat", {}).get("id")
        if not self._is_authorized(chat_id):
            return
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        # Strip @botname suffix from the command (Telegram appends it in groups).
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/help", "/start"):
            await self._reply(HELP_TEXT)
        elif cmd == "/pnl":
            await self._cmd_pnl()
        elif cmd == "/account":
            await self._cmd_account()
        elif cmd == "/mute":
            if arg:
                await self._cmd_mute_duration(arg)
            else:
                await self._cmd_mute_menu()
        elif cmd == "/unmute":
            await self._cmd_unmute()
        else:
            await self._reply(
                f"Unknown command: <code>{cmd}</code>\nUse /help for the list."
            )

    async def _handle_callback(self, cq: dict) -> None:
        chat_id = cq.get("message", {}).get("chat", {}).get("id")
        if not self._is_authorized(chat_id):
            return

        cq_id = cq.get("id")
        data = cq.get("data", "")

        # Acknowledge so the inline-button spinner stops on the user's side.
        if cq_id:
            await self._answer_callback(cq_id)

        if data.startswith("mute:"):
            spec = data[len("mute:"):]
            if spec == "inf":
                self.alerter.mute(None)
                await self._reply("🔕 Alerts muted indefinitely. Use /unmute to resume.")
                return
            td = parse_duration(spec)
            if td is None:
                await self._reply(f"Could not parse duration: <code>{spec}</code>")
                return
            self.alerter.mute(td)
            await self._reply(f"🔕 Alerts muted for {_format_remaining(td)}.")
        elif data == "unmute":
            self.alerter.unmute()
            await self._reply("🔔 Alerts resumed.")

    # ----- Command handlers -----

    async def _cmd_pnl(self) -> None:
        try:
            account_state = await self.client.get_account_state()
        except Exception as e:
            await self._reply(f"Failed to fetch account state: {e}")
            return

        # Realized: prefer DB (authoritative across restarts). Fall back to
        # in-process metrics if the DB call errors. Unrealized: exchange.
        try:
            realized = await self.db.get_daily_pnl()
        except Exception as e:
            logger.warning("DB get_daily_pnl failed, using metrics", extra={"error": str(e)})
            realized = self.metrics.get_daily_pnl()

        unrealized = account_state.unrealized_pnl
        total = realized + unrealized
        emoji = "🟢" if total >= 0 else "🔴"
        await self._reply(
            f"{emoji} <b>Daily P&amp;L</b>\n\n"
            f"Realized: ${realized:,.2f}\n"
            f"Unrealized: ${unrealized:,.2f}\n"
            f"Total: ${total:,.2f}"
        )

    async def _cmd_account(self) -> None:
        try:
            account_state = await self.client.get_account_state()
        except Exception as e:
            await self._reply(f"Failed to fetch account state: {e}")
            return

        try:
            realized = await self.db.get_daily_pnl()
        except Exception as e:
            logger.warning("DB get_daily_pnl failed, using metrics", extra={"error": str(e)})
            realized = self.metrics.get_daily_pnl()

        trade_count = self.metrics.get_trade_count_today()
        unrealized = account_state.unrealized_pnl
        total_pnl = realized + unrealized
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        mute_line = self.alerter.mute_status()

        if account_state.positions:
            position_lines = []
            for sym, pos in account_state.positions.items():
                position_lines.append(
                    f"  {sym}: {pos.side.value} ${pos.notional_value:,.2f} "
                    f"(uPnL ${pos.unrealized_pnl:,.2f})"
                )
            pos_block = "\n".join(position_lines)
        else:
            pos_block = "  None"

        await self._reply(
            f"📋 <b>Account Overview</b>\n\n"
            f"Wallet: <code>{account_state.wallet_address}</code>\n"
            f"Equity: ${account_state.total_equity:,.2f}\n"
            f"Available: ${account_state.available_balance:,.2f}\n"
            f"Trades today: {trade_count}\n"
            f"{pnl_emoji} Daily P&amp;L: ${total_pnl:,.2f} "
            f"(realized ${realized:,.2f} / unrealized ${unrealized:,.2f})\n"
            f"Alerts: {mute_line}\n\n"
            f"<b>Positions</b>\n{pos_block}"
        )

    async def _cmd_mute_menu(self) -> None:
        status = self.alerter.mute_status()
        await self._reply(
            f"🔕 <b>Mute alerts</b>\n\nCurrent: {status}\n\nChoose a duration:",
            reply_markup=MUTE_KEYBOARD,
        )

    async def _cmd_mute_duration(self, arg: str) -> None:
        td = parse_duration(arg)
        if td is None:
            await self._reply(
                f"Could not parse duration: <code>{arg}</code>\n"
                f"Examples: <code>/mute 30m</code>, <code>/mute 1h</code>, "
                f"<code>/mute 2d</code>"
            )
            return
        self.alerter.mute(td)
        await self._reply(f"🔕 Alerts muted for {_format_remaining(td)}.")

    async def _cmd_unmute(self) -> None:
        self.alerter.unmute()
        await self._reply("🔔 Alerts resumed.")

    # ----- Telegram I/O -----

    async def _reply(self, text: str, reply_markup: Optional[dict] = None) -> None:
        if self._http is None:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload: dict = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            resp = await self._http.post(url, json=payload)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to send Telegram reply", extra={"error": str(e)})

    async def _answer_callback(self, callback_query_id: str) -> None:
        if self._http is None:
            return
        url = f"https://api.telegram.org/bot{self.token}/answerCallbackQuery"
        try:
            await self._http.post(url, json={"callback_query_id": callback_query_id})
        except Exception as e:
            logger.debug("answerCallbackQuery failed", extra={"error": str(e)})
