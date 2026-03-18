#!/usr/bin/env python3
"""
Harvest Imperva cookies from a real browser session.

Opens a standard Chrome window (no automation flags) to the DVSA site.
You browse normally, complete any Imperva challenge, and then press
Enter in the terminal. The script extracts the Imperva cookies and
saves them to imperva_cookies.json for the checker to use.

Usage:
    python harvest_cookies.py

The saved cookies (especially reese84) establish trust with Imperva's
bot detection. The checker injects these before each run so the
automated session inherits the "real user" status.

Cookies typically remain valid for several hours to days. Re-run this
script if the checker starts getting blocked again.
"""

import asyncio
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
COOKIE_SEED_FILE = PROJECT_ROOT / "imperva_cookies.json"
DVSA_LOGIN_URL = "https://driverpracticaltest.dvsa.gov.uk/login"
IMPERVA_PREFIXES = ("reese84", "visid_incap", "incap_ses", "nlbi_", "___utmvc")


async def harvest():
    import nodriver as uc

    print("=" * 60)
    print("  Imperva Cookie Harvester")
    print("=" * 60)
    print()
    print("A Chrome window will open to the DVSA booking site.")
    print("Please:")
    print("  1. Wait for the page to fully load")
    print("  2. Complete any Imperva challenge if presented")
    print("  3. You do NOT need to log in")
    print("  4. Come back here and press Enter when done")
    print()

    config = uc.Config()
    config.headless = False
    config.sandbox = False
    config.lang = "en-GB"

    browser = await uc.start(config)
    page = await browser.get(DVSA_LOGIN_URL)

    input("Press Enter after the page has loaded and any challenge is complete...")

    # Extract cookies via raw CDP — nodriver's typed Cookie parser crashes
    # on newer Chrome that dropped the deprecated 'sameParty' field.
    # We send a raw generator that yields the CDP command and returns the
    # unparsed JSON dict, bypassing nodriver's from_json() entirely.
    def _raw_get_cookies():
        cmd = {"method": "Storage.getCookies", "params": {}}
        response = yield cmd
        return response.get("cookies", [])

    cdp_cookies = await page.send(_raw_get_cookies())

    all_cookies = []
    imperva_cookies = []
    for cookie in cdp_cookies:
        entry = {
            "name": cookie.get("name", ""),
            "value": cookie.get("value", ""),
            "domain": cookie.get("domain", ""),
            "path": cookie.get("path", "/"),
            "secure": cookie.get("secure", False),
            "httpOnly": cookie.get("httpOnly", False),
        }
        all_cookies.append(entry)
        if any(entry["name"].startswith(prefix) for prefix in IMPERVA_PREFIXES):
            imperva_cookies.append(entry)

    browser.stop()

    if not imperva_cookies:
        print()
        print(f"WARNING: No Imperva cookies found among {len(all_cookies)} total cookies.")
        print("The site may not have set them yet, or the domain doesn't match.")
        print()
        print("All cookies found:")
        for c in all_cookies:
            print(f"  {c['domain']}  {c['name']}  {c['value'][:40]}...")
        print()

        save_all = input("Save ALL cookies instead? (y/N): ").strip().lower()
        if save_all == "y":
            imperva_cookies = all_cookies
        else:
            print("No cookies saved.")
            return

    # Save
    COOKIE_SEED_FILE.write_text(json.dumps(imperva_cookies, indent=2), encoding="utf-8")
    print()
    print(f"Saved {len(imperva_cookies)} cookies to {COOKIE_SEED_FILE.name}:")
    for c in imperva_cookies:
        print(f"  {c['name']}  ({c['domain']})")
    print()
    print("The checker will automatically use these on the next run.")


if __name__ == "__main__":
    import sys, platform
    if platform.system() == "Windows":
        # Suppress spurious "Event loop is closed" errors on Windows
        # when asyncio cleans up subprocess transports after loop.close()
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(harvest())
