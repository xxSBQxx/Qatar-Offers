# Qatar Offers — Implementation and Test Report

**Build date:** 12 October 2026  
**Timezone:** Asia/Qatar

## Automated checks completed

- Python compilation: passed for `api.py`, `crawler/ai.py`, `crawler/run.py`, `tests_api.py`, and `browser_tests.py`.
- API/auth/database smoke suite (`python tests_api.py`): passed, including authentication and roles, verified-only listing, expiry/staleness filtering, manual review preservation, source management, schedule settings, source discovery approval/dismissal, report intake/status workflow, account consent, account deletion, and policy routes.
- Collector suite (`python crawler/test_run.py`): passed for local extraction, deduplication, verification state, expiry, and preservation of manual corrections.
- Playwright browser suite (`python -m pytest -q browser_tests.py`): **2 passed** using Chromium and in-memory API fixtures. It checks responsive layout, mall directory, search, offer details/source links, cookie choices, policy pages, report form, and admin discovery/report sections. The offer used in browser tests is a test-only fixture and is not written to production data.
- HTML/inline JavaScript audit: passed; no duplicate HTML IDs or JavaScript syntax errors found.
- GitHub Actions YAML parsing: passed for the collection/deployment workflow and CI workflow.

## Live-source validation limitation

A live collector run was attempted in an isolated copy, but source requests did not complete within the environment's timeout. The collector observed that Google News RSS endpoints were disallowed by robots checks; those feeds/templates were removed instead of bypassing the restriction. No real offer was fabricated or added as a result of this attempt. `web/offers.json` is intentionally empty until a scheduled/manual run successfully collects and verifies real source data.

The local Playwright CLI screenshot command could not complete because the execution environment's Chromium policy returned `ERR_BLOCKED_BY_ADMINISTRATOR` for direct browser navigation. A Playwright CLI screenshot smoke test is included in `.github/workflows/ci.yml` for execution on a normal GitHub-hosted runner. The Python Playwright browser suite did run and pass locally.

## Design skill availability

Taste-Skill was not exposed as an available tool/plugin in this execution environment, so it was not invoked. The existing design was directly updated to a minimal Qatar-branded visual system with responsive mall/merchant directories, accessible controls, visible focus states, Arabic/RTL support, and reduced-motion handling. This report does not claim Taste-Skill was used.

## Credentials and launch requirements

- Optional broad Google Search/public-social discovery requires `GOOGLE_CSE_API_KEY` and `GOOGLE_CSE_ID` from an official Google Programmable Search Engine. Quota, billing, and terms depend on Google's current plan. Without these credentials, the collector uses configured official mall pages and any permitted RSS/API feeds explicitly added by the operator.
- Configure a persistent random `SESSION_SECRET`, `SESSION_SECURE=1` behind HTTPS, admin credentials, firewall/reverse proxy, and database backups before public server deployment.
- GitHub Pages hosts only the static website/data. Authentication, admin workflows, persistent database features, report submission, and scheduling require the FastAPI backend.
- The seeded directory includes 13 mall entries; not every mall currently has a validated direct source page. Source coverage is not exhaustive.
- Privacy, terms, cookies, refund, and rights-request pages are cautious templates, not legal advice or a compliance certification. Publish the actual operator identity and monitored contact channel and obtain qualified Qatar legal review before public launch.
