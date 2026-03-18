"""
Stealth browser management using nodriver.

nodriver (by the same author as undetected-chromedriver) provides:
- No Selenium dependency — direct CDP communication
- Automatic Chrome binary patching to remove automation flags
- No navigator.webdriver leak
- No CDP detection via Runtime.enable leak
- Real Chrome TLS stack (genuine JA3/JA4 fingerprint)

This module adds:
- Persistent browser profiles (appear as returning user)
- Session warming (natural navigation sequence)
- Imperva/Incapsula block detection
- Low-memory optimizations for Raspberry Pi

Key anti-detection principle: the LESS we modify the browser, the better.
Every flag, override, or JS patch is a potential fingerprint inconsistency
that Imperva can detect. nodriver already handles the basics — we should
only add what's strictly necessary.
"""

import logging
import platform
import random
import shutil
import asyncio

import nodriver as uc

from src.config import PROFILE_DIR, PROXY_URL, DVSA_HOME_URL, DVSA_LOGIN_URL, HEADLESS
from src.human import human_sleep, random_scroll

log = logging.getLogger(__name__)

# Block detection: phrases that indicate an actual block page, not just
# the presence of Imperva/Incapsula scripts (which are on every DVSA page).
# These must be specific enough to avoid false positives on normal pages.
_BLOCK_SIGNALS = [
    "access to this page has been denied",
    "bot detected",
    "automated access to this resource",
    "your request has been blocked",
    "error 15",
    "error code 15",
]


def clear_profiles() -> None:
    """
    Delete all persistent browser profiles to remove stale cookies/state.

    Stale cookies from failed runs (e.g. expired reese84 tokens, invalid
    Imperva sessions) can cause immediate blocks on the next attempt.
    Clearing forces a fresh start, with seed cookies providing the
    Imperva trust tokens if available.
    """
    if PROFILE_DIR.exists():
        for child in PROFILE_DIR.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except Exception as e:
                log.warning(f"Failed to remove profile item {child}: {e}")
        log.info("Cleared all browser profiles")
    else:
        log.info("No profiles directory to clear")


async def create_browser(profile_name: str = "default") -> uc.Browser:
    """
    Launch a stealth Chrome instance with nodriver.

    Anti-detection strategy: keep Chrome as close to stock as possible.
    - Do NOT override user agent (let Chrome use its real one so it
      matches sec-ch-ua, navigator.userAgentData, and the TLS fingerprint)
    - Do NOT disable features unnecessarily (each flag changes the fingerprint)
    - Do NOT inject JS patches that wrap native functions (Imperva checks
      property descriptors to detect overrides)
    - Let nodriver handle the core anti-automation patches

    On Linux/Pi, minimal memory flags are added since there's no alternative.
    """
    profile_path = PROFILE_DIR / profile_name
    profile_path.mkdir(parents=True, exist_ok=True)

    config = uc.Config()

    # Persistent profile for session continuity
    config.user_data_dir = str(profile_path)

    # Non-headless is MUCH harder for Imperva to detect. Only use headless
    # on systems with no display (Pi) — and even then, consider Xvfb.
    config.headless = HEADLESS

    config.sandbox = False
    config.lang = "en-GB"

    # Only the truly necessary arguments — every extra flag is a fingerprint risk
    config.add_argument("--window-size=1366,768")
    config.add_argument("--disable-dev-shm-usage")

    # Memory optimization (only on Linux/Pi where RAM is constrained)
    if platform.system() == "Linux":
        config.add_argument("--single-process")
        config.add_argument("--js-flags=--max-old-space-size=256")
        config.add_argument("--disable-gpu")

    # Proxy support
    if PROXY_URL:
        config.add_argument(f"--proxy-server={PROXY_URL}")

    browser = await uc.start(config)
    log.info(f"Browser launched (profile: {profile_name}, headless: {HEADLESS})")

    return browser


async def inject_stealth_scripts(page):
    """
    Inject minimal stealth patches via CDP.

    IMPORTANT: Less is more. nodriver already patches navigator.webdriver
    and CDP detection. We only add the languages patch here because the
    DVSA site is UK-specific and en-GB needs to be first.

    We deliberately do NOT patch:
    - chrome.runtime (nodriver handles this; our version was detectable)
    - permissions.query (wrapping native functions is detectable via
      Function.prototype.toString and property descriptor checks)
    - WebGL vendor/renderer (hardcoded Intel values may not match the
      actual GPU, creating a fingerprint inconsistency)
    """
    import nodriver.cdp.page as cdp_page

    # Minimal: only set languages to match en-GB locale
    stealth_js = """\
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-GB', 'en-US', 'en'],
    configurable: true
});
"""
    await page.send(cdp_page.add_script_to_evaluate_on_new_document(stealth_js))
    await page.evaluate(stealth_js)


async def _wait_for_page_ready(page, marker: str, timeout: int = 30):
    """
    Wait until the expected page content is loaded.

    Args:
        page: nodriver page/tab
        marker: HTML element ID or text that indicates the page is ready
        timeout: max seconds to wait
    """
    for _ in range(timeout // 2):
        try:
            source = await page.get_content()
            if marker in source.lower():
                return True
        except Exception:
            pass
        await asyncio.sleep(2)
    log.warning(f"Timed out waiting for page marker: {marker}")
    return False


async def wait_for_imperva_interstitial(page, max_wait: int = 120) -> bool:
    """
    Wait for Imperva's "please wait" interstitial challenge to resolve.

    When Imperva is suspicious (bot score B10) but hasn't blocked yet, it
    serves a small interstitial page (~4-5KB) that runs the reese84 JS
    challenge in the background. The page shows a "please wait" spinner.

    If the challenge passes, Imperva reloads to the real page (much larger).
    If it fails, the page stays small or shows an error.

    We detect the interstitial by page size: real DVSA pages are 10KB+,
    while the interstitial is ~4-5KB. We poll until the page grows or
    we time out.

    Args:
        page: nodriver page/tab
        max_wait: maximum seconds to wait (default 2 minutes)

    Returns:
        True if the page resolved to real content, False if still stuck.
    """
    elapsed = 0
    poll_interval = 3

    while elapsed < max_wait:
        try:
            source = await page.get_content()
            page_len = len(source)
            source_lower = source.lower()

            # Real DVSA pages have substantial content
            if page_len > 8000:
                log.info(f"Imperva interstitial resolved after {elapsed}s ({page_len} chars)")
                return True

            # hCaptcha challenge page — not a block, it's solvable
            if "hcaptcha" in source_lower or "h-captcha" in source_lower or "data-sitekey" in source_lower:
                log.info(f"Imperva interstitial resolved to hCaptcha challenge after {elapsed}s")
                return True

            # Check if it's an actual block (not just "please wait")
            for signal in _BLOCK_SIGNALS:
                if signal in source_lower:
                    log.warning(f"Imperva challenge resolved to block: '{signal}'")
                    return False

            log.info(f"Imperva interstitial still showing ({page_len} chars, {elapsed}s/{max_wait}s)...")
        except Exception as e:
            log.debug(f"Error checking interstitial: {e}")

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    log.warning(f"Imperva interstitial did not resolve within {max_wait}s")
    return False


async def warm_session(page):
    """
    Visit pages in a natural sequence before hitting the login page.

    This builds a realistic Referer chain and sets cookies that
    a real user would have. Imperva tracks navigation patterns -
    going directly to a login page without prior browsing is suspicious.
    """
    log.info("Warming session with natural navigation sequence")

    # Step 1: Visit gov.uk change test page (common entry point)
    await page.get(DVSA_HOME_URL)
    await human_sleep(2, 5)
    await random_scroll(page)
    await human_sleep(1, 3)

    # Step 2: Navigate to DVSA login (natural transition)
    await page.get(DVSA_LOGIN_URL)
    await human_sleep(3, 6)

    # Step 3: Wait for the login form to actually appear
    await _wait_for_page_ready(page, "driving-licence-number")
    await human_sleep(1, 3)

    log.info("Session warming complete")


async def handle_queueit(page, max_wait: int = 600) -> bool:
    """
    Detect and wait through Queue-it waiting room.

    Queue-it redirects to a waiting room URL. We detect it and
    poll periodically until admitted.

    Args:
        page: nodriver page/tab
        max_wait: maximum seconds to wait (default 10 minutes)

    Returns:
        True if passed through or no queue, False if timed out.
    """
    try:
        url = (page.url or "").lower()

        # Only detect Queue-it by URL redirect, not page source
        # (DVSA pages reference Queue-it scripts even when not queuing)
        if "queue-it" not in url and "queue.driverpracticaltest" not in url:
            return True

        log.info("Queue-it waiting room detected - waiting patiently...")
        elapsed = 0
        while elapsed < max_wait:
            await asyncio.sleep(15)
            elapsed += 15
            url = page.url or ""
            if "queue-it" not in url:
                log.info(f"Queue-it cleared after {elapsed}s")
                await human_sleep(2, 4)
                return True
            log.info(f"Still in Queue-it... ({elapsed}s)")

        log.warning("Queue-it timed out")
        return False
    except Exception as e:
        log.error(f"Queue-it handler error: {e}")
        return True


async def check_for_block(page) -> bool:
    """
    Detect Imperva/Incapsula or generic WAF block pages.

    Only returns True for actual block pages, not normal DVSA pages
    that happen to contain Imperva scripts.
    """
    try:
        source = await page.get_content()
        source_lower = source.lower()

        # If the page has DVSA form fields, it's not a block page
        if "driving-licence-number" in source_lower:
            return False

        # hCaptcha pages are solvable challenges, not blocks
        if "hcaptcha" in source_lower:
            return False

        # Known DVSA page markers — if any are present, it's a real page
        dvsa_markers = ["manage", "booking", "test date", "change", "govuk"]
        if any(marker in source_lower for marker in dvsa_markers):
            return False

        # Check for block signals
        for signal in _BLOCK_SIGNALS:
            if signal in source_lower:
                log.warning(f"Block detected: '{signal}' found in page")
                log.info(f"Block page URL: {page.url}")
                log.info(f"Block page content ({len(source)} chars): {source[:1000]}")
                return True

        # Imperva error 15 and similar challenge pages are tiny — just a
        # script tag that sets cookies. A real DVSA page is always > 2KB.
        url = (page.url or "").lower()
        is_dvsa = "dvsa" in url or "driverpracticaltest" in url
        if is_dvsa and len(source) < 2000:
            log.warning(f"Suspected Imperva challenge page: only {len(source)} chars on DVSA URL")
            log.info(f"Challenge page URL: {page.url}")
            log.info(f"Challenge page content: {source[:1000]}")
            return True
    except Exception as e:
        log.error(f"Block check error: {e}")
    return False
