#!/usr/bin/env python3
"""
Shoji Appointment Checker
─────────────────────────
Navigates the vCita booking flow for Hair Studio Picnic, checks whether
Shoji | A'dam has any available days in DESIRED_START..DESIRED_END, and
sends an email if new availability is found.

Run manually:   python check_shoji.py
Debug (visible browser):  python check_shoji.py --debug
"""

from __future__ import annotations

import os
import re
import sys
import json
import argparse
import calendar
import logging
import smtplib
from datetime import date, datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION  ← edit these freely
# ═══════════════════════════════════════════════════════════════════════════════

DESIRED_START = date(2026, 3, 8)    # First acceptable date (inclusive)
DESIRED_END   = date(2026, 6, 3)    # Last  acceptable date (inclusive)

# Which weekdays to accept. Uses Python's weekday numbers:
#   0=Monday  1=Tuesday  2=Wednesday  3=Thursday  4=Friday  5=Saturday  6=Sunday
#
# Examples:
#   DESIRED_DAYS = set()      → any day of the week
#   DESIRED_DAYS = {6}        → Sundays only
#   DESIRED_DAYS = {5, 6}     → Saturdays and Sundays
#   DESIRED_DAYS = {0, 6}     → Mondays and Sundays
DESIRED_DAYS: set[int] = {6}        # Sundays only

# ── Email settings — set all four as GitHub Secrets (none hardcoded here) ────
#
# Recommended: SendGrid free SMTP relay (sendgrid.com → free tier → API key)
#   SMTP_HOST = smtp.sendgrid.net   SMTP_PORT = 587   SMTP_USER = apikey
#   SMTP_PASS = <your SendGrid API key>
#
# Alternative: Gmail App Password
#   SMTP_HOST = smtp.gmail.com      SMTP_PORT = 465   SMTP_USER = <sender address>
#   SMTP_PASS = <Gmail App Password>
#
EMAIL_SENDER    = os.environ.get("EMAIL_SENDER", "")     # "From" address
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "")  # "To"  address
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.sendgrid.net")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "apikey")   # literal "apikey" for SendGrid
SMTP_PASS = os.environ.get("SMTP_PASS", "")         # SendGrid API key or Gmail App Password

# ═══════════════════════════════════════════════════════════════════════════════

BOOKING_URL = (
    "https://live.vcita.com/site/hairstudiopicnic/online-scheduling"
    "?action=3scwrr4eyxmb2rpc"
    "&o=cHJvZmlsZV9wYWdl"
    "&s=aHR0cHM6Ly9saXZlLnZjaXRhLmNvbS9zaXRlL2hhaXJzdHVkaW9waWNuaWM%3D"
    "&showClose=true"
)

SCRIPT_DIR = Path(__file__).parent
LOG_FILE   = SCRIPT_DIR / "checker.log"
STATE_FILE = SCRIPT_DIR / "last_notified.json"

# ── Logging (writes to both file and stdout) ────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3,    "april": 4,
    "may": 5,     "june": 6,     "july": 7,      "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_month_year(text: str) -> tuple[int, int] | None:
    """Parse 'March 2026' → (2026, 3). Returns None on failure."""
    m = re.search(r"([a-zA-Z]+)\s+(\d{4})", text)
    if not m:
        return None
    month = MONTH_MAP.get(m.group(1).lower())
    if not month:
        return None
    return int(m.group(2)), month


def load_last_notified() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()).get("dates", []))
    return set()


def save_notified(dates: list[date]) -> None:
    STATE_FILE.write_text(json.dumps({"dates": [d.isoformat() for d in dates]}))


# ── Email ────────────────────────────────────────────────────────────────────

def _day_filter_label() -> str:
    """Human-readable description of DESIRED_DAYS for use in emails/logs."""
    if not DESIRED_DAYS:
        return "any day"
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return " / ".join(day_names[d] for d in sorted(DESIRED_DAYS))


def send_email(available_dates: list[date]) -> None:
    lines = "\n".join(
        f"  • {d.strftime('%A, %d %B %Y')}" for d in sorted(available_dates)
    )
    day_label = _day_filter_label()
    body = (
        f"Shoji | A'dam has open slots in your window\n"
        f"({DESIRED_START.strftime('%d %b')} – {DESIRED_END.strftime('%d %b %Y')}"
        f", {day_label}):\n\n"
        f"{lines}\n\n"
        f"Book now → {BOOKING_URL}\n\n"
        f"— Your Shoji appointment checker"
    )

    msg = MIMEMultipart()
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECIPIENT
    msg["Subject"] = f"Shoji slot open — {len(available_dates)} day(s) available"
    msg.attach(MIMEText(body, "plain"))

    if SMTP_PORT == 465:
        # SSL from the start (Gmail-style)
        with smtplib.SMTP_SSL(SMTP_HOST, 465) as srv:
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
    else:
        # STARTTLS (SendGrid / most other providers)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

    log.info("Email sent — %d date(s): %s", len(available_dates), [d.isoformat() for d in available_dates])


# ── Scraping ─────────────────────────────────────────────────────────────────

def _save_debug_screenshot(page, label: str = "debug") -> None:
    path = SCRIPT_DIR / f"{label}.png"
    try:
        page.screenshot(path=str(path))
        log.info("Screenshot saved → %s", path.name)
    except Exception:
        pass


def _find_booking_ctx(page):
    """
    Return the Frame that contains the booking widget, or `page` itself.

    The vCita booking widget is embedded inside an <iframe> on the host page.
    page.evaluate() / page.locator() only see the *outer* page, so we must
    locate the iframe's Frame object and use that for all subsequent operations.
    """
    frames = page.frames
    log.info("Frames on page: %d", len(frames))
    for i, frame in enumerate(frames):
        log.info("  [%d] %s", i, frame.url)

    # Pass 1 — fast URL match: the vCita booking widget lives at clients.vcita.com
    for frame in frames[1:]:
        if "clients.vcita.com" in frame.url:
            log.info("Booking frame found by URL: %s", frame.url)
            return frame

    # Pass 2 — content match fallback: look for location text inside any frame
    for frame in frames[1:]:
        try:
            has_content = frame.evaluate(
                "() => document.body && document.body.textContent.includes('Rotterdam')"
            )
            if has_content:
                log.info("Booking frame found by content: %s", frame.url)
                return frame
        except Exception:
            continue

    log.info("No booking iframe found — using main page context")
    return page


def _js_click_text(ctx, text: str) -> bool:
    """
    Click an element whose DIRECT text node is exactly `text`, within `ctx`
    (a Playwright Frame or Page).  TreeWalker finds raw text nodes so it
    bypasses child icon elements and never over-matches longer strings.
    """
    return bool(ctx.evaluate(
        """(text) => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const node = walker.currentNode;
                if (node.textContent.trim() === text) {
                    const el = node.parentElement;
                    if (el && el.getBoundingClientRect().height > 0) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        }""",
        text
    ))


def _get_calendar_month_year(ctx) -> str:
    """Find 'Month Year' text (e.g. 'March 2026') in the booking frame."""
    result = ctx.evaluate("""
        () => {
            const pattern = /^[A-Z][a-z]+ \\d{4}$/;
            for (const el of document.querySelectorAll('span, div, p, h1, h2, h3, h4')) {
                const txt = el.textContent.trim();
                if (pattern.test(txt)) return txt;
            }
            return null;
        }
    """)
    return result or ""


def _get_selected_day_num(ctx) -> int | None:
    """
    Return the day number of the currently selected (gold-circled) calendar cell.
    Uses three detection methods so it works regardless of exact color values:
      1. Non-transparent / non-white background (the gold circle)
      2. Near-white text (light text on dark background)
      3. A CSS class that contains 'select', 'active', 'chosen', or 'current'
    """
    return ctx.evaluate("""
        () => {
            for (const td of document.querySelectorAll('table td')) {
                const style = window.getComputedStyle(td);
                const bg    = style.backgroundColor;
                const color = style.color;
                const cls   = (td.className || '').toLowerCase();

                // Method 1 — colored background (gold circle)
                const bgParts = bg.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                const hasColorBg = bgParts && !(
                    bg === 'rgba(0, 0, 0, 0)' ||
                    (parseInt(bgParts[1]) > 240 &&
                     parseInt(bgParts[2]) > 240 &&
                     parseInt(bgParts[3]) > 240)
                );

                // Method 2 — near-white text
                const cParts = color.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                const hasLightText = cParts &&
                    parseInt(cParts[1]) > 200 &&
                    parseInt(cParts[2]) > 200 &&
                    parseInt(cParts[3]) > 200;

                // Method 3 — class name
                const hasSelectedClass = ['select','active','chosen','current','highlight']
                    .some(c => cls.includes(c));

                if (hasColorBg || hasLightText || hasSelectedClass) {
                    const n = parseInt(td.textContent.trim());
                    if (n >= 1 && n <= 31) return n;
                }
            }
            return null;
        }
    """)


def _get_visible_time_slots(ctx) -> list[str]:
    """
    Return all visible time-slot texts, e.g. ['13:00', '14:00'].
    Uses JS to search buttons, divs, spans — anything with HH:MM text and
    non-zero dimensions — because the slots may not be <button> elements.
    """
    result = ctx.evaluate("""
        () => {
            const pattern = /^\\d{1,2}:\\d{2}$/;
            const seen    = new Set();
            const slots   = [];
            const tags    = 'button,div,span,a,li,[role="button"]';
            for (const el of document.querySelectorAll(tags)) {
                const txt  = el.textContent.trim();
                const rect = el.getBoundingClientRect();
                if (pattern.test(txt) && !seen.has(txt) &&
                    rect.width > 0 && rect.height > 0 && rect.height < 120) {
                    slots.push(txt);
                    seen.add(txt);
                }
            }
            return slots;
        }
    """)
    return result or []


def _click_next_month(ctx) -> bool:
    """Click the '>' next-month arrow. Returns False if nothing found."""
    for sel in [
        "[aria-label*='next' i]", "[aria-label*='Next' i]",
        ".next-month", "button.next", "th.next", "[class*='next']",
    ]:
        try:
            btn = ctx.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.click()
                ctx.wait_for_timeout(1_000)
                return True
        except Exception:
            continue

    # Fallback: JS scan for small '>' element
    clicked = ctx.evaluate("""
        () => {
            const texts = ['>', '›', '▶', '»', '\u203a'];
            const els = [...document.querySelectorAll('button, a, span, div')].filter(el => {
                const txt = el.textContent.trim();
                const r   = el.getBoundingClientRect();
                return texts.includes(txt) && r.width > 0 && r.width < 80 && r.height > 0;
            });
            if (!els.length) return false;
            els.sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
            els[0].click();
            return true;
        }
    """)
    if clicked:
        ctx.wait_for_timeout(1_000)
        return True
    return False


def _get_available_day_nums(ctx) -> list[int]:
    """
    Return day numbers that appear available in the current calendar view.

    The booking widget uses Vuetify (v-btn).  Unavailable days have the
    'v-btn--disabled' CSS class on their inner <button> child, which also
    sets pointer-events:none and renders text at rgba(0,0,0,0.18).

    Detection (first matching rule wins):
      1. Child has 'disabled' in its CSS class name  → unavailable
      2. Child pointer-events == 'none'              → unavailable
      3. Child text color alpha < 0.5                → unavailable (0.18 when disabled)
      4. Gold/colored background on any descendant   → available   (auto-selected day)
      5. Everything else                             → available   (dark text, no disable flag)
    """
    results = ctx.evaluate("""
        () => {
            const rows = [];

            for (const td of document.querySelectorAll('table td')) {
                // Use /^\\d{1,2}$/ instead of String(n)===txt — avoids invisible-char pitfalls
                const txt = td.textContent.trim();
                if (!/^\\d{1,2}$/.test(txt)) continue;
                const n = parseInt(txt, 10);
                if (n < 1 || n > 31) continue;

                // First child <button/div/span> — the actual clickable day element
                const firstChild = td.querySelector('*');
                let childAlpha = 1.0, childPointerEv = 'auto', childCls = '';
                if (firstChild) {
                    const cs  = window.getComputedStyle(firstChild);
                    const m   = cs.color.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
                    childAlpha     = (m && m[4] !== undefined) ? parseFloat(m[4]) : 1.0;
                    childPointerEv = cs.pointerEvents;
                    childCls       = firstChild.className || '';
                }

                // Any descendant with a non-white colored background = gold circle (selected)
                let hasBg = false;
                for (const el of td.querySelectorAll('*')) {
                    const bg = window.getComputedStyle(el).backgroundColor;
                    if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
                        const p = bg.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                        if (p && !(parseInt(p[1])>240 && parseInt(p[2])>240 && parseInt(p[3])>240)) {
                            hasBg = true; break;
                        }
                    }
                }

                rows.push({ day: n, childCls, childPointerEv, childAlpha, hasBg });
            }
            return rows;
        }
    """)

    day_nums = []
    for item in (results or []):
        cls   = item["childCls"]
        pe    = item["childPointerEv"]
        alpha = item["childAlpha"]
        hasBg = item["hasBg"]

        # ── Availability decision ────────────────────────────────────────────
        if hasBg:
            # Gold circle → auto-selected first available day
            available = True
        elif "disabled" in cls:
            # Vuetify v-btn--disabled class → no slots
            available = False
        elif pe == "none":
            # pointer-events blocked → not clickable → no slots
            available = False
        elif alpha < 0.5:
            # Semi-transparent text (alpha ≈ 0.18 when disabled) → no slots
            available = False
        else:
            # Dark opaque text, no disabled flag → slots available
            available = True

        if available:
            day_nums.append(item["day"])
            log.info("    Day %2d: ✓ available  (hasBg=%s  alpha=%.2f  pe=%s  cls=%r)",
                     item["day"], hasBg, alpha, pe, cls)
        else:
            log.debug("    Day %2d: ✗ disabled   (alpha=%.2f  pe=%s  cls=%r)",
                      item["day"], alpha, pe, cls)

    log.info("  Detected available days: %s", day_nums)
    return day_nums


def _scan_calendar_month(ctx, cal_year: int, cal_month: int) -> list[date]:
    """
    Detect available days in the calendar using CSS visual properties only.
    Filters by date range (DESIRED_START/END) and weekday (DESIRED_DAYS).
    """
    found = []
    last_of_month = date(cal_year, cal_month, calendar.monthrange(cal_year, cal_month)[1])

    start_day = DESIRED_START.day if (cal_year == DESIRED_START.year and cal_month == DESIRED_START.month) else 1
    end_day   = DESIRED_END.day   if (cal_year == DESIRED_END.year   and cal_month == DESIRED_END.month)   else last_of_month.day

    day_label = _day_filter_label()
    log.info("  Checking days %d–%d of %d-%02d  (filter: %s)",
             start_day, end_day, cal_year, cal_month, day_label)

    available_nums = _get_available_day_nums(ctx)
    log.info("  Available day numbers detected: %s", available_nums)

    for day_num in available_nums:
        if not (start_day <= day_num <= end_day):
            continue
        try:
            slot_date = date(cal_year, cal_month, day_num)
        except ValueError:
            continue
        # Apply weekday filter (skip if DESIRED_DAYS is set and this day doesn't match)
        if DESIRED_DAYS and slot_date.weekday() not in DESIRED_DAYS:
            log.debug("  ✗ Skipped %s — wrong weekday (%s)", slot_date, slot_date.strftime("%A"))
            continue
        found.append(slot_date)
        log.info("  ✓ Available: %s (%s)", slot_date, slot_date.strftime("%A"))

    return found


def scrape_available_dates(headless: bool = True) -> list[date]:
    found: list[date] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(20_000)

        try:
            # ── Step 1: Load booking page ─────────────────────────────────
            log.info("Loading booking page…")
            page.goto(BOOKING_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(3_000)

            # ── Detect iframe ─────────────────────────────────────────────
            # The vCita widget may load inside an <iframe>; all subsequent
            # operations run against `ctx` (either the iframe Frame or page).
            ctx = _find_booking_ctx(page)

            # ── Step 2: Select Amsterdam ──────────────────────────────────
            log.info("Selecting Amsterdam…")
            if not _js_click_text(ctx, "Amsterdam"):
                raise Exception("Could not find Amsterdam button")
            ctx.wait_for_selector("text=Men cut", state="visible", timeout=10_000)
            _save_debug_screenshot(page, "step2_amsterdam_expanded")

            # ── Step 3: Select Men cut | A'dam ────────────────────────────
            log.info("Selecting Men cut…")
            if not _js_click_text(ctx, "Men cut | A'dam"):
                raise Exception("Could not find Men cut service card")
            ctx.wait_for_selector("text=Book this service", state="visible", timeout=10_000)
            _save_debug_screenshot(page, "step3_men_cut_selected")

            # ── Step 4: Book this service ─────────────────────────────────
            log.info("Clicking 'Book this service'…")
            if not _js_click_text(ctx, "Book this service"):
                raise Exception("Could not find 'Book this service' link")
            ctx.wait_for_selector("text=Shoji | A'dam", state="visible", timeout=10_000)
            _save_debug_screenshot(page, "step4_book_service_clicked")

            # ── Step 5: Select Shoji | A'dam ──────────────────────────────
            log.info("Selecting Shoji…")
            if not _js_click_text(ctx, "Shoji | A'dam"):
                raise Exception("Could not find Shoji | A'dam staff card")
            ctx.wait_for_selector("text=Select date & time", state="visible", timeout=10_000)
            ctx.wait_for_timeout(1_000)
            _save_debug_screenshot(page, "step5_shoji_selected")

            # ── Step 6: Walk through the calendar ─────────────────────────
            for attempt in range(4):
                header_text = _get_calendar_month_year(ctx)
                parsed = parse_month_year(header_text)

                if not parsed:
                    log.warning("Could not read calendar header (attempt %d)", attempt)
                    _save_debug_screenshot(page, f"debug_calendar_{attempt}")
                    break

                cal_year, cal_month = parsed
                log.info("Calendar: %s", header_text)

                first_of_month = date(cal_year, cal_month, 1)
                last_of_month  = date(cal_year, cal_month, calendar.monthrange(cal_year, cal_month)[1])

                if first_of_month > DESIRED_END:
                    log.info("Calendar is past desired window — done.")
                    break

                if last_of_month >= DESIRED_START:
                    found.extend(_scan_calendar_month(ctx, cal_year, cal_month))

                if last_of_month >= DESIRED_END:
                    break

                if not _click_next_month(ctx):
                    log.warning("Could not find next-month button.")
                    _save_debug_screenshot(page, "debug_no_next_btn")
                    break

        except PlaywrightTimeoutError as e:
            log.error("Timeout: %s", e)
            _save_debug_screenshot(page, "debug_timeout")

        except Exception as e:
            log.exception("Unexpected error: %s", e)
            _save_debug_screenshot(page, "debug_error")

        finally:
            browser.close()

    return found


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Check Shoji appointment availability")
    parser.add_argument(
        "--debug", action="store_true",
        help="Show browser window (non-headless) for troubleshooting"
    )
    args = parser.parse_args()

    log.info(
        "=== Shoji Checker | %s – %s | %s ===",
        DESIRED_START.strftime("%d %b %Y"),
        DESIRED_END.strftime("%d %b %Y"),
        _day_filter_label(),
    )

    available = scrape_available_dates(headless=not args.debug)

    if not available:
        log.info("No slots found in desired range.")
        return

    # Only email if availability has changed since last notification
    last_notified = load_last_notified()
    current_set   = {d.isoformat() for d in available}

    if current_set != last_notified:
        send_email(available)
        save_notified(available)
    else:
        log.info(
            "Same %d date(s) as last notification — skipping email.", len(available)
        )


if __name__ == "__main__":
    main()
