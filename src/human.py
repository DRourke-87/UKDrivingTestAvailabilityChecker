"""
Human-like behavior simulation for anti-detection.

Uses Poisson-distributed timing, Bézier curve mouse movement,
per-character typing with realistic cadence, and natural scrolling.
"""

import math
import random
import asyncio
import logging

log = logging.getLogger(__name__)


# ── Timing ──────────────────────────────────────────────────────────────────

def poisson_sleep_duration(mean_seconds: float) -> float:
    """
    Generate a Poisson-distributed sleep duration.

    Real human reaction times follow a right-skewed distribution:
    most responses are near the mean, with occasional longer pauses.
    An exponential distribution (inter-arrival time of Poisson process)
    models this well.
    """
    duration = random.expovariate(1.0 / mean_seconds)
    # Clamp to reasonable bounds (10% to 400% of mean)
    return max(mean_seconds * 0.1, min(mean_seconds * 4.0, duration))


async def human_sleep(min_s: float = 0.8, max_s: float = 2.4):
    """Variable-length pause mimicking human reaction time."""
    mu = (min_s + max_s) / 2
    sigma = (max_s - min_s) / 6
    t = max(min_s, min(max_s, random.gauss(mu, sigma)))
    await asyncio.sleep(t)


async def micro_pause():
    """Very short pause (50-200ms) between sub-actions."""
    await asyncio.sleep(random.uniform(0.05, 0.2))


# ── Mouse movement (Bézier curves) ─────────────────────────────────────────

def _bezier_point(t: float, p0: tuple, p1: tuple, p2: tuple, p3: tuple) -> tuple:
    """Evaluate cubic Bézier curve at parameter t."""
    u = 1 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return (x, y)


def _generate_bezier_path(start: tuple, end: tuple, num_points: int = 20) -> list:
    """
    Generate a natural-looking mouse path using cubic Bézier curves.

    Control points are offset randomly to create the slight curves
    and overshoots that characterize real mouse movement.
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy)

    # Control points with random offset (creates natural curve)
    spread = max(30, dist * 0.3)
    cp1 = (
        start[0] + dx * 0.25 + random.uniform(-spread, spread) * 0.5,
        start[1] + dy * 0.25 + random.uniform(-spread, spread) * 0.5,
    )
    cp2 = (
        start[0] + dx * 0.75 + random.uniform(-spread, spread) * 0.3,
        start[1] + dy * 0.75 + random.uniform(-spread, spread) * 0.3,
    )

    # Occasional overshoot: extend past the target then correct
    if random.random() < 0.3:
        overshoot = random.uniform(3, 12)
        angle = math.atan2(dy, dx)
        overshoot_point = (
            end[0] + overshoot * math.cos(angle),
            end[1] + overshoot * math.sin(angle),
        )
        # First move to overshoot, then correct to target
        path = []
        for i in range(num_points - 3):
            t = i / (num_points - 4)
            path.append(_bezier_point(t, start, cp1, cp2, overshoot_point))
        # Correction movement
        for i in range(3):
            t = i / 2
            px = overshoot_point[0] + (end[0] - overshoot_point[0]) * t
            py = overshoot_point[1] + (end[1] - overshoot_point[1]) * t
            path.append((px, py))
        return path

    return [_bezier_point(i / (num_points - 1), start, cp1, cp2, end) for i in range(num_points)]


async def move_mouse_to(page, x: float, y: float, current_pos: tuple = None):
    """
    Move mouse along a Bézier curve path to target coordinates.

    Args:
        page: nodriver page/tab object
        x, y: target coordinates
        current_pos: current mouse position, or random start if None
    """
    if current_pos is None:
        current_pos = (random.uniform(100, 500), random.uniform(100, 400))

    path = _generate_bezier_path(current_pos, (x, y))

    for point in path:
        await page.send(
            "Input.dispatchMouseEvent",
            type="mouseMoved",
            x=int(point[0]),
            y=int(point[1]),
        )
        # Variable speed: slower at start/end (acceleration/deceleration)
        await asyncio.sleep(random.uniform(0.005, 0.025))


async def human_click(page, element):
    """
    Click an element with natural mouse movement.

    1. Get element position
    2. Move mouse along Bézier curve
    3. Brief pause (human processing)
    4. Click with slight position jitter
    """
    try:
        # Get element's bounding box
        box = await element.get_position()
        if box is None:
            await element.click()
            return

        # Target center with small random offset
        target_x = box.x + box.width / 2 + random.uniform(-3, 3)
        target_y = box.y + box.height / 2 + random.uniform(-2, 2)

        await move_mouse_to(page, target_x, target_y)
        await human_sleep(0.1, 0.35)
        await element.click()
    except Exception:
        # Fallback to direct click if position detection fails
        await element.click()


# ── Typing ──────────────────────────────────────────────────────────────────

async def human_type(element, text: str):
    """
    Type text character by character with realistic timing.

    Features:
    - Variable inter-key delay (40-180ms base, faster for common bigrams)
    - Occasional longer pauses (~7% chance) simulating thought
    - Rare typo-then-backspace (~2% chance) for authenticity
    """
    # Common fast bigrams (keys physically close or commonly typed together)
    fast_bigrams = {"th", "he", "in", "er", "an", "re", "on", "at", "en", "nd"}

    for i, char in enumerate(text):
        # Base typing speed
        if i > 0 and text[i-1:i+1].lower() in fast_bigrams:
            delay = random.uniform(0.03, 0.10)  # Faster for common pairs
        else:
            delay = random.uniform(0.04, 0.18)

        # Occasional thinking pause (~7%)
        if random.random() < 0.07:
            delay += random.uniform(0.3, 0.7)

        # Rare typo simulation (~2%) — type wrong char, pause, backspace, correct
        if random.random() < 0.02 and char.isalpha():
            wrong = chr(ord(char) + random.choice([-1, 1]))
            await element.send_keys(wrong)
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await element.send_keys("\b")  # Backspace
            await asyncio.sleep(random.uniform(0.05, 0.15))

        await element.send_keys(char)
        await asyncio.sleep(delay)


# ── Scrolling ───────────────────────────────────────────────────────────────

async def random_scroll(page):
    """Natural scroll behavior — occasionally scroll while reading."""
    if random.random() < 0.4:
        scroll_amount = random.randint(80, 350)
        direction = random.choice([1, 1, 1, -1])  # Bias toward scrolling down
        await page.evaluate(f"window.scrollBy(0, {scroll_amount * direction})")
        await human_sleep(0.3, 1.0)


# ── Time-of-day variation ──────────────────────────────────────────────────

def time_of_day_multiplier(hour: int) -> float:
    """
    Vary check interval based on time of day.

    Returns a multiplier (0.8-1.5) for the base interval:
    - Midday (10-15): slightly faster checks (0.8-0.9x)
    - Morning/evening edges: slower checks (1.2-1.5x)
    - Normal hours: 1.0x
    """
    if 10 <= hour <= 15:
        return random.uniform(0.8, 0.95)
    elif hour < 8 or hour > 21:
        return random.uniform(1.2, 1.5)
    else:
        return random.uniform(0.95, 1.1)
