"""
Imperva cookie seeding — load cookies harvested from a real browser session.

Imperva uses several cookies for bot detection:
  - reese84: main bot-detection token (JS fingerprint challenge result)
  - visid_incap_*: visitor identification
  - incap_ses_*: session tracking
  - nlbi_*: load balancer identity
  - ___utmvc: additional fingerprint cookie

A real browser session produces valid values for these cookies. By seeding
them into the automated browser before navigation, we inherit the "trusted"
status of that real session. The reese84 cookie is the most important one —
without it, Imperva immediately challenges or blocks the request.

Usage:
  1. Run `python harvest_cookies.py` to open a real Chrome window
  2. Browse to the DVSA site and complete any challenge
  3. The script saves cookies to imperva_cookies.json
  4. The checker automatically loads these on each run
"""

import json
import logging

from src.config import COOKIE_SEED_FILE

log = logging.getLogger(__name__)

# Cookie name prefixes that are Imperva-related
IMPERVA_PREFIXES = ("reese84", "visid_incap", "incap_ses", "nlbi_", "___utmvc")


def load_seed_cookies() -> list[dict]:
    """Load Imperva cookies from the seed file, if it exists."""
    if not COOKIE_SEED_FILE.exists():
        log.info("No cookie seed file found — starting fresh")
        return []

    try:
        data = json.loads(COOKIE_SEED_FILE.read_text(encoding="utf-8"))
        cookies = data if isinstance(data, list) else data.get("cookies", [])
        log.info(f"Loaded {len(cookies)} seed cookies from {COOKIE_SEED_FILE.name}")
        return cookies
    except Exception as e:
        log.warning(f"Failed to load cookie seed file: {e}")
        return []


def save_seed_cookies(cookies: list[dict]) -> None:
    """Save cookies to the seed file (atomic write)."""
    tmp = COOKIE_SEED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    tmp.replace(COOKIE_SEED_FILE)
    log.info(f"Saved {len(cookies)} cookies to {COOKIE_SEED_FILE.name}")


def filter_imperva_cookies(cookies: list[dict]) -> list[dict]:
    """Keep only Imperva-related cookies."""
    return [
        c for c in cookies
        if any(c.get("name", "").startswith(prefix) for prefix in IMPERVA_PREFIXES)
    ]


async def inject_seed_cookies(page) -> int:
    """
    Inject seed cookies into the browser via CDP before navigating to DVSA.

    Must be called after browser launch but before visiting the target site.
    We navigate to the DVSA domain first (a lightweight request) so the
    browser accepts cookies for that domain.

    Returns the number of cookies injected.
    """
    cookies = load_seed_cookies()
    if not cookies:
        return 0

    injected = 0
    for cookie in cookies:
        try:
            # Use raw CDP generator to avoid nodriver parser issues
            def _raw_set_cookie(c=cookie):
                cmd = {
                    "method": "Network.setCookie",
                    "params": {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ".driverpracticaltest.dvsa.gov.uk"),
                        "path": c.get("path", "/"),
                        "secure": c.get("secure", True),
                        "httpOnly": c.get("httpOnly", False),
                    },
                }
                yield cmd
            await page.send(_raw_set_cookie())
            injected += 1
        except Exception as e:
            log.debug(f"Failed to inject cookie {cookie.get('name')}: {e}")

    log.info(f"Injected {injected}/{len(cookies)} seed cookies")
    return injected
