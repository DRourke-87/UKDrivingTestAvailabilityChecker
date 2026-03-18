"""
Multi-backend hCaptcha solver with automatic failover.

Supports:
  1. CapSolver (AI-powered, fastest, ~$2/1K) — primary
  2. 2Captcha (human workers, ~$3/1K) — fallback

The solver tries backends in order. If one fails, it falls through
to the next. This maximizes reliability while keeping costs low.
"""

import os
import time
import logging
import requests

from src.config import CAPSOLVER_API_KEY, TWOCAPTCHA_API_KEY

log = logging.getLogger(__name__)


class CaptchaSolveError(Exception):
    pass


# ── CapSolver Backend ───────────────────────────────────────────────────────

def _solve_capsolver(site_key: str, page_url: str) -> str:
    """
    Solve hCaptcha using CapSolver's AI engine.

    Typically solves in 2-9 seconds (much faster than human workers).
    """
    if not CAPSOLVER_API_KEY:
        raise CaptchaSolveError("CapSolver API key not configured")

    log.info("Attempting hCaptcha solve via CapSolver...")

    # Create task
    resp = requests.post("https://api.capsolver.com/createTask", json={
        "clientKey": CAPSOLVER_API_KEY,
        "task": {
            "type": "HCaptchaTaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
    }, timeout=30)
    data = resp.json()

    if data.get("errorId", 0) != 0:
        raise CaptchaSolveError(f"CapSolver create error: {data.get('errorDescription')}")

    task_id = data["taskId"]

    # Poll for result (CapSolver is usually fast: 2-15s)
    for _ in range(30):
        time.sleep(3)
        result = requests.post("https://api.capsolver.com/getTaskResult", json={
            "clientKey": CAPSOLVER_API_KEY,
            "taskId": task_id,
        }, timeout=15).json()

        status = result.get("status")
        if status == "ready":
            token = result["solution"]["gRecaptchaResponse"]
            log.info("hCaptcha solved via CapSolver")
            return token
        if status == "failed":
            raise CaptchaSolveError(f"CapSolver failed: {result}")

    raise CaptchaSolveError("CapSolver timed out")


# ── 2Captcha Backend ───────────────────────────────────────────────────────

def _solve_twocaptcha(site_key: str, page_url: str) -> str:
    """
    Solve hCaptcha using 2Captcha's human worker network.

    Typically 15-60 seconds. Most reliable for complex captchas.
    """
    if not TWOCAPTCHA_API_KEY:
        raise CaptchaSolveError("2Captcha API key not configured")

    log.info("Attempting hCaptcha solve via 2Captcha...")

    # Submit captcha
    resp = requests.post("http://2captcha.com/in.php", data={
        "key": TWOCAPTCHA_API_KEY,
        "method": "hcaptcha",
        "sitekey": site_key,
        "pageurl": page_url,
        "json": 1,
    }, timeout=30)
    data = resp.json()

    if data.get("status") != 1:
        raise CaptchaSolveError(f"2Captcha submit error: {data}")

    captcha_id = data["request"]

    # Poll for result (human workers: 15-90s typical)
    for _ in range(30):
        time.sleep(10)
        result = requests.get("http://2captcha.com/res.php", params={
            "key": TWOCAPTCHA_API_KEY,
            "action": "get",
            "id": captcha_id,
            "json": 1,
        }, timeout=15).json()

        if result.get("status") == 1:
            log.info("hCaptcha solved via 2Captcha")
            return result["request"]

        if result.get("request") != "CAPCHA_NOT_READY":
            raise CaptchaSolveError(f"2Captcha error: {result}")

    raise CaptchaSolveError("2Captcha timed out")


# ── Unified solver ──────────────────────────────────────────────────────────

def solve_hcaptcha(site_key: str, page_url: str) -> str:
    """
    Solve hCaptcha using available backends with automatic failover.

    Order: CapSolver (fastest) → 2Captcha (most reliable)

    Returns:
        The solved captcha token string.

    Raises:
        CaptchaSolveError if all backends fail.
    """
    backends = []
    if CAPSOLVER_API_KEY:
        backends.append(("CapSolver", _solve_capsolver))
    if TWOCAPTCHA_API_KEY:
        backends.append(("2Captcha", _solve_twocaptcha))

    if not backends:
        raise CaptchaSolveError("No captcha solver API keys configured")

    errors = []
    for name, solver in backends:
        try:
            return solver(site_key, page_url)
        except CaptchaSolveError as e:
            log.warning(f"{name} failed: {e}")
            errors.append(f"{name}: {e}")

    raise CaptchaSolveError(f"All captcha backends failed: {'; '.join(errors)}")


async def extract_and_solve_hcaptcha(page) -> bool:
    """
    Detect hCaptcha on page, extract sitekey, solve, and inject token.

    Returns True if captcha was solved and injected, False if no captcha
    found or solve failed.
    """
    try:
        source = await page.get_content()
        if "hcaptcha" not in source.lower():
            return True  # No captcha present

        log.info("hCaptcha detected on page")

        # Extract sitekey from iframe src or data attribute
        site_key = None

        # Try iframe src
        site_key = await page.evaluate("""
            (() => {
                const iframe = document.querySelector("iframe[src*='hcaptcha']");
                if (iframe) {
                    const match = iframe.src.match(/sitekey=([^&]+)/);
                    if (match) return match[1];
                }
                const el = document.querySelector("[data-sitekey]");
                if (el) return el.getAttribute("data-sitekey");
                return null;
            })()
        """)

        if not site_key:
            log.warning("Could not extract hCaptcha sitekey")
            return False

        page_url = page.url
        log.info(f"Solving hCaptcha (sitekey: {site_key[:12]}...)")

        token = solve_hcaptcha(site_key, page_url)

        # Inject the solved token
        await page.evaluate(f"""
            (() => {{
                const resp = document.querySelector("[name='h-captcha-response']");
                if (resp) resp.value = "{token}";
                const grecap = document.querySelector("[name='g-recaptcha-response']");
                if (grecap) grecap.value = "{token}";
                // Trigger hCaptcha callback if registered
                if (typeof hcaptchaCallback === "function") hcaptchaCallback("{token}");
                // Also try dispatching event
                const textarea = document.querySelector("textarea[name='h-captcha-response']");
                if (textarea) {{
                    textarea.value = "{token}";
                    textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
            }})()
        """)

        log.info("hCaptcha token injected successfully")
        return True

    except CaptchaSolveError as e:
        log.error(f"Captcha solve failed: {e}")
        return False
    except Exception as e:
        log.error(f"Captcha handling error: {e}")
        return False
