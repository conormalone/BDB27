# Phase 0.5 — Construct Pre-Combine Expectation Proxies (GTM)

> Persisted from Conor's task brief (2026-10-16). This file records the Phase 0.5 **spec** and its
> implementation. It does **not** amend the binding `TASK.md` (ceiling-binding task); Phase 0.5 is an
> additive external-data phase that feeds Phase 4 (Models).

## Spec (verbatim)

**Context:** NFL Big Data Bowl 2027 (Kaggle). External Data Integration.

### Goal
Construct a pre-Combine expectation proxy dataset mapped to Kaggle's `players.csv`. This will test the
exploratory hypothesis that lower-tier prospects over-exert at the Combine relative to blue-chip
prospects. Output a single memory-optimized `outputs/pre_combine_expectation.parquet` table mapping Kaggle
`nflId` to Pre-Combine Expected Draft Position (EDP).

### Data Sources (External)
1. **Grinding The Mocks (GTM):** Public GitHub repository or API exports.

### Engineering Rules
- **Memory Safety:** Execute entirely in lazy Polars or DuckDB. Do not load full JSON/CSVs into Pandas.
- **API Pacing:** enforce strict rate limiting (e.g. 0.5s sleep between calls). Cache raw API responses
  locally in `data/raw/external/` before processing.
- **Run Modes:** Script `00b_external_proxies.py` must support `--mode sample|full` and `--force`. In sample
  mode, pull only a subset of years/players to validate the pipeline.
- **Compliance:** Log the exact URLs, access dates, and API endpoints in `notes/external_data_manifest.md`
  to format for the official Kaggle External Data forum post.

### Execution Steps
**1. GTM Expected Draft Position (EDP):** Ingest historical GTM mock draft data for the draft classes
represented in the Kaggle dataset. Filter strictly for **February snapshots** (prior to the late-Feb/early-
March Combine dates) to ensure the EDP is uncontaminated by Combine performance. Extract the median/mean
EDP per player. Column: `Pre_Combine_EDP`.

**2. ID Resolution & Fuzzy Matching:** Kaggle `nflId` does not natively exist in GTM. Implement a robust
matching function (string similarity on Name + Draft Year + Position + College). Write unmatched threshold
cases to `outputs/unmatched_external_ids.csv`. Assert a match rate > 85% for drafted WRs/TEs. If it falls
below this threshold, halt execution and write to `notes/blockers.md`.

**4. Checkpoint & Deliverable:** Join external proxy to the Kaggle `nflId`. Write to
`outputs/pre_combine_expectation.parquet` containing strictly: `nflId`, `Pre_Combine_EDP`,
`match_confidence_score`. Ensure this script gates the execution of Phase 4 (Models), making these features
available for the utilization (u_i) interaction term.

> The source list / step numbers have gaps because **CFBD references were cut by Conor — CFBD is NOT in
> scope. GTM is the sole external source.**

## Implementation (`src/00b_external_proxies.py`)

- **Config-driven** — everything lives under the `external:` section of `config.yaml` (paths, endpoints,
  February month, aggregation, fuzzy weights/thresholds, `min_match_rate`, in-scope years/positions, strict
  deliverable `schema`). No magic numbers in code.
- **Memory-safe** — ingest/aggregate run in DuckDB over the raw CSV(s); no full CSV/JSON into pandas.
- **Run modes** — `--mode sample|full`, `--force`, plus `--synthetic` (fixture path), with checkpoint/skip
  (`_DONE.json`), wall-time + peak-RAM logging to `outputs/run_log.csv`, and strict `request_sleep_s` API
  pacing; raw responses are cached to `data/raw/external/` before processing.
- **Acquisition** — `--mode full` prefers a cached raw export under `data/raw/external/gtm/`; with none it
  makes **bounded** live attempts (config endpoints + retries), then, if still empty, writes a
  data-availability **blocker** and stops (never fabricates data, never substitutes another source).
  `--mode sample` prefers a cached export (subset of years) and otherwise falls back to the committed
  fixture, labelled `UNVALIDATED SAMPLE OUTPUT`. `--synthetic` runs the identical path on the fixture,
  labelled `SYNTHETIC FIXTURE (not a finding)`.
- **February filter → EDP** — keep `month(snapshot_date) == external.snapshot.month` (February) for the
  in-scope years, then aggregate `median` (config; `mean` also supported) projected pick per prospect.
- **ID resolution** — one-to-one greedy fuzzy match on **Name + Draft Year (hard filter) + Position +
  College**, using stdlib `difflib` (no new dependency). Confidence = the weighted combined score; accept
  at/above `external.fuzzy.min_score` with a `min_name_score` guard. Sub-threshold cases →
  `outputs/unmatched_external_ids.csv` (provenance first column).
- **Match-rate gate** — asserts the drafted WR/TE match rate **> `external.min_match_rate` (0.85)**; below
  the threshold it halts and appends a blocker.
- **Deliverable** — writes `outputs/pre_combine_expectation.parquet` with **exactly** `nflId`,
  `Pre_Combine_EDP`, `match_confidence_score` (dtypes `int64, float64, float64`); the schema is asserted on
  write. A sidecar `<file>.parquet.provenance.txt` carries the run label (the strict schema forbids a
  provenance column).
- **Phase-4 gate** — `src/gate.py::external_proxy_gate_ok` requires the proxy to **exist** with **exactly**
  the configured schema when `external.require_for_phase4` is true; `04_models.py --mode full` is wired to
  require it. `--mode sample` is never gated.

## Data availability

The live GTM export is currently **not machine-obtainable** (its front end is a Shiny websocket app;
`robots.txt` disallows crawling; no static export / GitHub mirror / usable capture). See
`notes/external_data_manifest.md` (routes tried) and `notes/blockers.md` (the active data blocker). The
pipeline is therefore built and exercised end to end against the schema-compliant synthetic fixture
(`src/synthetic_gtm_fixture.py` → `fixtures/synthetic/gtm/`).
