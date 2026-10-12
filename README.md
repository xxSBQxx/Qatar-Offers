# Qatar Offers

Qatar Offers is a bilingual English/Arabic offer-discovery website, a local extraction and verification pipeline, and an optional self-hosted FastAPI + SQLite backend. It does not call Gemini, OpenAI, or a hosted AI inference API. The extractor is currently deterministic Python rules plus optional local Tesseract OCR; it is **not** represented as a trained large language model.

## Architecture

- `web/`: responsive public website and admin interface. The public site reads the API when hosted with the backend and falls back to the generated, verified-only JSON files on GitHub Pages.
- `crawler/ai.py`: deterministic local offer extraction from HTML text and images.
- `crawler/run.py`: respectful collection, robots.txt checks, throttling, retries, source revalidation, RSS/Atom discovery, deduplication, expiry, and verified-only publication.
- `sources.json`: configurable official mall pages, sitemaps, RSS feeds, and search-feed templates. RSS/search results are **discovery candidates**, not automatically trusted sources.
- `data/state.json`: portable collector state and review queue. It is committed by the GitHub Actions workflow to retain history between temporary runners.
- `api.py`: optional server backend, SQLite database, authentication, user favorites, admin review, source/mall/category/user management, audit trail, and scheduled collection.
- `.github/workflows/refresh.yml`: every-six-hours collection and GitHub Pages deployment. It retries safely from the latest `main` commit and never force-pushes.

## Important verification rules

1. Only offers found in a currently reachable configured source page with concrete offer evidence can be auto-verified. Search/RSS discoveries are queued as pending and never published automatically.
2. The public JSON/API contains active, verified offers only. Listings must have been rechecked within the last seven days; expired offers are excluded. If the source changes and an offer disappears, the offer is deactivated and returned to review unless an administrator manually approved it.
3. Missing end dates remain missing. The collector never invents expiry dates. Offers without a published expiry date may be displayed if source evidence verifies the offer, but the original merchant page remains the final authority.
4. AI confidence is a diagnostic hint only and is not proof of validity.
5. The collector checks robots.txt, uses per-host delays, bounded retries, response-size limits, and only uses explicitly configured public sources/feed endpoints. It does not bypass logins, CAPTCHAs, paywalls, or access controls.
6. No scraper can guarantee every offer from every mall. Website layout changes, blocked pages, dynamic JavaScript, social-only promotions, and source terms can limit coverage. Add and validate official sources incrementally.

## GitHub Pages setup (free within GitHub plan limits)

1. Push the repository contents to your GitHub repository on branch `main`.
2. In **Settings → Pages**, select **GitHub Actions** as the build/deployment source.
3. In **Actions**, run **Discover, verify, and publish Qatar offers** once manually.
4. The workflow runs every six hours. It installs Tesseract English/Arabic language support and the Python dependencies, collects configured sources, commits only generated data files, and deploys `web/`.
5. No AI API keys are required. The GitHub Actions workflow uses public RSS/search feeds configured in `sources.json`; those feeds are not AI services.

GitHub Pages is static hosting. It cannot run a permanent Python API, secure login sessions, or the admin database. The public site works with static JSON on Pages, but secure admin tools and database-backed user features require the backend below.

## Local development / self-hosted server

Use Python 3.11+ (Python 3.12 recommended). From the project root:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate
python -m pip install -r requirements.txt
```

For local OCR, install Tesseract and the English/Arabic language data on the machine. OCR is optional; text-page extraction still works without it. Check installation with `tesseract --list-langs` and look for `eng` and `ara`.

Set environment variables **outside source control** before starting the API. Example (Linux/macOS):

```bash
export ADMIN_USERNAME='offers-admin'
export ADMIN_PASSWORD='replace-with-a-unique-password-at-least-12-characters'
export SESSION_SECRET='replace-with-a-long-random-secret'
export SESSION_SECURE='1'
export DATABASE_PATH='./data/qatar_offers.sqlite3'
export DISCOVERY_INTERVAL_HOURS='6'
uvicorn api:app --host 127.0.0.1 --port 8000
```

PowerShell equivalent:

```powershell
$env:ADMIN_USERNAME = 'offers-admin'
$env:ADMIN_PASSWORD = 'replace-with-a-unique-password-at-least-12-characters'
$env:SESSION_SECRET = 'replace-with-a-long-random-secret'
$env:SESSION_SECURE = '0' # use 1 behind HTTPS in production
$env:DATABASE_PATH = '.\data\qatar_offers.sqlite3'
$env:DISCOVERY_INTERVAL_HOURS = '6'
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` for the website and `http://127.0.0.1:8000/admin` for the admin dashboard. The admin account is created on first database initialization only if `ADMIN_USERNAME` and an `ADMIN_PASSWORD` of at least 12 characters are set. Do not expose the service to the public internet until you set a strong `SESSION_SECRET`, use HTTPS (`SESSION_SECURE=1`), configure a firewall/reverse proxy, and secure server access. Back up the SQLite database and `data/state.json`.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_USERNAME` | unset | Initial administrator username |
| `ADMIN_PASSWORD` | unset | Initial admin password; at least 12 characters |
| `SESSION_SECRET` | development fallback | HMAC secret setting for production session configuration; set a long random value |
| `SESSION_SECURE` | `0` | Set to `1` for HTTPS-only session cookies |
| `SESSION_HOURS` | `12` | Session lifetime in hours |
| `DATABASE_PATH` | `data/qatar_offers.sqlite3` | Persistent SQLite database path |
| `DISCOVERY_INTERVAL_HOURS` | `6` | Background collection interval while the API process is running |
| `CRAWLER_SLEEP` | `2` | Minimum per-host delay in seconds |
| `KEEP_DAYS` | `10` | Revalidation stale threshold in days |
| `MAX_NEW_IMAGES` | `25` | Maximum newly processed images per run |
| `MAX_PAGE_BYTES` | `5000000` | Maximum downloaded page response size |
| `MAX_DISCOVERY_ITEMS` | `100` | Maximum feed discovery candidates followed per run |
| `COLLECTOR_TIMEOUT` | `900` | API-triggered collector timeout in seconds |
| `API_RATE_LIMIT` | `120` | API requests per client per 60-second window |

## Configure sources

Edit `sources.json` and add official source pages to `malls[].pages`, with a unique mall `id`, human-readable names, and `type` set to `text`, `images`, or `api` for a permitted public JSON API. You may add RSS/Atom URLs to `feeds`. `search_feed_templates` can use `{query}` and `discovery_queries` to populate public RSS-based discovery. Do not add sources that prohibit this use. Search/RSS candidates are intentionally pending until verified against source evidence. Keep requests within the source's terms and rate limits.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python crawler/test_run.py
python tests_api.py
python -m py_compile api.py crawler/ai.py crawler/run.py
```

The tests are offline and use a fake source page; they do not certify that every real mall source is currently accessible. To manually run collection locally, use `python crawler/run.py`. To run the backend, use `uvicorn api:app --host 127.0.0.1 --port 8000`.

## Deployment and known limitations

- The GitHub workflow publishes a static website and generated verified-only JSON. Secure admin, sessions, database-backed favorites, and persistent job controls are available only when `api.py` runs on a server.
- The collector is a transparent local rules engine with local OCR, not a trained AI model. A genuinely learned model would need separately selected local model weights, evaluation, and compute.
- Source coverage is limited to configured sources and discovered RSS articles. Dynamic JavaScript pages may require a permitted site-specific integration. Do not circumvent access controls.
- Merchants may not provide logo assets, price comparisons, redemption steps, or expiry dates. Those fields remain empty when the source does not publish them; the UI uses a merchant initial instead of fabricating a logo.
- SQLite is appropriate for an initial single-server deployment. Use a managed database and a dedicated worker/queue if traffic or ingestion volume outgrows a single server.
- This project does not include paid services or AI API credentials. Public search feeds and merchant websites are external content sources, not hosted AI inference services.
