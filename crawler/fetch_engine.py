"""Unified, polite page fetcher.

Two tiers:
 1. Plain HTTP (requests) -- fast, cheap, used for the large majority of
    sites that render their HTML on the server.
 2. A headless-browser render (Playwright/Chromium), used ONLY when tier 1
    comes back looking like an empty JS-application shell (very little text,
    no offer/discount vocabulary, a "Loading" placeholder, etc). Several
    Qatar mall sites are single-page JS applications -- mallofqatar.com.qa is
    a confirmed example -- so a requests-only crawler silently sees nothing
    on those pages and can never find or expire their offers correctly.
    Rendering is the real fix, not a workaround: it reads the same markup a
    visitor's browser would, nothing is bypassed or spoofed.

Every fetch is robots.txt-checked first (urllib.robotparser) and skipped,
loudly, if disallowed -- this module never bypasses access restrictions.
robots.txt itself is cached per-host for the run so it is fetched once.
"""
import re
import time
import logging
import urllib.robotparser
from urllib.parse import urlparse

import requests

log = logging.getLogger("qatar-offers.fetch")

UA_STRING = "Mozilla/5.0 (compatible; QatarOffersBot/1.0; +https://github.com/)"
UA = {"User-Agent": UA_STRING}

# Markers that strongly suggest the HTTP response is a JS-app shell rather
# than real content: very little visible text, or a framework boot div with
# nothing else, etc. Conservative on purpose -- false positives just cost an
# extra (also polite, also robots-checked) render; false negatives mean a
# page silently returns no offers, which is the status quo without this file.
_JS_SHELL_HINTS = re.compile(r'id="(root|app|__next|__nuxt)"|ng-version=|data-reactroot', re.I)

_robots_cache = {}


def _robots_for(url):
    parsed = urlparse(url)
    host = f"{parsed.scheme}://{parsed.netloc}"
    if host in _robots_cache:
        return _robots_cache[host]
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(host + "/robots.txt")
    try:
        rp.read()
    except Exception as e:
        log.warning("robots.txt unreadable for %s (%s) -- treating as allow-all per RFC default", host, e)
        rp = None  # robotparser treats a failed read as allow-all already, but be explicit in logs
    _robots_cache[host] = rp
    return rp


def allowed(url):
    """True unless robots.txt for this host explicitly disallows our UA on this path."""
    rp = _robots_for(url)
    if rp is None:
        return True
    try:
        return rp.can_fetch(UA_STRING, url)
    except Exception:
        return True


def looks_like_js_shell(html):
    """Heuristic: short visible-text body plus an SPA mount-point marker."""
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    visible = re.sub(r"\s+", " ", text).strip()
    return len(visible) < 600 and bool(_JS_SHELL_HINTS.search(html))


class FetchResult:
    def __init__(self, text, status, rendered, url):
        self.text = text
        self.status = status
        self.rendered = rendered
        self.url = url


_playwright_available = None


def _check_playwright():
    global _playwright_available
    if _playwright_available is not None:
        return _playwright_available
    try:
        import playwright.sync_api  # noqa: F401
        _playwright_available = True
    except Exception:
        _playwright_available = False
    return _playwright_available


def render_with_browser(url, timeout_ms=25000):
    """Render a page with headless Chromium and return its final DOM HTML.
    Returns None (never raises) if Playwright/its browser isn't installed --
    callers fall back to the plain-HTTP text they already have."""
    if not _check_playwright():
        log.warning("Playwright not installed -- cannot render JS pages; run `pip install playwright "
                    "&& playwright install chromium` to enable this (see START_HERE.md)")
        return None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=UA_STRING)
            page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        log.error("render failed for %s: %s", url, str(e)[:200])
        return None


def fetch(url, retries=3, allow_render=True):
    """Fetch a URL politely: robots-checked, retried with backoff, and
    upgraded to a headless-browser render if the plain response looks like
    an empty JS-app shell. Raises requests.exceptions.RequestException after
    exhausting retries (same contract as plain requests.get), so existing
    callers' except blocks keep working."""
    if not allowed(url):
        log.warning("robots.txt disallows fetching %s -- skipping, not bypassing", url)
        raise PermissionError(f"robots.txt disallows fetching {url}")

    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            if allow_render and looks_like_js_shell(r.text):
                rendered = render_with_browser(url)
                if rendered:
                    log.info("rendered JS-app page for real content: %s", url)
                    return FetchResult(rendered, r.status_code, True, url)
                log.warning("page looks JS-rendered but no renderer available/failed: %s", url)
            return FetchResult(r.text, r.status_code, False, url)
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < retries:
                wait = min(2 ** attempt, 20)
                log.warning("fetch retry %s/%s for %s in %ss (%s)", attempt, retries, url, wait, e)
                time.sleep(wait)
    log.error("fetch failed after %s attempts: %s (%s)", retries, url, last_exc)
    raise last_exc
