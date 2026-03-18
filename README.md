# UK Driving Test Availability Checker

Automatically monitors the DVSA booking portal for earlier practical test slots and emails you the moment one becomes available. Designed to run 24/7 on a Raspberry Pi.

## Features

- **Stealth browser automation** — uses `nodriver` (undetected Chrome) with human-like typing, scrolling, and randomised delays to avoid bot detection
- **hCaptcha solving** — integrates with [CapSolver](https://www.capsolver.com/) and [2Captcha](https://2captcha.com/) as a fallback
- **Queue-it handling** — waits through the DVSA virtual waiting room automatically
- **Smart scheduling** — checks approximately every 5 minutes using Poisson-distributed intervals (harder to fingerprint), operating only between 06:00–23:20
- **Exponential backoff** — backs off automatically if a WAF block is detected
- **Email notifications** — sends a formatted HTML email via [Brevo](https://www.brevo.com/) when an earlier slot is found
- **Web dashboard** — lightweight Flask UI showing live status, stats, and recent logs; auto-refreshes every 60 seconds
- **systemd services** — runs as managed background services with automatic restart

## Requirements

- Python 3.11+
- Google Chrome installed
- Raspberry Pi 3B+ / Zero 2W or any Linux machine (also works on macOS/Windows for development)
- A [Brevo](https://www.brevo.com/) account (free tier is sufficient) for email notifications
- At least one captcha solver API key: [CapSolver](https://www.capsolver.com/) or [2Captcha](https://2captcha.com/)

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/DRourke-87/UKDrivingTestAvailabilityChecker.git
cd UKDrivingTestAvailabilityChecker
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
nano .env   # or edit with any text editor
```

| Variable | Required | Description |
|---|---|---|
| `DVSA_LICENCE_NUMBER` | Yes | Your driving licence number |
| `DVSA_TEST_REF` | Yes | Your current test booking reference |
| `CURRENT_TEST_DATE` | Yes | Your booked test date (`YYYY-MM-DD`) |
| `EARLIEST_ACCEPTABLE` | Yes | Earliest date you'd accept (`YYYY-MM-DD`) |
| `CAPSOLVER_API_KEY` | One required | CapSolver API key |
| `TWOCAPTCHA_API_KEY` | One required | 2Captcha API key |
| `BREVO_API_KEY` | Yes | Brevo transactional email API key |
| `NOTIFY_EMAIL` | Yes | Email address to send alerts to |
| `FROM_EMAIL` | Yes | Sender email address (must be verified in Brevo) |
| `FLASK_SECRET` | No | Secret key for Flask session (default: `change_this`) |
| `FLASK_PORT` | No | Dashboard port (default: `5050`) |
| `PROXY_URL` | No | Optional HTTP/SOCKS5 proxy |

## Usage

### Run manually

```bash
source venv/bin/activate
python -m src.scheduler
```

### Run the dashboard

```bash
source venv/bin/activate
python dashboard/app.py
```

Then open `http://localhost:5050` (or `http://<pi-ip>:5050` from another device on your network).

## Raspberry Pi — Running as systemd Services

Copy the service files and enable them to start on boot:

```bash
# Copy project to Pi
sudo cp systemd/dvsa-checker.service /etc/systemd/system/
sudo cp systemd/dvsa-dashboard.service /etc/systemd/system/

# Reload systemd and enable services
sudo systemctl daemon-reload
sudo systemctl enable dvsa-checker dvsa-dashboard
sudo systemctl start dvsa-checker dvsa-dashboard

# Check status
sudo systemctl status dvsa-checker
sudo journalctl -u dvsa-checker -f   # live logs
```

> The service files assume the project is at `/home/pi/dvsa-checker` and the virtual environment is at `/home/pi/dvsa-checker/venv`. Edit the `.service` files if your paths differ.

## Project Structure

```
.
├── src/
│   ├── checker.py      # End-to-end DVSA slot check workflow
│   ├── scheduler.py    # Main loop with Poisson scheduling & backoff
│   ├── stealth.py      # Undetected browser setup & anti-bot helpers
│   ├── captcha.py      # hCaptcha solving (CapSolver / 2Captcha)
│   ├── human.py        # Human-like delays, typing, scrolling
│   ├── notifier.py     # Brevo email notifications
│   ├── state.py        # Persistent state (JSON)
│   └── config.py       # Environment variable loading
├── dashboard/
│   └── app.py          # Flask monitoring dashboard
├── systemd/
│   ├── dvsa-checker.service
│   └── dvsa-dashboard.service
├── .env.example
└── requirements.txt
```

## How It Works

1. A stealth Chrome browser launches with a persistent profile and randomised fingerprint
2. It navigates to the DVSA booking site, warming the session with natural page visits
3. Queue-it is handled automatically if present
4. Credentials are entered with human-like typing speed and timing
5. hCaptcha is solved via the configured API
6. The booking calendar is scraped for available dates
7. If an earlier slot is found (within your acceptable range), an email is sent immediately
8. The browser closes, state is saved, and the scheduler sleeps for a randomised interval before the next check

## Disclaimer

This tool is for personal use to monitor your own existing DVSA booking. Use responsibly and in accordance with the [DVSA terms of service](https://www.gov.uk/change-driving-test). The author takes no responsibility for misuse or account issues arising from use of this tool.
