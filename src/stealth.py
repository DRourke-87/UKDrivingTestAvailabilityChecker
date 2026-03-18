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
import random
import asyncio

import nodriver as uc

from src.config import PROFILE_DIR, PROXY_URL, DVSA_HOME_URL, DVSA_LOGIN_URL
from src.human import human_sleep, random_scroll

log = logging.getLogger(__name__)

# Curated list of recent Chrome user agents (rotate per session)
_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Imperva/block detection keywords
_BLOCK_SIGNALS = [
    "imperva", "incapsula", "access denied",
    "please verify you are a human", "bot detected",
    "unusual traffic", "security check", "blocked",
    "automated access", "request blocked",
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

    # Headless mode (Pi has no display)
    config.headless = True

    # Rotate user agent per session (but consistent within session)
    ua = random.choice(_USER_AGENTS)

    # Browser arguments
    config.add_argument("--no-sandbox")
    config.add_argument("--disable-dev-shm-usage")
    config.add_argument("--disable-gpu")
    config.add_argument("--disable-software-rasterizer")
    config.add_argument("--disable-extensions")
    config.add_argument("--window-size=1366,768")
    config.add_argument("--lang=en-GB")
    config.add_argument(f"--user-agent={ua}")

    # Memory optimization for Pi
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
    log.info(f"Browser launched (profile: {profile_name}, UA: {ua[:50]}...)")

    return browser


async def inject_stealth_scripts(page):
    """
    Inject additional stealth patches via CDP.

    nodriver already handles most automation detection, but we add
    extra hardening for Imperva-specific checks.
    """
    await page.evaluate("""
        // Ensure languages match
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-GB', 'en-US', 'en']
        });

        // Realistic plugin list (not empty like headless)
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugins = [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ];
                plugins.length = 3;
                return plugins;
            }
        });

        // Chrome runtime object (missing in automation)
        if (!window.chrome) {
            window.chrome = {};
        }
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
    """)


async def warm_session(page):
    """
    Visit pages in a natural sequence before hitting the login page.

    This builds a realistic Referer chain and sets cookies that
    a real user would have. Imperva tracks navigation patterns —
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
    await human_sleep(2, 4)

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
        url = page.url or ""
        source = await page.get_content()

        if "queue-it" not in url and "queueit" not in source.lower():
            return True

        log.info("Queue-it waiting room detected — waiting patiently...")
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

    Returns True if a block is detected.
    """
    try:
        source = await page.get_content()
        source_lower = source.lower()
        for signal in _BLOCK_SIGNALS:
            if signal in source_lower:
                log.warning(f"Block detected: '{signal}' found in page")
                return True
    except Exception as e:
        log.error(f"Block check error: {e}")
    return False
