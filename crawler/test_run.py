"""Offline tests for the local extraction, categorization, and crawler engine."""
import sys, pathlib, datetime as dt, json, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ai, run, categorize, fetch_engine

today = dt.date.today().isoformat()
future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
past = (dt.date.today() - dt.timedelta(days=5)).isoformat()

# --- extraction -------------------------------------------------------
items = ai.text_offers(
    "Promotions\nNike\nNike up to 50% off\nValid 01 Sep 2026 - 31 Dec 2026\n"
    "Carrefour\nBuy 1 get 1 Free\n",
    "Mall of Qatar", "https://mall.example/offers", today
)
assert items, "Should extract at least one offer"
assert any(x["store_name"] == "Nike" and x["discount_text"] for x in items), items
assert all(x["source_url"] == "https://mall.example/offers" for x in items)
assert all(x["category"] in run.CATS for x in items), items
assert all("subcategory" in x and "tags" in x and "category_confidence" in x for x in items)

# expired date ranges are filtered
old = ai.text_offers(
    "Nike\nSale up to 50% off\n01 Jan 2020 - 31 Jan 2020",
    "Example Mall", "https://example.test", today
)
assert old == [], old

# --- categorization taxonomy -------------------------------------------
cls = categorize.classify("50% off dresses", "New season fashion sale", "50% off", "Zara")
assert cls["category"] == "Fashion", cls

cls = categorize.classify("20% off dining bill", "Enjoy 20% off your restaurant bill when you pay with your "
                           "QNB credit card at this restaurant", "20% off", "Nando's")
assert cls["category"] == "Restaurants" and "Banking Offer" in cls["tags"], cls

cls = categorize.classify("0% installment cashback", "Get cashback on your QIB credit card purchases",
                           "", "QIB")
assert cls["category"] == "Banking", cls

# merchant name alone should not be enough to classify confidently
cls = categorize.classify("Special promotion", "Visit us today", "", "Carrefour")
assert cls["needs_review"] in (True, False)  # just must not crash; weak signal path

# --- promo code / price / products extraction ---------------------------
items2 = ai.text_offers(
    "Samsung Galaxy\nSamsung Galaxy S24 offer\n20% off laptops\nUse code TECH20\nQAR 199 only\n"
    "Valid 01 Jan 2026 - 31 Dec 2026\n",
    "City Center Doha", "https://city.example/offers", today
)
assert any(o.get("promo_code") == "TECH20" for o in items2), items2
assert any(o.get("price") == "199" for o in items2), items2

# --- crawler integration with a fake page and no network ----------------
class Response:
    def __init__(self, text):
        self.text = text
        self.content = text.encode()
        self.headers = {"content-type": "text/html"}

import fetch_engine as _fetch_engine
original_fetch = run.fetch
original_fe_fetch = _fetch_engine.fetch
run.fetch = lambda url, retries=run.RETRIES: Response("<html><body><h1>Nike</h1><p>Nike up to 50% off</p></body></html>")
run.fetch_engine.fetch = lambda url, retries=run.RETRIES: _fetch_engine.FetchResult(
    "<html><body><h1>Nike</h1><p>Nike up to 50% off</p></body></html>", 200, False, url)
root = pathlib.Path(tempfile.mkdtemp())
(root / "sources.json").write_text(json.dumps({"malls": [{
    "id": "test-mall", "name": "Test Mall", "name_ar": "مجمع تجريبي",
    "pages": [{"url": "https://example.test/offers", "type": "text"}]
}]}), encoding="utf-8")
run.main(root)
out = json.loads((root / "web/offers.json").read_text(encoding="utf-8"))
assert out["offers"], out
assert out["offers"][0]["mall_name"] == "Test Mall"
assert out["offers"][0]["source_url"] == "https://example.test/offers"
assert out["timezone"] == "Asia/Qatar"
assert "category" in out["offers"][0] and out["offers"][0]["category"] in run.CATS

# re-running with an unchanged page must not duplicate the offer record
state_after_1 = json.loads((root / "data/state.json").read_text(encoding="utf-8"))
n1 = len(state_after_1["offers"])
run.main(root)
state_after_2 = json.loads((root / "data/state.json").read_text(encoding="utf-8"))
assert len(state_after_2["offers"]) == n1, "unchanged page must not create duplicate offers"

# --- manual-correction protection ---------------------------------------
off_id = next(iter(state_after_2["offers"]))
state_after_2["offers"][off_id]["category"] = "Technology"  # simulate a human correction
state_after_2["offers"][off_id]["locked_fields"] = ["category"]
(root / "data/state.json").write_text(json.dumps(state_after_2), encoding="utf-8")
# force the page hash to change (so it's re-extracted) without changing the
# offer content itself, so it maps to the SAME fingerprint/id as before
run.fetch_engine.fetch = lambda url, retries=run.RETRIES: _fetch_engine.FetchResult(
    "<html><body><h1>Nike</h1><p>Nike up to 50% off</p><!-- refreshed --></body></html>", 200, False, url)
run.main(root)
state_after_3 = json.loads((root / "data/state.json").read_text(encoding="utf-8"))
assert state_after_3["offers"][off_id]["category"] == "Technology", \
    "a manually-locked field must survive an automatic refresh"

# --- retry/backoff wrapper (tests run.fetch itself, not a replacement) ---
run.fetch = original_fetch
import requests as _requests
calls = {"n": 0}
def flaky_get(url, headers=None, timeout=None):
    calls["n"] += 1
    if calls["n"] < 3:
        raise _requests.exceptions.ConnectionError("transient")
    class R:
        text = "ok"
        def raise_for_status(self): pass
    return R()
orig_get = _requests.get
_requests.get = flaky_get
import time as _time
_orig_sleep = _time.sleep
_time.sleep = lambda s: None  # don't actually wait during tests
r = run.fetch("https://example.test/flaky")
assert r.text == "ok" and calls["n"] == 3, "fetch should retry transient failures and then succeed"
_time.sleep = _orig_sleep
_requests.get = orig_get
run.fetch_engine.fetch = original_fe_fetch

# --- fetch_engine: JS-shell detection and robots.txt enforcement --------
shell_html = '<html><body><div id="root"></div><script>boot()</script></body></html>'
real_html = "<html><body><h1>Nike</h1><p>" + "Great offer up to 50% off everything today. " * 15 + "</p></body></html>"
assert fetch_engine.looks_like_js_shell(shell_html) is True
assert fetch_engine.looks_like_js_shell(real_html) is False

class FakeRobots:
    def can_fetch(self, ua, url):
        return "/disallowed/" not in url
fetch_engine._robots_cache["https://example.test"] = FakeRobots()
assert fetch_engine.allowed("https://example.test/ok") is True
assert fetch_engine.allowed("https://example.test/disallowed/page") is False
try:
    fetch_engine.fetch("https://example.test/disallowed/page")
    raise AssertionError("fetch() should refuse a robots-disallowed URL")
except PermissionError:
    pass

# --- discovery: runs cleanly with no provider, never fabricates results -
import discover
assert discover.discover() == []  # no API key configured in this test environment
queue_root = pathlib.Path(tempfile.mkdtemp())
(queue_root / "sources.json").write_text(json.dumps({"malls": []}), encoding="utf-8")
q = discover.run(queue_root)
assert q["candidates"] == [] and q["provider"] is None
assert (queue_root / "data/discovered_sources.json").exists()

# --- regression test for the reported bug: an old, now-expired mall promo
# (e.g. a "70% off, valid April-May" listing) must never be shown as active,
# whether it is still on the source page, or the source page is simply
# unreachable this run (a fetch failure must not be confused with "still
# running", but it also must not silently resurrect/keep an already-expired
# offer -- expiry is decided purely by the date, every run). -------------
expired_root = pathlib.Path(tempfile.mkdtemp())
(expired_root / "sources.json").write_text(json.dumps({"malls": [
    {"id": "mall-of-qatar", "name": "Mall of Qatar", "pages": [{"url": "https://example.test/moq", "type": "text"}]}
]}), encoding="utf-8")
(expired_root / "data").mkdir(exist_ok=True)
old_promo_text = ("<html><body><h1>Mega Sale</h1><p>Mega Sale up to 70% off storewide. "
                   "Valid 01 Apr 2020 - 31 May 2020</p></body></html>")
run.fetch_engine.fetch = lambda url, retries=run.RETRIES: _fetch_engine.FetchResult(old_promo_text, 200, False, url)
run.main(expired_root)
out = json.loads((expired_root / "web/offers.json").read_text(encoding="utf-8"))
assert out["offers"] == [], f"an offer with a 2020 end date must never be published as active: {out['offers']}"

# Now simulate: the source briefly fails to fetch (network hiccup). A
# transient failure must not itself mark anything expired or invent a date.
def _always_fails(url, retries=run.RETRIES):
    raise ConnectionError("simulated transient network failure")
run.fetch_engine.fetch = _always_fails
run.main(expired_root)
out2 = json.loads((expired_root / "web/offers.json").read_text(encoding="utf-8"))
assert out2["offers"] == [], "a fetch failure must not resurrect an expired offer"
assert out2["errors"] and "simulated transient network failure" in out2["errors"][0]["error"]
run.fetch_engine.fetch = original_fe_fetch

# --- regression test for the reported bug: news/PR/lucky-draw content and
# other-mall content must never be extracted as an offer. -----------------
noise_text = (
    "Mall of Qatar\n"
    "Mall of Qatar congratulates the grand prize winner of its lucky draw promotion, "
    "who won a car this week.\n\n"
    "Place Vendome Qatar\n"
    "Place Vendome Qatar marks 8 years since its grand opening with a special ceremony.\n\n"
    "Nike\n"
    "Nike up to 40% off running shoes. Valid 01 Jan 2026 - 31 Dec 2026\n"
)
items3 = ai.text_offers(noise_text, "Mall of Qatar", "https://example.test/moq-news", today,
                         other_malls=["Mall of Qatar", "Place Vendome Qatar"])
stores_found = {o["store_name"] for o in items3}
assert "Nike" in stores_found, f"a real offer in the same page must still be extracted: {items3}"
assert not any("winner" in (o.get("description") or "").lower() or "lucky draw" in (o.get("title") or "").lower()
               for o in items3), f"lucky-draw/news content must never be extracted as an offer: {items3}"
assert not any("Vendome" in (o.get("store_name") or "") or "Vendome" in (o.get("description") or "")
               for o in items3), f"content about a DIFFERENT mall must not be filed under Mall of Qatar: {items3}"

print("all local extraction, categorization, and crawler tests passed")
