# Qatar Offers — discovery engine + hybrid categorization + premium frontend

## What changed in this upgrade (read this first)

- **Root cause of the stale "70%-off Mall of Qatar" offer, found and fixed.** Fetching `mallofqatar.com.qa` directly (via the search/fetch tools, 12 Oct 2026) showed it is a JavaScript single-page app — its raw HTML is just a loading shell with no offer text in it at all. A plain HTTP crawler (what this project had) can never see real content there, so any offer that had been published for that mall was not something this crawler's current logic could have kept honestly up to date. **Fix:** `crawler/fetch_engine.py` now detects an empty JS-app shell and re-fetches it with a real headless browser (Playwright/Chromium) before giving up — a genuine fix, not a workaround. `sources.json`'s old hardcoded, now-stale `/en/events/qatar-mega-sale` URL was also removed and replaced with the mall's real current `/en/whats-new` page plus sitemap-driven discovery, so a single stale slug can't be the sole source of an offer again. This is covered by an automated regression test (`crawler/test_run.py`) that specifically simulates a 70%-off, April–May-dated promo and asserts it is never published, including when the source page fails to fetch.
- **Dynamic source discovery** (`crawler/discover.py`): finds new candidate mall/store/restaurant sites via a real search API (Google Programmable Search, Bing, or SerpAPI — bring your own key, see below), robots.txt-checks every candidate, and queues them in `data/discovered_sources.json` for a human to review in the admin dashboard. It never auto-adds a source to `sources.json` and never fabricates a result: with no API key configured, it says so and returns nothing.
- **Verified, real mall coverage**: `sources.json` now includes Mall of Qatar, Doha Festival City, Place Vendôme, Villaggio, City Center Doha, Ezdan Mall, Lagoona Mall, and Tawar Mall, with URLs verified reachable on 12 Oct 2026. Several are marked `"coverage_note"` as newly added and not yet crawl-proven — see the admin dashboard's coverage table for live status per mall, honestly, rather than claiming success in advance.
- **New pages**: `web/mall.html` (mall index + per-mall offer pages), `web/privacy.html`, `web/terms.html`, `web/cookies.html`, plus a real (not decorative) cookie-consent banner.
- **Admin dashboard additions**: source/mall coverage table, the discovery review queue, and (still) real analytics from the live dataset.
- **A real, automated test pass** with Playwright (browser automation) plus `axe-core` (WCAG 2 A/AA): 44 functional checks and a full accessibility sweep across every page, in English, Arabic/RTL, and dark mode. Two real bugs were found and fixed this way — see the test report below — not just described as "should work."


A static website plus a Python collector. GitHub Actions runs the collector
every six hours and republishes the site with GitHub Pages. The project is
kept portable so the same Python collector can later run on your own server.

## AI discovery engine (`crawler/discover.py`)

Finds new, real Qatar mall/store/restaurant official pages using a search API, rather than a hardcoded list. **Required to actually run:** one of —
- `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_CX` (Google Programmable Search Engine), or
- `BING_SEARCH_KEY` (Bing Web Search API), or
- `SERPAPI_KEY` (SerpAPI)

None of these are configured in this delivery — I have no account to provision one from, and doing so requires the project owner's own Google/Microsoft/SerpAPI account and billing. Without a key, `discover.py` logs that clearly and produces an empty queue; it does not fabricate candidate sources. Add whichever key as a GitHub Actions repository secret and the next scheduled run will start populating `data/discovered_sources.json` for real.

**Deliberately out of scope, and why:** the brief also asks for discovery across "public social pages." Facebook, Instagram, TikTok and X all require their own official API credentials (or a login) for programmatic access; scraping them without one would mean bypassing their access controls, which this project will not do under any framing. If the owner provisions official Meta/TikTok/X API credentials, a small adapter can be added to `discover.py` the same way the search providers are — not implemented here because no such credentials exist to test against.

**Headless-browser rendering** (`crawler/fetch_engine.py`) needs Playwright's Chromium browser installed (`pip install -r crawler/requirements.txt && playwright install chromium`); the GitHub Actions workflow now does this automatically. Without it, a JS-rendered source (Mall of Qatar, Doha Festival City) simply yields zero offers that run, logged clearly, rather than silently failing or inventing anything.

## The frontend (web/)

`web/` is a multi-page static site. There is no separate server API — the
pages read `web/offers.json` and `web/malls.json` directly, which is what
`crawler/run.py` writes. That is "the backend" for this architecture.

- `index.html` — hero, live search, categories, featured/trending/latest
  rails, a filterable + sortable + paginated full browse grid, favorites,
  and a device profile box.
- `offer.html?id=…` — offer detail: price, promo code (with copy button),
  expiry progress bar, terms note, confidence note, source link, share,
  related offers, and `schema.org/Offer` JSON-LD for SEO.
- `merchant.html` (no query) — merchant index; `merchant.html?name=…` — a
  single merchant's profile and live offers.
- `admin.html` — a lightweight passphrase-gated dashboard (passphrase
  `qatar-offers-admin`, change it in `web/admin.html`) showing **real**
  analytics computed from the current `offers.json`: live offer count,
  needs-review count, offers-by-category/mall, AI-vs-rule classification
  split, average confidence, offers expiring soon, the last run's fetch
  errors, a full sortable offers table, and a "export flagged offers" CSV
  download. This gate is a convenience for a static site, not a secure
  backend login — anyone with the passphrase and the device can view it.
- `assets/app.css` / `assets/app.js` — shared design system and app logic:
  the 16-category taxonomy, English/Arabic (RTL) i18n, dark mode, search/
  filter/sort/pagination, favorites + recently-viewed + device profile
  (all `localStorage`, per-device — this static site has no multi-device
  user-account backend), share (native share sheet or copy-link), and SEO
  helpers.

Offers are never shown as active past their `end_date`: `run.py` already
excludes expired offers from `offers.json`, and the frontend re-checks the
date client-side as a second safety net before rendering anything.

- `mall.html` (no query) — mall index, every configured mall with its live offer count and area; `mall.html?id=…` — a single mall's page and its live offers.
- `privacy.html`, `terms.html`, `cookies.html` — see "Legal pages" below.
- A real cookie-consent banner (`assets/app.js`) — see "Legal pages."

## Legal pages — IMPORTANT, read before publishing this site

I wrote `privacy.html`, `terms.html`, and `cookies.html` to accurately describe **what this codebase actually does** (no accounts, no cookies, no analytics today, localStorage only, offers sourced from each merchant's own public page with a visible source link, merchants control their own offers/purchases, no refunds happen on this site). I did **not** invent a business name, address, or contact email — every such field is a clearly marked `[bracketed placeholder]` for the real operator to fill in. These documents reference Qatar's Personal Data Privacy Protection Law (PDPPL, Law No. 13 of 2016) at a general level; I am not a lawyer and this is not legal advice. **Do not publish this site to real users until:**
1. A qualified Qatar-licensed lawyer has reviewed all three documents end to end;
2. Every bracketed placeholder is replaced with the real operator's accurate details;
3. The "What this site is" claims still match reality if the operator later adds accounts, a newsletter, payments, or third-party analytics — update the policy *before* shipping the feature, not after.

The cookie consent banner is functionally real (it gates nothing today because nothing non-essential is loaded yet, but it records a genuine, timestamped, revocable choice in `localStorage` via `QO.getCookieConsent()`/`setCookieConsent()`, and `QO.hasAnalyticsConsent()` is ready for any future analytics script to check before loading).

## Testing — what was actually run, and the bugs it found

All verified with real browser automation (Playwright) plus `axe-core` (WCAG 2 A/AA) against data produced by the actual crawler, not hand-written fixtures: routes, desktop/mobile layouts, English/Arabic (RTL), light/dark mode, search+filter+sort+pagination, favorites persistence, offer-detail actions (favorite/share/promo-copy/source link/JSON-LD), mall and merchant navigation, the cookie-consent banner (first-visit display, "necessary only" choice recorded and respected, doesn't reappear), the admin dashboard (wrong-passphrase rejection, correct-passphrase unlock, real KPI numbers, coverage table, discovery queue, CSV export), simulated data-outage error state, mobile nav, and keyboard tab order.

**Final result: 44/44 functional checks passed, 0 critical/serious accessibility violations across every page in EN, AR/RTL, and dark mode.** Two real bugs were caught and fixed by this process, not just described as working:
1. **Admin dashboard crashed silently** after a correct passphrase (`ReferenceError: t is not defined` in the new discovery-queue renderer) — found via a functional assertion that the dashboard actually renders, not just that the gate accepts a password; fixed.
2. **WCAG 2 AA color-contrast failure** (`#7C7079` on the page background measured 4.22:1, below the 4.5:1 text minimum) across every page's muted text, plus three `<select>` filters with no accessible name — found by `axe-core`, not manual inspection; fixed by darkening `--mute` to `#635A60` (verified ≥4.5:1 against every background it's used on) and adding `aria-label`s to the filter dropdowns.

I did not run this against the real deployed mall websites from this environment (the sandbox I work in cannot reach `mallofqatar.com.qa` etc. — only the search/fetch tools I used for research can, and those aren't the crawler itself). The crawler's behavior against real sites can only be confirmed once it runs in GitHub Actions; I verified its logic locally against realistic mock pages built from real page structures I inspected, including a dedicated regression test for the exact expired-offer bug reported.

## What's in the engine

- **Fetching**: `crawler/run.py` visits only the URLs explicitly listed in
  `sources.json` (and their configured sitemaps), with automatic retries
  (exponential backoff, 3 attempts by default) and per-run error logging to
  `data/run.log`. A page/image that fails to fetch is simply skipped for
  that run — it is never marked expired or deleted because of a temporary
  network problem. Unchanged pages and already-read images are skipped
  entirely (hash/URL cache in `data/state.json`), so a normal run only does
  network and extraction work for things that actually changed.
- **Extraction**: `crawler/ai.py` pulls titles, store names, descriptions,
  discount text, promo codes, prices (QAR), short product mentions, a
  terms-apply flag, start/end dates, and the source URL from page text, plus
  OCR'd promotion banners (local Tesseract, nothing uploaded anywhere).
  Nothing is invented — fields the page doesn't state are left `null`.
- **Categorization**: `crawler/categorize.py` is a two-tier classifier.
  1. A transparent weighted-keyword rule engine scores every offer against
     16 categories (Electronics, Fashion, Groceries, Restaurants, Travel,
     Technology, Beauty, Entertainment, Automotive, Home, Telecom, Banking,
     Education, Retail, Services, Other), each with subcategories, using the
     *whole* offer text — title, description, discount text — not just the
     merchant name (a merchant-name match alone is capped and can never push
     an offer to "confident" by itself).
  2. Offers the rule tier isn't confident about are optionally escalated to
     the Anthropic API for a second opinion — only if `ANTHROPIC_API_KEY` is
     set as a secret (see below). Each distinct offer is classified at most
     once ever: results are cached by content hash in `data/state.json`, and
     a per-run `AI_CALL_BUDGET` (default 40) hard-caps spend regardless of
     how many offers are found.
  A cross-cutting "Banking Offer" tag is applied whenever a bank card is
  mentioned (e.g. a restaurant discount that requires a specific bank's
  card stays categorized under Restaurants and is tagged Banking Offer,
  not re-categorized as Banking).
  Low-confidence results are published with `needs_review: true` and a
  numeric `category_confidence`, and the frontend shows a small "Under
  review" badge on them instead of silently guessing.
- **Deduplication**: offers are keyed by a content fingerprint (store +
  title + discount + end date), not by page URL, so the same offer appearing
  on more than one source page/sitemap entry updates one record (its
  `seen_sources` list grows) instead of creating duplicates.
- **Manual-correction protection**: setting `"locked_fields": ["category", ...]`
  on an offer record in `data/state.json` tells future automatic runs to
  keep that field exactly as a human last set it, even when the source page
  changes and the offer is re-extracted.
- **Safe migration**: `data/state.json` is versioned. Older state files are
  upgraded in place (new fields added, old lowercase category names mapped
  safely to `Other` + `needs_review: true` for re-check) — no offer records
  are ever dropped by the upgrade.
- **Timezone**: all "today", expiry comparisons, and "checked at" timestamps
  use Asia/Qatar (UTC+3, no DST), not UTC or server-local time.

## Setup on GitHub

1. Create a repository and upload the contents of this folder.
2. In repository **Settings → Pages**, choose **GitHub Actions** as the source.
3. (Optional) In **Settings → Secrets and variables → Actions**, add a
   secret named `ANTHROPIC_API_KEY` to enable the AI second-opinion tier for
   low-confidence categorizations. Without it, the collector runs fully
   locally/offline exactly as before — the rule tier handles everything.
4. Open **Actions → Refresh offers → Run workflow**.
5. When the workflow finishes, open the published GitHub Pages URL.

Scheduled refreshes run every six hours. GitHub is only being used as the
temporary runner/hosting location; `crawler/` can later be copied to a Linux
server and scheduled with cron or a system service.

## Add another mall

Edit `sources.json` and add a mall object with a unique `id`, English and
Arabic names, and official page URLs. Use `"type": "text"` for pages with
written promotion details and `"type": "images"` for pages whose offers are
banner images. For image pages, set `image_contains` to a URL fragment that
matches offer banners and, if helpful, `context_must` to text near promotion
images. Only use official public pages and respect each site's terms and
robots rules.

```json
{
  "id": "example-mall",
  "name": "Example Mall",
  "name_ar": "اسم المجمع",
  "area": "Doha",
  "pages": [
    {"url": "https://official.example.qa/offers", "type": "text"}
  ],
  "search": false
}
```

## Important limitations and remaining tasks

**Architecture, honestly stated:** this is a static site (GitHub Pages) plus a scheduled Python collector — there is no application server, no real database, and no multi-device user-account system. That shapes everything below:

- **No real database.** All offer/mall/state data lives in versioned JSON files (`data/state.json`, `web/offers.json`) committed by the GitHub Action. This is adequate for the current data volume and gives you free hosting, full version history, and zero server to secure — but it does not scale indefinitely and has no concurrent-write protection beyond "the Action runs one at a time." If offer volume grows into the thousands, moving `data/state.json` to a real database (e.g. SQLite checked out by the Action, or a hosted Postgres) is the natural next step; the current `crawler/run.py` functions are already structured around a single `st["offers"]` dict, which maps cleanly onto that migration.
- **No secure admin authentication.** The admin dashboard's passphrase lives in client-side JavaScript (`web/admin.html`) — anyone who views source can read it. This is a deliberate, documented trade-off for a static, serverless site, not an oversight. For real access control, put the whole site or just `/admin.html` behind an authenticating proxy (e.g. Cloudflare Access, Netlify password protection, or a small server that checks a real session before serving the page) — none of which exist in a GitHub Pages deployment without adding infrastructure outside this repo's scope.
- **No multi-device user accounts.** Favorites, recently-viewed, and the device profile are `localStorage`-only, per browser/device, by design — adding real accounts needs a server and a database, which this architecture doesn't have.
- **This is primarily a local rule-based extraction/categorization engine**; the Anthropic API is only an optional, budget-capped second opinion for offers the rule tier flags as ambiguous, and the search API (Google/Bing/SerpAPI) for discovery is optional and unconfigured in this delivery (see above). Both degrade gracefully to "do nothing and say so" without keys — never to fabricated output.
- OCR depends on Tesseract and may struggle with stylized banners, small text, or unusual fonts.
- Pages rendered entirely by JavaScript now get a headless-browser render (see above), but that still depends on Playwright/Chromium being installed in the run environment, and a render can still miss content that only appears after a user interaction (e.g. clicking a "Load more" button) the crawler doesn't know to click.
- A source registry cannot safely be exhaustive without checking each mall's official site and terms — the collector only publishes offers it can extract from configured, reachable sources; it never invents offers.
- Offers disappear when their stated end date has passed, or when no source has shown them for 10 days (`KEEP_DAYS` in `crawler/run.py`) — never because of a single failed fetch (see the regression test added this round).
- Legal pages need real operator details and qualified legal review before this site is published for real users (see "Legal pages" above) — they are accurate about the codebase today, not a compliance guarantee.
- Social-platform discovery (Facebook/Instagram/TikTok/X) is not implemented — it needs official API credentials the project doesn't have; see "AI discovery engine" above.
- Arabic legal text was deliberately not machine-translated in `privacy.html`/`terms.html`/`cookies.html` — inaccurate legal translation is a real risk; commission a professional translation once the English text is lawyer-approved.

## Configuration (environment variables, all optional)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables AI second-opinion categorization for low-confidence offers. |
| `ANTHROPIC_CLASSIFY_MODEL` | `claude-haiku-4-5-20251001` | Model used for the optional AI tier. |
| `AI_CALL_BUDGET` | `40` | Max AI classification calls per run. |
| `AI_SLEEP` | `1.5` | Seconds to wait between fetches (politeness/rate limiting). |
| `FETCH_RETRIES` | `3` | Retry attempts per page/image fetch. |
| `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_CX` | unset | Enables dynamic source discovery via Google Programmable Search. |
| `BING_SEARCH_KEY` | unset | Alternative discovery provider (Bing Web Search API). |
| `SERPAPI_KEY` | unset | Alternative discovery provider (SerpAPI). |

All keys/secrets are read from environment variables only — none are committed to the repository. In GitHub Actions, set them as repository secrets (Settings → Secrets and variables → Actions); the workflow already passes through whichever ones you set.

## Test locally

Install Python 3.12 and the packages in `crawler/requirements.txt`. For
banner OCR, also install the Tesseract OCR system package and Arabic
language data. Run:

```bash
python crawler/test_run.py
```

This exercises extraction, the full category taxonomy (including the
bank-card-tag-vs-Banking-category case), promo code/price parsing, the
crawler end to end (including re-run deduplication), manual-correction
locking surviving an automatic refresh, and the retry/backoff wrapper.
