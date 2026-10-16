# External Data Manifest — BDB27 Phase 0.5 (Grinding The Mocks)

Compliance record for the Kaggle **External Data** forum post: exact URLs/endpoints, access dates and
outcomes of every route attempted to obtain the external pre-Combine expectation proxy.

**Source in scope:** Grinding The Mocks (GTM) — the SOLE external source for Phase 0.5 (CFBD references
were cut by Conor and are out of scope). **Status: UNAVAILABLE** (see `notes/blockers.md`); the pipeline
is built and exercised against a schema-compliant synthetic fixture pending data access.

## Bounded acquisition attempt (manual, 2026-10-16)

| Route | Target | Result |
|---|---|---|
| Direct (DNS) | `grindingthemocks.com` → 216.92.3.120 | DNS resolves |
| Direct (HTTP :80) | `http://grindingthemocks.com/` | **301** → `https://grindingthemocks.shinyapps.io/Dashboard` |
| Direct (HTTPS :443) | `https://grindingthemocks.com/`, `/api`, `www.` | **TCP connect timeout** (unreachable) |
| Redirect target (found new) | `grindingthemocks.shinyapps.io/Dashboard/` | **202/302, reachable** — but it is a Shiny **websocket** app (interactive, no static export) |
| Third-party renderer | `r.jina.ai/https://grindingthemocks.shinyapps.io/Dashboard/` | renders the landing shell only; the data table is loaded lazily over the Shiny websocket — **no data in response** |
| `robots.txt` (redirect target) | `grindingthemocks.shinyapps.io/robots.txt` | `User-agent: * / Disallow: /` → **not crawled** (site policy) |
| GitHub repo search (no token) | `grinding+themocks`, `grindingthemocks`, `grinding-the-mocks`, `mock+draft+expected+draft+position` | 0 relevant repos (1 unrelated) |
| GitHub code search | `api.github.com/search/code?q=grindingthemocks` | **401** — requires authentication (none available; none invented) |
| Wayback availability | `archive.org/wayback/available?url=grindingthemocks.com` | closest **2024-05-28** snapshot; captured page is the same app shell (no data) |
| Wayback CDX (csv/json) | `web.archive.org/cdx/...filter=original:.*(csv|json)` | empty; CDX endpoint times out/429 (rate-limited) |
| Static export probe | `.../Dashboard/{data.csv,www/data.csv}`, `.../data.csv`, `.../mockdata.csv` | **404** (no static download) |

**Conclusion:** no bounded, policy-compliant machine route yields a raw GTM export. Per the spec we do
**not** substitute another source and do **not** fabricate data.

## Machine acquisition log (this run)

<!-- AUTO-MANIFEST:START -->
| source | url | access_date | result |
|---|---|---|---|
| fixture | /home/conormalone/.openclaw/workspace/BDB27/fixtures/synthetic/gtm/gtm_mock_history.csv | 2026-10-16 | SYNTHETIC FIXTURE (not a finding) |
<!-- AUTO-MANIFEST:END -->
