"""
Multi-backend hCaptcha solver with automatic failover.

Supports:
  1. CapSolver (AI-powered, fastest, ~$2/1K) — primary
  2. 2Captcha (human workers, ~$3/1K) — fallback

The solver tries backends in order. If one fails, it falls through
to the next. This maximizes reliability while keeping costs low.
"""

import json
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
    resp = requests.post("https://2captcha.com/in.php", data={
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
        result = requests.get("https://2captcha.com/res.php", params={
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

    Handles two scenarios:
    1. Imperva's standalone captcha gate page — has data-sitekey on a
       .h-captcha div and uses onCaptchaFinished callback to POST the
       token to /_Incapsula_Resource then reload.
    2. hCaptcha embedded in the DVSA login form — iframe with sitekey
       in src, hidden textarea fields for the token.

    Returns True if captcha was solved/submitted or no captcha present.
    Returns False if solve failed.
    """
    try:
        source = await page.get_content()
        source_lower = source.lower()

        # Check for any hCaptcha presence
        has_hcaptcha = (
            "hcaptcha" in source_lower
            or "h-captcha" in source_lower
            or "data-sitekey" in source_lower
        )
        if not has_hcaptcha:
            return True  # No captcha present

        log.info("hCaptcha detected on page")

        # Extract sitekey and callback name
        captcha_info = await page.evaluate("""
            (() => {
                const info = {sitekey: null, callback: null, isImperva: false};

                // Check for Imperva captcha page (.h-captcha div with data-sitekey)
                const hcDiv = document.querySelector(".h-captcha[data-sitekey]");
                if (hcDiv) {
                    info.sitekey = hcDiv.getAttribute("data-sitekey");
                    info.callback = hcDiv.getAttribute("data-callback") || null;
                    info.isImperva = typeof onCaptchaFinished === "function";
                }

                // Fallback: check for iframe with sitekey in src
                if (!info.sitekey) {
                    const iframe = document.querySelector("iframe[src*='hcaptcha']");
                    if (iframe) {
                        const match = iframe.src.match(/sitekey=([^&]+)/);
                        if (match) info.sitekey = match[1];
                    }
                }

                // Fallback: any element with data-sitekey
                if (!info.sitekey) {
                    const el = document.querySelector("[data-sitekey]");
                    if (el) info.sitekey = el.getAttribute("data-sitekey");
                }

                return JSON.stringify(info);
            })()
        """)

        info = json.loads(captcha_info)
        site_key = info.get("sitekey")
        callback_name = info.get("callback")
        is_imperva = info.get("isImperva", False)

        if not site_key:
            log.warning("Could not extract hCaptcha sitekey from page")
            log.debug(f"Page source snippet: {source[:500]}")
            return False

        page_url = page.url
        log.info(f"Solving hCaptcha (sitekey: {site_key[:12]}..., "
                 f"callback: {callback_name}, imperva: {is_imperva})")

        token = solve_hcaptcha(site_key, page_url)
        log.info(f"Got captcha token ({len(token)} chars): {token[:30]}...")

        # Inject the solved token and trigger the appropriate callback
        safe_token = json.dumps(token)
        inject_result = await page.evaluate(f"""
            (() => {{
                const token = {safe_token};
                const result = {{filled: 0, callback: null}};

                // Fill in all known token fields
                document.querySelectorAll(
                    "[name='h-captcha-response'], [name='g-recaptcha-response']"
                ).forEach(el => {{ el.value = token; result.filled++; }});

                // Also set via hcaptcha API if available
                if (typeof hcaptcha !== "undefined" && hcaptcha.setResponse) {{
                    try {{ hcaptcha.setResponse(token); result.filled++; }} catch(e) {{}}
                }}

                // Trigger the page's registered callback
                // Imperva uses onCaptchaFinished; other pages may use
                // the data-callback attribute or hcaptcha's own callback
                if (typeof onCaptchaFinished === "function") {{
                    onCaptchaFinished(token);
                    result.callback = "onCaptchaFinished";
                }} else if (typeof hcaptchaCallback === "function") {{
                    hcaptchaCallback(token);
                    result.callback = "hcaptchaCallback";
                }} else {{
                    // Try the data-callback attribute name
                    const cb = document.querySelector("[data-callback]");
                    if (cb) {{
                        const cbName = cb.getAttribute("data-callback");
                        if (typeof window[cbName] === "function") {{
                            window[cbName](token);
                            result.callback = cbName;
                        }}
                    }}
                }}

                return JSON.stringify(result);
            }})()
        """)
        log.info(f"Token injection result: {inject_result}")

        log.info("hCaptcha token injected and callback triggered")
        return True

    except CaptchaSolveError as e:
        log.error(f"Captcha solve failed: {e}")
        return False
    except Exception as e:
        log.error(f"Captcha handling error: {e}")
        return False
