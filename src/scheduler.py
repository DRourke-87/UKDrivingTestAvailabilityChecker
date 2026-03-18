"""
Main scheduler — runs the DVSA slot checker at Poisson-distributed intervals.

Features:
  - Poisson-distributed intervals (~5 min mean) — harder to fingerprint
  - Time-of-day variation (slower at window edges, faster midday)
  - Operating window: 06:00–23:20 only
  - Exponential backoff on consecutive WAF blocks
  - Graceful SIGTERM shutdown
  - Rotating log files (5MB, 3 backups)
"""

import signal
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

from src.config import (
    CHECK_INTERVAL_MEAN, WINDOW_START, WINDOW_END, LOG_DIR,
    CLEAR_PROFILES_ON_START,
)
from src.checker import check_for_earlier_slot
from src.notifier import send_notification
from src.state import load_state, save_state
from src.stealth import clear_profiles
from src.human import poisson_sleep_duration, time_of_day_multiplier

# ── Logging setup ───────────────────────────────────────────────────────────

log_file = LOG_DIR / "checker.log"

handler = RotatingFileHandler(
    log_file, maxBytes=5 * 1024 * 1024, backupCount=3
)
handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
))

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(handler)

# Also log to stdout for systemd journal
stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
))
root_logger.addHandler(stdout_handler)

log = logging.getLogger(__name__)

# ── Shutdown handling ───────────────────────────────────────────────────────

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    log.info("SIGTERM received - shutting down after current check completes")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


# ── Operating window ───────────────────────────────────────────────────────

def in_operating_window() -> bool:
    now = datetime.now()
    start = now.replace(hour=WINDOW_START[0], minute=WINDOW_START[1], second=0)
    end = now.replace(hour=WINDOW_END[0], minute=WINDOW_END[1], second=0)
    return start <= now <= end


# ── Main loop ──────────────────────────────────────────────────────────────

async def run():
    log.info("DVSA Slot Checker started")
    log.info(f"Operating window: {WINDOW_START[0]:02d}:{WINDOW_START[1]:02d} - "
             f"{WINDOW_END[0]:02d}:{WINDOW_END[1]:02d}")
    log.info(f"Mean check interval: {CHECK_INTERVAL_MEAN}s")

    # Clear stale browser profiles to avoid issues from expired cookies
    if CLEAR_PROFILES_ON_START:
        clear_profiles()

    while not _shutdown:
        if not in_operating_window():
            log.info("Outside operating window - sleeping 60s")
            await asyncio.sleep(60)
            continue

        # ── Run check ───────────────────────────────────────────────
        log.info("-- Starting slot check --")
        state = load_state()

        try:
            result = await check_for_earlier_slot()
        except Exception as e:
            log.error(f"Unhandled exception in checker: {e}", exc_info=True)
            result = {"success": False, "message": f"Unhandled: {e}", "blocked": False}

        # Update state
        state["last_run"] = datetime.now().isoformat()
        state["last_result"] = result
        state["runs"] = state.get("runs", 0) + 1

        if result.get("earliest_date"):
            prev_earliest = state.get("earliest_seen")
            if not prev_earliest or result["earliest_date"] < prev_earliest:
                state["earliest_seen"] = result["earliest_date"]

        # ── Handle blocks with exponential backoff ──────────────────
        if result.get("blocked"):
            state["consecutive_blocks"] = state.get("consecutive_blocks", 0) + 1
            state["last_block_time"] = datetime.now().isoformat()
            log.warning(f"Block #{state['consecutive_blocks']} detected")
        else:
            state["consecutive_blocks"] = 0

        # ── Send notification if needed ─────────────────────────────
        if result.get("notify") and result.get("earliest_date"):
            # Dedup: don't re-notify for the same date within 1 hour
            last_notified = state.get("last_notification_date")
            if last_notified != result["earliest_date"]:
                sent = send_notification(result["earliest_date"])
                if sent:
                    state["notifications_sent"] = state.get("notifications_sent", 0) + 1
                    state["last_notification_date"] = result["earliest_date"]

        save_state(state)
        log.info(f"Result: {result['message']}")

        if _shutdown:
            break

        # ── Calculate next interval ─────────────────────────────────
        # Base: Poisson-distributed around mean
        interval = poisson_sleep_duration(CHECK_INTERVAL_MEAN)

        # Time-of-day adjustment
        hour = datetime.now().hour
        interval *= time_of_day_multiplier(hour)

        # Exponential backoff on blocks (capped at 30 min)
        blocks = state.get("consecutive_blocks", 0)
        if blocks > 0:
            backoff_multiplier = min(2 ** blocks, 6)  # Cap at 6x (30 min)
            interval *= backoff_multiplier
            log.info(f"Backoff active: {backoff_multiplier}x due to {blocks} consecutive blocks")

        interval = max(120, min(1800, interval))  # Clamp: 2 min - 30 min
        log.info(f"Next check in {interval:.0f}s ({interval/60:.1f} min)")
        await asyncio.sleep(interval)

    log.info("Scheduler shut down cleanly")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
