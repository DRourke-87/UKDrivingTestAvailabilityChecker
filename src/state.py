"""Atomic state management with crash-safe JSON persistence."""

import json
import os
import logging
from pathlib import Path

from src.config import STATE_FILE

log = logging.getLogger(__name__)


def load_state() -> dict:
    """Load state from JSON file, returning defaults if missing or corrupt."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"State file corrupt, starting fresh: {e}")
    return {
        "last_run": None,
        "last_result": None,
        "earliest_seen": None,
        "runs": 0,
        "notifications_sent": 0,
        "consecutive_blocks": 0,
        "last_block_time": None,
        "last_notification_date": None,
    }


def save_state(state: dict):
    """
    Atomically write state to disk.

    Writes to a temp file first, then renames. This prevents corruption
    if the process is killed mid-write (e.g., SIGKILL, power loss).
    """
    tmp = STATE_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, STATE_FILE)
    except IOError as e:
        log.error(f"Failed to save state: {e}")
