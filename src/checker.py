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

import asyncio
import logging
from datetime import datetime, date

from src.config import (
    DVSA_LICENCE_NUMBER, DVSA_TEST_REF,
    CURRENT_TEST_DATE, EARLIEST_ACCEPTABLE,
    DVSA_LOGIN_URL, CAPTURE_HAR,
)
from src.stealth import (
    create_browser, inject_stealth_scripts,
    warm_session, handle_queueit, check_for_block,
    wait_for_imperva_interstitial, _wait_for_page_ready,
)
from src.captcha import extract_and_solve_hcaptcha
from src.cookies import inject_seed_cookies
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
        har = None
        browser = await create_browser()
        page = await browser.get("about:blank")
        await inject_stealth_scripts(page)

        # ── HAR capture (opt-in via CAPTURE_HAR=true) ────────────────────
        if CAPTURE_HAR:
            from src.har import HarCapture
            har = HarCapture()
            har.attach(page)

        # ── Warm session (build natural Referer chain) ──────────────────
        await warm_session(page)

        # ── Inject Imperva seed cookies (from real browser session) ─────
        # Inject AFTER warm session so they aren't overwritten by the
        # navigation to the login page. The page is already on the DVSA
        # domain at this point, so cookies are accepted.
        await inject_seed_cookies(page)

        if not await handle_queueit(page):
            result["message"] = "Stuck in Queue-it waiting room"
            return result

        # Check if the login page is an Imperva interstitial
        source = await page.get_content()
        page_len = len(source)
        log.info(f"Post-warmup page: {page_len} chars, URL: {page.url}")
        if page_len < 8000 and "driving-licence-number" not in source.lower():
            log.info(f"Imperva interstitial on login page — waiting for challenge to resolve...")
            log.info(f"Interstitial content preview: {source[:500]}")
            if not await wait_for_imperva_interstitial(page, max_wait=120):
                result["message"] = "Imperva interstitial on login page did not resolve"
                result["blocked"] = True
                return result
            # Log what the page looks like after interstitial resolved
            source = await page.get_content()
            log.info(f"Post-interstitial page: {len(source)} chars, URL: {page.url}")
            await human_sleep(2, 4)

        # ── Solve hCaptcha if present (before or instead of login form) ──
        # Imperva's captcha page may use an iframe — check both page source
        # AND the DOM for captcha indicators (iframes, divs, scripts).
        source = await page.get_content()
        source_lower = source.lower()

        # Comprehensive captcha detection: check source text AND DOM elements
        captcha_diag = await page.evaluate("""
            (() => {
                const d = {};
                d.url = location.href;
                d.pageLen = document.documentElement.outerHTML.length;
                d.hasLoginForm = !!document.querySelector('#driving-licence-number');
                d.iframes = [];
                document.querySelectorAll('iframe').forEach(f => {
                    d.iframes.push({src: f.src || '', id: f.id || '', cls: f.className || '', w: f.width, h: f.height});
                });
                d.hcaptchaDivs = document.querySelectorAll('.h-captcha, [data-sitekey]').length;
                d.hcaptchaScripts = [];
                document.querySelectorAll('script[src]').forEach(s => {
                    if (s.src.includes('hcaptcha') || s.src.includes('captcha'))
                        d.hcaptchaScripts.push(s.src);
                });
                d.hasOnCaptchaFinished = typeof onCaptchaFinished === 'function';
                d.bodyText = (document.body?.innerText || '').substring(0, 300);
                return JSON.stringify(d);
            })()
        """)
        log.info(f"Captcha diagnostics: {captcha_diag[:800]}")

        has_captcha = (
            "hcaptcha" in source_lower
            or "h-captcha" in source_lower
            or "data-sitekey" in source_lower
            or "hcaptcha" in captcha_diag.lower()
            or "captcha" in captcha_diag.lower()
        )

        if has_captcha and "driving-licence-number" not in source_lower:
            log.info("hCaptcha detected — solving before proceeding")
            if not await extract_and_solve_hcaptcha(page):
                result["message"] = "Failed to solve hCaptcha on landing"
                return result

            # onCaptchaFinished POSTs the token and reloads the page
            # automatically — just wait for the reload to complete
            log.info("Waiting for page reload after captcha solve...")
            await human_sleep(8, 12)

            # May get an interstitial or another captcha after solve
            source = await page.get_content()
            if len(source) < 8000 and "driving-licence-number" not in source.lower():
                log.info("Post-captcha interstitial — waiting...")
                if not await wait_for_imperva_interstitial(page, max_wait=60):
                    result["message"] = "Post-captcha interstitial did not resolve"
                    result["blocked"] = True
                    return result
                await human_sleep(2, 4)

            # Imperva may chain captchas — solve again if needed
            source = await page.get_content()
            if "hcaptcha" in source.lower() or "h-captcha" in source.lower():
                log.info("Second hCaptcha detected after first solve — solving again")
                if not await extract_and_solve_hcaptcha(page):
                    result["message"] = "Failed to solve second hCaptcha"
                    return result
                await human_sleep(8, 12)

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

        # ── Handle hCaptcha if present on login form ─────────────────
        # The captcha may also appear embedded in the login form itself
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
        log.info("Login submitted, waiting for response...")
        await human_sleep(5, 8)

        # Log where we ended up after login
        post_login_url = page.url or ""
        log.info(f"Post-login URL: {post_login_url}")

        if not await handle_queueit(page):
            result["message"] = "Stuck in Queue-it after login"
            return result

        # ── Wait for Imperva interstitial to resolve ─────────────────
        # After login, Imperva often serves a "please wait" challenge
        # page (~4-5KB) while it runs its reese84 JS fingerprint check.
        # We must wait for this to resolve to the real page (10KB+)
        # before trying to interact with the manage page.
        source = await page.get_content()
        page_size = len(source)
        log.info(f"Post-login page size: {page_size} chars")

        if page_size < 8000:
            log.info("Imperva interstitial detected — waiting for challenge to resolve...")
            if not await wait_for_imperva_interstitial(page, max_wait=120):
                result["message"] = "Imperva interstitial did not resolve"
                result["blocked"] = True
                return result
            await human_sleep(2, 4)

        # ── Solve hCaptcha if Imperva presents one after login ────────
        # Imperva may serve an hCaptcha challenge page instead of (or
        # after) the "please wait" interstitial. We need to solve it
        # to proceed to the real manage page.
        source = await page.get_content()
        if "hcaptcha" in source.lower():
            log.info("hCaptcha challenge detected after login")
            if not await extract_and_solve_hcaptcha(page):
                result["message"] = "Failed to solve post-login hCaptcha"
                return result
            await human_sleep(1, 2)

            # Submit the captcha form / reload after solving
            submit_btn = await page.select(
                "button[type='submit'], input[type='submit'], .govuk-button",
                timeout=5,
            )
            if submit_btn:
                await human_click(page, submit_btn)
                await human_sleep(5, 8)

            # May get another interstitial after captcha solve
            source = await page.get_content()
            if len(source) < 8000:
                log.info("Post-captcha interstitial — waiting...")
                await wait_for_imperva_interstitial(page, max_wait=60)
                await human_sleep(2, 4)

        if await check_for_block(page):
            result["message"] = "WAF block after login submission"
            result["blocked"] = True
            return result

        # ── Navigate to date change ─────────────────────────────────────
        await random_scroll(page)
        await human_sleep(1.5, 3)

        # Log page content for debugging
        manage_source = await page.get_content()
        log.info(f"Manage page length: {len(manage_source)} chars")

        # Extract all links/buttons visible on the page for debugging
        links_text = await page.evaluate("""
            (() => {
                const items = [];
                document.querySelectorAll('a, button, [role="button"]').forEach(el => {
                    const text = el.textContent.trim().substring(0, 80);
                    const href = el.getAttribute('href') || '';
                    if (text) items.push(text + (href ? ' -> ' + href : ''));
                });
                return items.join(' | ');
            })()
        """)
        log.info(f"Page links/buttons: {links_text[:500]}")

        # Find the "Change date and time" link — it contains editTestDateTime
        # in the href. There are multiple Change links on the manage page
        # (date, test centre, vehicle) so we must target the right one.
        change_link = None

        # Strategy 1: Exact match on the editTestDateTime event ID
        change_link = await page.select("a[href*='editTestDateTime']", timeout=5)

        # Strategy 2: Text-based search for the date-specific link
        if not change_link:
            change_link = await page.find("Date and time of test", best_match=True, timeout=5)

        # Strategy 3: Fallback to short notice slots link
        if not change_link:
            change_link = await page.select("a[href*='viewShortNoticeSlots']", timeout=3)

        if not change_link:
            result["message"] = f"Could not find date change link on booking page. Links found: {links_text[:300]}"
            return result

        log.info("Clicking 'Change date and time' link")
        await human_click(page, change_link)
        await human_sleep(3, 5)

        # ── Handle any intermediate pages before the calendar ────────
        # The flow may go: manage -> choice page -> calendar
        # Check if we need to select "date" option and continue
        current_url = page.url or ""
        log.info(f"After editTestDateTime click, URL: {current_url}")

        page_html = await page.get_content()
        log.info(f"Date change page size: {len(page_html)} chars")

        # Log what's on this page for debugging
        page_debug = await page.evaluate("""
            (() => {
                const info = {};
                info.h1 = document.querySelector('h1')?.textContent?.trim() || '';
                info.title = document.title || '';
                info.forms = document.querySelectorAll('form').length;
                info.radios = [];
                document.querySelectorAll('input[type="radio"]').forEach(r => {
                    info.radios.push({id: r.id, name: r.name, value: r.value, label: r.parentElement?.textContent?.trim()?.substring(0, 80)});
                });
                info.buttons = [];
                document.querySelectorAll('button, input[type="submit"], .govuk-button').forEach(b => {
                    info.buttons.push(b.textContent?.trim()?.substring(0, 60) || b.value || '');
                });
                info.calendarElements = document.querySelectorAll('.BookingCalendar-date, [data-date], .SlotPicker-day, .day').length;
                info.links = [];
                document.querySelectorAll('a').forEach(a => {
                    const t = a.textContent?.trim()?.substring(0, 60);
                    const h = a.getAttribute('href') || '';
                    if (t && (h.includes('slot') || h.includes('date') || h.includes('time') || h.includes('calendar')))
                        info.links.push(t + ' -> ' + h);
                });
                return JSON.stringify(info);
            })()
        """)
        log.info(f"Date page debug: {page_debug[:800]}")

        # If there's a choice screen (radio buttons), select date option and continue
        choice_field = await page.select("#test-choice-date", timeout=3)
        if not choice_field:
            choice_field = await page.select("input[value='datetime']", timeout=2)
        if not choice_field:
            choice_field = await page.select("input[value='date']", timeout=2)

        if choice_field:
            log.info("Choice screen found — selecting date option")
            await human_click(page, choice_field)
            await human_sleep(0.8, 1.5)

            continue_btn = await page.select(
                "button[type='submit'], input[type='submit'], #driving-licence-submit, .govuk-button",
                timeout=5,
            )
            if continue_btn:
                await human_click(page, continue_btn)
                await human_sleep(3, 5)
                log.info(f"After choice submit, URL: {page.url}")

        # ── Scrape available dates ──────────────────────────────────────
        await random_scroll(page)
        await human_sleep(1, 2.5)

        # Log calendar page for debugging
        cal_debug = await page.evaluate("""
            (() => {
                const info = {};
                info.url = location.href;
                info.h1 = document.querySelector('h1')?.textContent?.trim() || '';
                // Try multiple known DVSA calendar selectors
                info.bookingCalFree = document.querySelectorAll('.BookingCalendar-date--free').length;
                info.dataDate = document.querySelectorAll('[data-date]').length;
                info.slotPicker = document.querySelectorAll('.SlotPicker-day, .SlotPicker-slot').length;
                info.dayLinks = document.querySelectorAll('a.day, td.day a, .day--available').length;
                info.allTds = document.querySelectorAll('td').length;
                // Grab any date-like text from the page
                const body = document.body?.innerText || '';
                const dateMatches = body.match(/\\d{1,2}\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+\\d{4}/gi) || [];
                info.datesInText = dateMatches.slice(0, 10);
                return JSON.stringify(info);
            })()
        """)
        log.info(f"Calendar debug: {cal_debug[:800]}")

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
            log.info("Pausing 30s on calendar page for visual inspection...")
            await asyncio.sleep(30)
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
        log.info(f"All available dates: {[str(d) for d in sorted(parsed_dates)]}")

        # Keep browser open so you can visually verify the calendar
        log.info("Pausing 30s on calendar page for visual inspection...")
        await asyncio.sleep(30)

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
        if har:
            try:
                har.flush()
            except Exception as e:
                log.warning(f"Failed to write HAR file: {e}")
        if browser:
            try:
                browser.stop()
            except Exception:
                pass

    return result
