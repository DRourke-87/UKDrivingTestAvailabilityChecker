# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated UK DVSA driving test slot checker that monitors the booking portal 24/7 (designed for Raspberry Pi) and sends email alerts when an earlier test date becomes available. Uses stealth browser automation with anti-bot evasion to interact with the DVSA website.

## Running the Project

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment variables
cp .env.example .env
# Edit .env with DVSA credentials, captcha API keys, Brevo email settings

# Run the checker
python -m src.scheduler

# Run the dashboard (separate terminal)
python -m dashboard.app
```

For production deployment on Raspberry Pi, install the systemd services from `systemd/`.

## Architecture

The system follows a linear pipeline triggered on a schedule:

**`src/scheduler.py`** → Main async loop. Runs checks every ~5 minutes using Poisson-distributed intervals (anti-fingerprinting). Operates 06:00–23:20. Implements exponential backoff on WAF blocks.

**`src/checker.py`** → Orchestrates a single end-to-end check: launch browser → warm session → handle Queue-it → login → solve hCaptcha → navigate to date change → scrape dates → compare thresholds → notify.

**`src/stealth.py`** → Wraps `nodriver` (undetected Chrome). Manages persistent browser profiles, injects stealth JS patches (navigator, WebGL, permissions API), detects Queue-it waiting rooms and Imperva WAF blocks, and warms sessions with realistic navigation.

**`src/captcha.py`** → hCaptcha solver with automatic failover: CapSolver (primary, AI-powered) → 2Captcha (fallback, human workers).

**`src/human.py`** → Anti-detection behaviors: Bézier curve mouse movements with overshoots, per-character typing with variable cadence and rare typos, Poisson-distributed sleep timing, natural scrolling.

**`src/state.py`** → Crash-safe JSON state persistence using atomic writes (temp file + rename). Tracks run counts, notifications sent, consecutive blocks, earliest date seen.

**`src/notifier.py`** → Sends Brevo transactional emails when an earlier slot is found within the acceptable date range.

**`src/config.py`** → Loads all configuration from `.env`: DVSA credentials, date thresholds, captcha API keys, email settings, dashboard port.

**`dashboard/app.py`** → Flask web UI showing real-time status, last check result, run stats, and recent log output. Auto-refreshes every 60s.

## Key Design Decisions

- **`nodriver` over Selenium/Playwright**: Chosen specifically because it's harder for Imperva/bot detection to fingerprint.
- **Poisson-distributed intervals**: Check timing follows a Poisson distribution rather than fixed intervals, making the pattern harder to detect.
- **Persistent browser profiles**: Cookies and localStorage survive restarts via Chrome profile directories under `profiles/`.
- **Atomic state writes**: State is written to a temp file then renamed, preventing corruption on power loss (important for Pi).
- **Dual captcha backends**: CapSolver is faster (2-9s) but 2Captcha provides reliable fallback.

## Configuration

All secrets and settings live in `.env` (never committed). See `.env.example` for the full list. Key groups:
- DVSA credentials (licence number, test reference)
- Date thresholds (current test date, earliest acceptable)
- Captcha solver API keys (CapSolver and/or 2Captcha)
- Brevo email API settings
- Dashboard port and Flask secret

## Important Files Not in Git

- `.env` — credentials and configuration
- `state.json` — runtime state (auto-created)
- `logs/checker.log` — rotating log (5MB × 3 backups)
- `profiles/` — persistent Chrome profile data
