"""
Main DVSA slot checking workflow.

Orchestrates the full end-to-end check:
  1. Launch stealth browser with persistent profile
  2. Warm session (natural navigation chain)
  3. Handle Queue-it if present
  4. Login with human-like typing
  5. Solve hCaptcha if needed
  6. Navigate to date change page
  7. Scrape available dates
  8. Compare against thresholds
  9. Close browser to free RAM
"""

import logging
from datetime import datetime, date

from src.config import (
    DVSA_LICENCE_NUMBER, DVSA_TEST_REF,
    CURRENT_TEST_DATE, EARLIEST_ACCEPTABLE,
    DVSA_LOGIN_URL,
)
from src.stealth import (
    create_browser, inject_stealth_scripts,
    warm_session, handle_queueit, check_for_block,
    _wait_for_page_ready,
)
from src.captcha import extract_and_solve_hcaptcha
from src.human import human_sleep, human_click, human_type, random_scroll

log = logging.getLogger(__name__)


async def check_for_earlier_slot() -> dict:
    """
    Perform a full DVSA slot availability check.

    Returns:
        dict with keys:
            success (bool): whether the check completed without errors
            earliest_date (str|None): earliest available date found (YYYY-MM-DD)
            message (str): human-readable result description
            notify (bool): whether to send a notification
            blocked (bool): whether a WAF block was detected
    """
    browser = None
    result = {
        "success": False,
        "earliest_date": None,
        "message": "",
        "notify": False,
        "blocked": False,
    }

    try:
        # ── Launch browser ──────────────────────────────────────────────
        browser = await create_browser()
        page = await browser.get("about:blank")
        await inject_stealth_scripts(page)

        # ── Warm session (build natural Referer chain) ──────────────────
        await warm_session(page)

        if not await handle_queueit(page):
            result["message"] = "Stuck in Queue-it waiting room"
            return result

        if await check_for_block(page):
            result["message"] = "WAF block detected on landing"
            result["blocked"] = True
            return result

        await random_scroll(page)
        await human_sleep(1, 3)

        # ── Enter licence number ────────────────────────────────────────
        log.info("Entering credentials")
        licence_field = await page.select("#driving-licence-number", timeout=20)
        if not licence_field:
            result["message"] = "Could not find licence number field"
            return result

        await human_click(page, licence_field)
        await human_sleep(0.5, 1.2)
        await human_type(licence_field, DVSA_LICENCE_NUMBER)
        await human_sleep(0.8, 1.8)

        # ── Enter test reference ────────────────────────────────────────
        ref_field = await page.select("#application-reference-number", timeout=10)
        if not ref_field:
            result["message"] = "Could not find reference number field"
            return result

        await human_click(page, ref_field)
        await human_sleep(0.4, 1.0)
        await human_type(ref_field, DVSA_TEST_REF)
        await human_sleep(1.0, 2.5)

        # ── Handle hCaptcha if present ──────────────────────────────────
        if not await extract_and_solve_hcaptcha(page):
            result["message"] = "Failed to solve hCaptcha"
            return result
        await human_sleep(0.5, 1.5)

        # ── Submit login ────────────────────────────────────────────────
        submit_btn = await page.select("#booking-login", timeout=10)
        if not submit_btn:
            result["message"] = "Could not find login button"
            return result

        await human_click(page, submit_btn)
        await human_sleep(4, 8)

        # Log where we ended up after login for debugging
        post_login_url = page.url or ""
        log.info(f"Post-login URL: {post_login_url}")

        if not await handle_queueit(page):
            result["message"] = "Stuck in Queue-it after login"
            return result

        # Wait for the post-login page to load before checking for blocks
        await human_sleep(2, 4)

        if await check_for_block(page):
            result["message"] = "WAF block after login submission"
            result["blocked"] = True
            return result

        # ── Navigate to date change ─────────────────────────────────────
        await random_scroll(page)
        await human_sleep(1.5, 3)

        # Look for "Change" link
        change_link = await page.find("Change", best_match=True, timeout=15)
        if not change_link:
            result["message"] = "Could not find 'Change' link on booking page"
            return result

        await human_click(page, change_link)
        await human_sleep(2, 4)

        # ── Select "Change date and time" if choice screen appears ──────
        try:
            date_option = await page.select("#test-choice-field", timeout=5)
            if date_option:
                await human_click(page, date_option)
                await human_sleep(0.8, 1.5)

                continue_btn = await page.select("#driving-licence-submit", timeout=5)
                if continue_btn:
                    await human_click(page, continue_btn)
                    await human_sleep(2, 4)
        except Exception:
            log.info("No choice screen - direct date selection flow")

        # ── Scrape available dates ──────────────────────────────────────
        await random_scroll(page)
        await human_sleep(1, 2.5)

        available_dates = await page.evaluate("""
            (() => {
                const dates = [];
                // DVSA calendar uses these selectors for available slots
                const cells = document.querySelectorAll(
                    '.BookingCalendar-date--free, [data-date]:not([disabled])'
                );
                cells.forEach(cell => {
                    const d = cell.getAttribute('data-date') || cell.textContent.trim();
                    if (d && d.match(/^\\d{4}-\\d{2}-\\d{2}$/)) {
                        dates.push(d);
                    }
                });
                return dates;
            })()
        """)

        if not available_dates:
            result["success"] = True
            result["message"] = "No available dates found on calendar"
            log.info("No slots visible on calendar")
            return result

        # Parse and find earliest
        parsed_dates = []
        for d_str in available_dates:
            try:
                parsed_dates.append(date.fromisoformat(d_str))
            except ValueError:
                pass

        if not parsed_dates:
            result["success"] = True
            result["message"] = "Could not parse any dates from calendar"
            return result

        earliest = min(parsed_dates)
        current = date.fromisoformat(CURRENT_TEST_DATE)
        threshold = date.fromisoformat(EARLIEST_ACCEPTABLE)

        log.info(f"Earliest slot: {earliest} | Current test: {current} | Threshold: {threshold}")

        result["success"] = True
        result["earliest_date"] = str(earliest)

        if earliest < current and earliest >= threshold:
            result["message"] = f"EARLIER SLOT AVAILABLE: {earliest}"
            result["notify"] = True
            log.info(f"*** EARLIER SLOT FOUND: {earliest} ***")
        elif earliest < threshold:
            result["message"] = f"Slot {earliest} is before earliest acceptable date {threshold}"
            result["notify"] = False
        else:
            result["message"] = f"No improvement. Earliest available: {earliest}, current: {current}"
            result["notify"] = False

    except Exception as e:
        log.error(f"Checker exception: {e}", exc_info=True)
        result["message"] = f"Exception: {str(e)}"

    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass

    return result
