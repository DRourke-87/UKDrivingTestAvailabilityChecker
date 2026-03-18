"""
Minimal Flask dashboard for monitoring the DVSA slot checker.

Displays status, last check result, earliest slot found, run stats,
and recent log output. Auto-refreshes every 60 seconds.

Designed to be lightweight enough for Pi 3/Zero 2W.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string, jsonify
from dotenv import load_dotenv

# Resolve paths from project root (one level up from dashboard/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT_ROOT / "state.json"
LOG_FILE = PROJECT_ROOT / "logs" / "checker.log"
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "change_this")

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>DVSA Test Checker</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: #f3f4f6; color: #1f2937; }
  header { background: #00703c; color: white; padding: 1rem 2rem; display: flex;
           align-items: center; justify-content: space-between; }
  header h1 { font-size: 1.2rem; font-weight: 600; }
  .badge { background: rgba(255,255,255,0.2); padding: 4px 12px; border-radius: 12px;
           font-size: .8rem; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; padding: 1.5rem;
          max-width: 900px; margin: 0 auto; }
  .card { background: white; border-radius: 8px; padding: 1.5rem;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .card h2 { font-size: .75rem; text-transform: uppercase; letter-spacing: .06em;
             color: #6b7280; margin-bottom: .75rem; }
  .big { font-size: 1.8rem; font-weight: 700; }
  .good { color: #00703c; }
  .warn { color: #d97706; }
  .bad  { color: #dc2626; }
  .log-box { font-family: 'SF Mono', Monaco, Consolas, monospace; font-size: .72rem;
             white-space: pre-wrap; word-break: break-all;
             max-height: 300px; overflow-y: auto; background: #111827;
             color: #d1fae5; padding: 1rem; border-radius: 6px;
             line-height: 1.5; }
  .status-row { display: flex; align-items: center; gap: 8px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .dot-green { background: #00703c; }
  .dot-amber { background: #d97706; }
  .dot-red   { background: #dc2626; }
  .meta { color: #6b7280; font-size: .85rem; margin-top: .5rem; }
  footer { text-align: center; padding: 1rem; color: #9ca3af; font-size: .75rem; }
  @media (max-width: 640px) { .grid { grid-template-columns: 1fr; padding: 1rem; } }
</style>
</head>
<body>
<header>
  <h1>DVSA Slot Checker</h1>
  <span class="badge">Pi Dashboard</span>
</header>
<div class="grid">

  <div class="card">
    <h2>Status</h2>
    <div class="status-row">
      <span class="dot {{ 'dot-green' if in_window else 'dot-amber' }}"></span>
      <strong>{{ 'Actively checking' if in_window else 'Outside window (06:00–23:20)' }}</strong>
    </div>
    <p class="meta">Checks ~every 5 min (Poisson-distributed)</p>
    {% if blocks > 0 %}
    <p class="meta warn">Backoff active: {{ blocks }} consecutive block(s)</p>
    {% endif %}
  </div>

  <div class="card">
    <h2>Last Check</h2>
    <p>{{ last_run_fmt }}</p>
    <p style="margin-top:.5rem" class="{{ result_class }}">
      {{ last_message }}
    </p>
  </div>

  <div class="card">
    <h2>Earliest Slot Found</h2>
    <p class="big {{ 'good' if notify else '' }}">
      {{ earliest or '—' }}
    </p>
    {% if earliest_ever and earliest_ever != earliest %}
    <p class="meta">Best ever seen: {{ earliest_ever }}</p>
    {% endif %}
  </div>

  <div class="card">
    <h2>Stats</h2>
    <p>Total checks: <strong>{{ runs }}</strong></p>
    <p>Notifications sent: <strong>{{ notifs }}</strong></p>
    <p>Current test date: <strong>{{ current_date }}</strong></p>
    <p>Threshold: <strong>{{ threshold }}</strong></p>
  </div>

  <div class="card" style="grid-column: 1 / -1">
    <h2>Recent Logs</h2>
    <div class="log-box">{{ logs }}</div>
  </div>

</div>
<footer>Auto-refreshes every 60s &middot; Running on Raspberry Pi</footer>
</body>
</html>"""


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _load_logs(tail: int = 50) -> str:
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE) as f:
                lines = f.readlines()
                return "".join(lines[-tail:])
        except IOError:
            pass
    return "No logs yet."


@app.route("/")
def index():
    state = _load_state()
    logs = _load_logs()

    now = datetime.now()
    in_window = (6, 0) <= (now.hour, now.minute) <= (23, 20)

    result = state.get("last_result") or {}
    notify = result.get("notify", False)
    blocked = result.get("blocked", False)
    error = "Exception" in result.get("message", "")
    last_message = result.get("message", "No checks yet")

    result_class = "good" if notify else "bad" if (error or blocked) else ""

    last_run = state.get("last_run", "Never")
    if last_run != "Never":
        try:
            dt = datetime.fromisoformat(last_run)
            last_run_fmt = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            last_run_fmt = last_run
    else:
        last_run_fmt = "Never"

    return render_template_string(TEMPLATE,
        in_window=in_window,
        last_run_fmt=last_run_fmt,
        last_message=last_message,
        result_class=result_class,
        notify=notify,
        earliest=result.get("earliest_date"),
        earliest_ever=state.get("earliest_seen"),
        runs=state.get("runs", 0),
        notifs=state.get("notifications_sent", 0),
        blocks=state.get("consecutive_blocks", 0),
        current_date=os.getenv("CURRENT_TEST_DATE", "—"),
        threshold=os.getenv("EARLIEST_ACCEPTABLE", "—"),
        logs=logs,
    )


@app.route("/api/status")
def api_status():
    return jsonify(_load_state())


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=False)
