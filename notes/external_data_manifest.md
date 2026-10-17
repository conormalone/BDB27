# External Data Manifest — BDB27 Phase 0.5 (Grinding The Mocks)

Compliance record for the Kaggle **External Data** forum post: exact URLs/endpoints, access dates and
outcomes of every route attempted to obtain the external pre-Combine expectation proxy.

**Source in scope:** Grinding The Mocks (GTM) — the SOLE external source for Phase 0.5 (CFBD references
were cut by Conor and are out of scope). **Status: UNAVAILABLE** (see `notes/blockers.md`); the pipeline
is built and exercised against a schema-compliant synthetic fixture pending data access. The **security
EDP board** (Phase 4) is a *separate*, supplied static export and is **PERMITTED** — see
“Security/stakes EDP board” below.

## Security/stakes EDP board (Phase 4) — permission + provenance  ·  2026-10-17

The stakes task consumes a **pre-Combine expected-draft-position (EDP) board** — an EDP-snapshots export
supplied to Conor on **2026-10-16** and placed under `data/raw/external/gtm/`.

| Item | Value |
|---|---|
| File | `data/raw/external/gtm/GTM_BDB_EDP_Snapshots_20261016.csv` (a `.xlsx` twin exists; only the CSV is read) |
| sha256 | `4664b369e56dfcbe62de678932059695c2c5b8b128989fa5a9068994857563b4` |
| Rows / cols | 3,625 data rows · 13 cols (schema + order pinned in `config.yaml`; asserted on read) |
| Accessed | 2026-10-16 (file mtime) · verified 2026-10-17 (sha256 + row count) |
| **Permission** | **PERMITTED** — the Competition Rules’ External Data section permits external data with citation/obligations; **Conor confirmed 2026-10-17**. Recorded in `config.yaml` (`external.board.permitted: true`) and `notes/rules.md` §6. |
| Redistribution | **NOT committed** (`data/` gitignored) — do not ship the board file. |

The board supersedes the earlier hypothetical GTM **fuzzy-name** mapping for the security axis: new security
code joins on **`nfl_id` directly** (`notes/decisions.md` D37). Unlike the Phase-0.5 GTM proxy (whose live
route is a blocked Shiny app — see below), the board is a **supplied static export**, so it is present and
usable. The Phase-0.5 GTM fuzzy pipeline remains blocked and is not the board’s acquisition route.

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
| board | /home/conormalone/.openclaw/workspace/BDB27/data/raw/external/gtm/GTM_BDB_EDP_Snapshots_20261016.csv | 2026-10-17 | PERMITTED (Conor 2026-10-17); sha256 4664b369…63b4; 3625 rows |
| fixture | /home/conormalone/.openclaw/workspace/BDB27/fixtures/synthetic/gtm/gtm_mock_history.csv | 2026-10-16 | SYNTHETIC FIXTURE (not a finding) |
<!-- AUTO-MANIFEST:END -->
