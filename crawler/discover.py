"""Dynamic source discovery.

Finds candidate Qatar mall/store/restaurant official pages that are NOT yet
in sources.json, using a real web search API -- not a hardcoded URL list.
Runs only if a search provider is configured via environment variables (see
START_HERE.md); otherwise it exits cleanly and says so, rather than
fabricating results.

Safety rules, all enforced here:
 - Every candidate URL is robots.txt-checked before being queued, and
   dropped (not bypassed) if disallowed.
 - Nothing is added to sources.json automatically. Candidates are written to
   data/discovered_sources.json as "pending" for a human to review in the
   admin dashboard and, if approved, add to sources.json themselves. This is
   the "queue uncertain things for review" rule applied to source discovery,
   not just to individual offers.
 - A candidate must match Qatar + retail/mall/restaurant vocabulary in its
   search snippet before being queued, to cut obvious noise; it is still
   just a candidate, not a verified source, until a human approves it and a
   real crawl of it succeeds.
"""
import os
import re
import json
import time
import pathlib
import logging
import hashlib
import urllib.request
import urllib.parse

import fetch_engine

log = logging.getLogger("qatar-offers.discover")

ROOT = pathlib.Path(__file__).resolve().parent.parent

QATAR_HINT = re.compile(r"\bqatar\b|\bdoha\b|\blusail\b|\bal rayyan\b|\.qa\b", re.I)
RETAIL_HINT = re.compile(
    r"\bmall\b|\bshopping\b|\boffer|\bpromotion|\bsale\b|\bdiscount|\brestaurant\b|"
    r"\bstore\b|\bbrand\b|\bsupermarket\b|\bhypermarket\b", re.I
)
EXCLUDE_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com",
    "youtube.com", "linkedin.com", "pinterest.com",  # social platforms: see note below
}
# Note on social pages: the brief asks for discovery across "public social
# pages" too. We deliberately do NOT scrape Facebook/Instagram/TikTok/X
# directly here: all four require authentication or a paid official API for
# programmatic access, and scraping them without one would mean bypassing
# their access restrictions, which this project will not do. A social
# platform becomes usable the same way any other source does: by configuring
# its official Graph/Business API credentials as environment variables (see
# START_HERE.md) and adding a small adapter -- none are configured here, so
# none are queried, and this module says so rather than silently skipping it.


def _search_provider():
    """Returns (name, search_fn) for whichever provider has credentials set,
    or (None, None) if none are configured."""
    if os.environ.get("GOOGLE_CSE_API_KEY") and os.environ.get("GOOGLE_CSE_CX"):
        return "google_cse", _search_google_cse
    if os.environ.get("BING_SEARCH_KEY"):
        return "bing", _search_bing
    if os.environ.get("SERPAPI_KEY"):
        return "serpapi", _search_serpapi
    return None, None


def _http_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _search_google_cse(query, num=10):
    key = os.environ["GOOGLE_CSE_API_KEY"]
    cx = os.environ["GOOGLE_CSE_CX"]
    url = ("https://www.googleapis.com/customsearch/v1?" +
           urllib.parse.urlencode({"key": key, "cx": cx, "q": query, "num": min(num, 10)}))
    data = _http_json(url)
    return [{"title": i.get("title"), "url": i.get("link"), "snippet": i.get("snippet")}
            for i in data.get("items", [])]


def _search_bing(query, num=10):
    key = os.environ["BING_SEARCH_KEY"]
    url = "https://api.bing.microsoft.com/v7.0/search?" + urllib.parse.urlencode({"q": query, "count": num})
    data = _http_json(url, headers={"Ocp-Apim-Subscription-Key": key})
    return [{"title": i.get("name"), "url": i.get("url"), "snippet": i.get("snippet")}
            for i in data.get("webPages", {}).get("value", [])]


def _search_serpapi(query, num=10):
    key = os.environ["SERPAPI_KEY"]
    url = ("https://serpapi.com/search.json?" +
           urllib.parse.urlencode({"engine": "google", "q": query, "num": num, "api_key": key}))
    data = _http_json(url)
    return [{"title": i.get("title"), "url": i.get("link"), "snippet": i.get("snippet")}
            for i in data.get("organic_results", [])]


DEFAULT_QUERIES = [
    "Qatar mall official website offers",
    "Doha shopping mall promotions official site",
    "Qatar supermarket weekly offers official website",
    "Qatar restaurant discount promotion official site",
    "Qatar electronics store offers official website",
    "Qatar fashion brand store Doha official website offers",
]


def discover(queries=None, per_query=8, existing_domains=None):
    """Run discovery. Returns a list of candidate dicts. Never raises for a
    missing provider -- returns [] and logs why, so callers/CI don't crash
    on a plain `python crawler/discover.py` with no keys configured."""
    provider_name, search_fn = _search_provider()
    if not provider_name:
        log.warning(
            "No search provider configured (set GOOGLE_CSE_API_KEY+GOOGLE_CSE_CX, "
            "BING_SEARCH_KEY, or SERPAPI_KEY) -- discovery cannot run. "
            "This is expected until the project owner provisions one; see START_HERE.md."
        )
        return []

    existing_domains = existing_domains or set()
    queries = queries or DEFAULT_QUERIES
    candidates, seen = [], set()

    for q in queries:
        try:
            results = search_fn(q, num=per_query)
        except Exception as e:
            log.error("search failed for %r via %s: %s", q, provider_name, str(e)[:200])
            continue
        for r in results:
            url = r.get("url")
            if not url:
                continue
            domain = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
            if domain in existing_domains or any(domain.endswith(d) for d in EXCLUDE_DOMAINS):
                continue
            text = f"{r.get('title','')} {r.get('snippet','')} {url}"
            if not (QATAR_HINT.search(text) and RETAIL_HINT.search(text)):
                continue
            if url in seen:
                continue
            seen.add(url)
            if not fetch_engine.allowed(url):
                log.info("dropping candidate disallowed by robots.txt: %s", url)
                continue
            cid = hashlib.sha1(url.encode()).hexdigest()[:16]
            candidates.append({
                "id": cid, "url": url, "title": r.get("title"), "snippet": r.get("snippet"),
                "found_via_query": q, "provider": provider_name,
                "status": "pending",  # pending | approved | rejected -- set by a human in admin
            })
        time.sleep(1)  # politeness between search calls
    return candidates


def load_queue(path=None):
    path = pathlib.Path(path or ROOT / "data/discovered_sources.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"updated": None, "candidates": []}


def save_queue(queue, path=None):
    path = pathlib.Path(path or ROOT / "data/discovered_sources.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue, ensure_ascii=False, indent=1), encoding="utf-8")


def run(root=ROOT):
    """Merge newly discovered candidates into the review queue, keeping any
    existing human decision (status) on a URL already seen before."""
    import datetime as dt
    root = pathlib.Path(root)
    sources = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    existing_domains = set()
    for m in sources.get("malls", []):
        for p in m.get("pages", []):
            existing_domains.add(urllib.parse.urlparse(p["url"]).netloc.lower().removeprefix("www."))

    queue = load_queue(root / "data/discovered_sources.json")
    by_url = {c["url"]: c for c in queue.get("candidates", [])}
    new = discover(existing_domains=existing_domains)
    for c in new:
        if c["url"] in by_url:
            c["status"] = by_url[c["url"]]["status"]  # preserve a human's prior decision
        by_url[c["url"]] = c

    queue = {"updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).strftime("%Y-%m-%d %H:%M AST"),
             "provider": _search_provider()[0], "candidates": list(by_url.values())}
    save_queue(queue, root / "data/discovered_sources.json")
    log.info("discovery run: %d candidates in queue (%d new)", len(by_url), len(new))
    return queue


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
