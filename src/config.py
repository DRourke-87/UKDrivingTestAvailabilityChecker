"""Centralized configuration loaded from .env file."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Resolve paths relative to project root (one level up from src/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)


def _require(var: str) -> str:
    val = os.getenv(var)
    if not val:
        print(f"[FATAL] Required env var {var} is not set. See .env.example.", file=sys.stderr)
        sys.exit(1)
    return val


# ── DVSA credentials ────────────────────────────────────────────────────────
DVSA_LICENCE_NUMBER = _require("DVSA_LICENCE_NUMBER")
DVSA_TEST_REF = _require("DVSA_TEST_REF")
CURRENT_TEST_DATE = _require("CURRENT_TEST_DATE")
EARLIEST_ACCEPTABLE = _require("EARLIEST_ACCEPTABLE")

# ── Captcha solvers (at least one) ──────────────────────────────────────────
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")
TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY", "")

# ── Brevo email ─────────────────────────────────────────────────────────────
BREVO_API_KEY = _require("BREVO_API_KEY")
NOTIFY_EMAIL = _require("NOTIFY_EMAIL")
FROM_EMAIL = _require("FROM_EMAIL")

# ── Dashboard ───────────────────────────────────────────────────────────────
FLASK_SECRET = os.getenv("FLASK_SECRET", "change_this")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5050"))

# ── Browser ────────────────────────────────────────────────────────────────
HEADLESS = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")
CLEAR_PROFILES_ON_START = os.getenv("CLEAR_PROFILES_ON_START", "true").lower() in ("true", "1", "yes")
CAPTURE_HAR = os.getenv("CAPTURE_HAR", "false").lower() in ("true", "1", "yes")

# ── Optional proxy ──────────────────────────────────────────────────────────
PROXY_URL = os.getenv("PROXY_URL", "")

# ── Paths ───────────────────────────────────────────────────────────────────
STATE_FILE = PROJECT_ROOT / "state.json"
LOG_DIR = PROJECT_ROOT / "logs"
PROFILE_DIR = PROJECT_ROOT / "profiles"
COOKIE_SEED_FILE = PROJECT_ROOT / "imperva_cookies.json"

# ── DVSA URL ────────────────────────────────────────────────────────────────
DVSA_LOGIN_URL = "https://driverpracticaltest.dvsa.gov.uk/login"
DVSA_HOME_URL = "https://www.gov.uk/change-driving-test"

# ── Scheduler ───────────────────────────────────────────────────────────────
CHECK_INTERVAL_MEAN = 300  # 5 minutes mean (Poisson distributed)
WINDOW_START = (6, 0)
WINDOW_END = (23, 20)

# Ensure runtime directories exist
LOG_DIR.mkdir(exist_ok=True)
PROFILE_DIR.mkdir(exist_ok=True)
