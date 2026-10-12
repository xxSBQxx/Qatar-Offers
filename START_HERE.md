# Qatar Offers — Start here

This repository now contains the upgraded static website, local collector, verification pipeline, optional FastAPI backend, SQLite database setup, and secure admin dashboard.

## Fastest way to use the existing GitHub project

1. Upload/commit the full repository contents to your existing `main` branch. Do not upload only selected files; the workflow and backend need the full folder structure.
2. In GitHub, open **Settings → Pages** and set the publishing source to **GitHub Actions**.
3. Open **Actions → Discover, verify, and publish Qatar offers → Run workflow**.
4. The workflow checks the configured sources, creates only verified/current public offers, commits generated JSON/state safely, and deploys the `web` folder.
5. The public site can run on GitHub Pages. The admin dashboard and persistent database require the backend setup below; GitHub Pages itself cannot run Python APIs or secure database-backed admin actions.

## Run locally / on your own server

Install Python 3.11+ (3.12 recommended), then run from the repository root:

```bash
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
```

Set these environment variables outside source control: `ADMIN_USERNAME`, `ADMIN_PASSWORD` (at least 12 characters), `SESSION_SECRET` (long random secret), `DATABASE_PATH` (persistent file path), and `SESSION_SECURE=1` when using HTTPS. Then start the API:

```bash
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` for the website and `http://127.0.0.1:8000/admin` for the admin dashboard. The first startup creates the admin account if the two admin environment variables are set. Do not expose the server publicly without HTTPS, a strong session secret, firewall/reverse-proxy controls, and database backups.

## Collector and OCR

Run `python crawler/run.py` to collect offers manually. The collector uses rules and optional local Tesseract OCR, not a hosted AI API. For Arabic/English banner OCR, install Tesseract and its `eng`/`ara` language packs. Search/RSS items remain pending until verified against source evidence. The crawler respects robots.txt, rate limits, and public-source restrictions.

Configure official mall/merchant pages, permitted RSS feeds, public JSON APIs, and sitemaps in `sources.json`, or manage them in the server admin dashboard. The server backend uses database-configured sources for its own scheduled jobs. GitHub Actions has its own schedule in `.github/workflows/refresh.yml`.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python crawler/test_run.py
python tests_api.py
python -m py_compile api.py crawler/ai.py crawler/run.py
```

See `README.md` for architecture, all environment variables, verification rules, and deployment limitations. No paid AI API keys are needed. Current source coverage is not exhaustive; add permitted official sources incrementally.
