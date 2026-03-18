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
"""

import logging
import platform
import random
import asyncio

import nodriver as uc

from src.config import PROFILE_DIR, PROXY_URL, DVSA_HOME_URL, DVSA_LOGIN_URL, HEADLESS
from src.human import human_sleep, random_scroll

log = logging.getLogger(__name__)

def _detect_chrome_version() -> str:
    """Detect installed Chrome major version to build matching user agents."""
    import subprocess
    import platform
    import re

    version = None
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["reg", "query",
                 r"HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon",
                 "/v", "version"],
                capture_output=True, text=True, timeout=5,
            )
            match = re.search(r"(\d+)\.\d+\.\d+\.\d+", result.stdout)
            if match:
                version = match.group(1)
        elif platform.system() == "Darwin":
            result = subprocess.run(
                ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "--version"],
                capture_output=True, text=True, timeout=5,
            )
            match = re.search(r"(\d+)\.\d+\.\d+\.\d+", result.stdout)
            if match:
                version = match.group(1)
        else:
            for cmd in ["google-chrome", "chromium-browser", "chromium"]:
                try:
                    result = subprocess.run(
                        [cmd, "--version"],
                        capture_output=True, text=True, timeout=5,
                    )
                    match = re.search(r"(\d+)\.\d+\.\d+\.\d+", result.stdout)
                    if match:
                        version = match.group(1)
                        break
                except FileNotFoundError:
                    continue
    except Exception:
        pass

    return version or "131"  # Fallback


_CHROME_MAJOR = _detect_chrome_version()

# Build user agent matching the installed Chrome version and actual platform
def _build_user_agent() -> str:
    system = platform.system()
    if system == "Windows":
        os_token = "Windows NT 10.0; Win64; x64"
    elif system == "Darwin":
        os_token = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        arch = platform.machine()
        os_token = f"X11; Linux {arch}"
    return f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{_CHROME_MAJOR}.0.0.0 Safari/537.36"


_USER_AGENT = _build_user_agent()

# Block detection: phrases that indicate an actual block page, not just
# the presence of Imperva/Incapsula scripts (which are on every DVSA page).
# These must be specific enough to avoid false positives on normal pages.
_BLOCK_SIGNALS = [
    "access to this page has been denied",
    "please verify you are a human",
    "bot detected",
    "automated access to this resource",
    "your request has been blocked",
]


async def create_browser(profile_name: str = "default") -> uc.Browser:
    """
    Launch a stealth Chrome instance with nodriver.

    Uses a persistent profile directory so cookies, localStorage, and
    Imperva tracking cookies persist across runs. This dramatically
    reduces captcha frequency and makes us look like a returning user.

    Memory optimizations for Raspberry Pi 3/Zero 2W (512MB-1GB RAM):
    - --single-process: reduces memory by ~100MB
    - --js-flags=--max-old-space-size=256: limit V8 heap
    - --disable-gpu: no GPU compositing
    - --disable-extensions: no extension overhead
    """
    profile_path = PROFILE_DIR / profile_name
    profile_path.mkdir(parents=True, exist_ok=True)

    config = uc.Config()

    # Persistent profile for session continuity
    config.user_data_dir = str(profile_path)

    # Headless mode (Pi has no display; desktop can run non-headless)
    config.headless = HEADLESS

    # Use Config attributes for options that nodriver manages directly
    config.sandbox = False
    config.lang = "en-GB"

    # Browser arguments
    config.add_argument("--disable-dev-shm-usage")
    config.add_argument("--disable-gpu")
    config.add_argument("--disable-software-rasterizer")
    config.add_argument("--disable-extensions")
    config.add_argument("--window-size=1366,768")
    config.add_argument(f"--user-agent={_USER_AGENT}")

    # Memory optimization (aggressive settings only on Linux/Pi)
    if platform.system() == "Linux":
        config.add_argument("--single-process")
        config.add_argument("--js-flags=--max-old-space-size=256")
    config.add_argument("--disable-features=TranslateUI")
    config.add_argument("--disable-background-networking")
    config.add_argument("--disable-default-apps")
    config.add_argument("--disable-sync")
    config.add_argument("--no-first-run")

    # Proxy support
    if PROXY_URL:
        config.add_argument(f"--proxy-server={PROXY_URL}")

    browser = await uc.start(config)
    log.info(f"Browser launched (profile: {profile_name}, UA: {_USER_AGENT[:50]}...)")

    return browser


_STEALTH_JS = """\
// Ensure languages match
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-GB', 'en-US', 'en']
});

// Chrome runtime object (missing in automation)
if (!window.chrome) { window.chrome = {}; }
if (!window.chrome.runtime) {
    window.chrome.runtime = {
        connect: function() {},
        sendMessage: function() {},
    };
}

// Permissions API (Imperva checks this)
const originalQuery = window.navigator.permissions?.query;
if (originalQuery) {
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters);
}

// WebGL vendor/renderer (headless returns different values)
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};
"""


async def inject_stealth_scripts(page):
    """
    Inject stealth patches via CDP Page.addScriptToEvaluateOnNewDocument.

    Unlike page.evaluate(), this persists across navigations so the
    patches are active on every page load including the DVSA login.
    """
    import nodriver.cdp.page as cdp_page
    await page.send(cdp_page.add_script_to_evaluate_on_new_document(_STEALTH_JS))
    # Also run immediately on the current page context
    await page.evaluate(_STEALTH_JS)


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

        # Check for block signals
        for signal in _BLOCK_SIGNALS:
            if signal in source_lower:
                log.warning(f"Block detected: '{signal}' found in page")
                log.debug(f"Page URL: {page.url}")
                log.debug(f"Page title: {source[:500]}")
                return True
    except Exception as e:
        log.error(f"Block check error: {e}")
    return False
