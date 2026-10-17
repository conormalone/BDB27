# Blockers — BDB27

**Status: 0 ACTIVE.** Per `TASK.md`, a blocker stops work and must be written here. **Prior
(combine-fatigue) blockers B1–B7 are archived at git tag `archive/combine-fatigue-v1`** (HEAD `fdc794c`).

As of **2026-10-12 (Cycle 4)** there are **no active blockers**. `04_models` and `05_validate` are now
implemented and tested on the synthetic fixture (D31), completing the game-side pipeline; their real
`--mode full` execution remains **data-blocked** by watch-item 3 below (the full game-tracking files are
absent on this Pi).

As of **2026-10-12 (Cycle 3)** there were **no active blockers**. `00_thresholds` and `03_game_demand`
were implemented and tested on the synthetic fixture (D30); their real `--mode full` execution remained
**data-blocked** by watch-item 3 below (the full game-tracking files are absent on this Pi).

As of **2026-10-12 (Cycle 2)** there were **no active blockers**. The pre-registration blocker that gated the
full run has been **resolved**: `notes/prereg.md` is **FROZEN** (Conor-approved values; `notes/decisions.md`
D28) and the freeze gate (`src/gate.py`) now passes for `--mode full` of the Combine-side stage
(`02_combine_envelope`). Remaining obstacles to a *whole-pipeline* full run are the **game-tracking files
being absent on this Pi** (watch-item 3, `PENDING FULL RUN`) — a data-availability limit, not a design
blocker.

Trigger a blocker on:

- A **missing required Combine/game field**.
- **Matched receivers or route snaps below config minima**.
- **Key Combine features unreliable** (below the ICC threshold).
- **Thresholds provenance not `FULL` in a full run**.
- **`notes/prereg.md` missing / unfrozen before a full run**.
- Any instruction **conflicting with competition rules**.

## Watch-items (documented, not blockers)

1. **Competition caps unverified at source.** Kaggle pages are JS-rendered; caps captured from live-page
   snippets + secondary mirrors (`notes/rules.md` §6). **Action:** re-verify word/figure caps on the
   competition page before the final writeup. Safe posture: ≤2,000 words, ≤5 figures.
2. **Time zone unverified.** `time` is naive; assumed UTC. **Action:** confirm before using absolute
   time-of-day as an exposure.
3. **Full game-tracking files absent.** `game_tracking_{2023,24,25}.csv` are not on the drive; game-side
   audit items (c/d/e/h/i) and Phases 3–5 are `PENDING FULL RUN` (expected, not blocking Phase 0).
4. **`notes/prereg.md` freeze — RESOLVED 2026-10-12.** Was a skeleton (TBD values); now **FROZEN** with
   Conor-approved values (`notes/decisions.md` D28). The runtime freeze gate is `src/gate.py`; the go/no-go
   gate for game-side full runs additionally needs the audit gate (matched WR, route snaps, ICC).
5. **`route_ran` is categorical, not a flag** (audit d, 2026-10-11). Config key `game.route_snap_col`
   still reads as a flag; *route snap* is defined as `route_ran IS NOT NULL`. Handled in code — documented
   so nobody treats it as boolean. Not a blocker.
6. **Thin Combine repeats → low/unestimated ICC** (audit g, 2026-10-11). 40-yd dash has ~2 attempts/player;
   cutting drills mostly 1 (many features ICC≈0 or `UNESTIMABLE`). This is a design constraint for Phase-2
   feature selection (reliable: `peak_speed`, `speed_at_10/20yd`, `env_vmax`), not a blocker. **Frozen
   threshold** `envelope.icc_min=0.5` (prereg §4); residue: the two retained raw-speed features remain
   collinear (VIF>5) — flagged, not a blocker (`notes/limitations.md`).

---

## Phase 0.5 — External pre-Combine expectation proxy · 2026-10-16

**Status: DATA BLOCKER.** The Phase 0.5 spec requires the external **Grinding The Mocks (GTM)**
expected-draft-position export. It is **not machine-obtainable** from this host via any bounded,
policy-compliant route (full route table + access dates in `notes/external_data_manifest.md`):

- Direct `grindingthemocks.com` — DNS resolves (216.92.3.120); HTTPS (:443) TCP connect **times out**;
  HTTP (:80) returns **301** → `https://grindingthemocks.shinyapps.io/Dashboard`.
- Redirect target `grindingthemocks.shinyapps.io/Dashboard/` is **reachable** but is an interactive **Shiny
  websocket** app (no static export; `robots.txt` = `User-agent: * / Disallow: /` → not crawled). A
  third-party renderer (`r.jina.ai`) returns the landing shell only — the data table loads lazily.
- **No GitHub mirror** (repo search returns 0 relevant; code search needs auth — none available, none
  invented). **Wayback** holds only the ~2020–2024 app shell; the CDX endpoint 429s/times out. Static
  export probes (`/Dashboard/data.csv`, `www/data.csv`, …) → **404**.

**Action taken:** per the spec we **do not fabricate data** and **do not substitute a source** (the
non-GTM fallback covers only 2025/26 and is out of spec; CFBD is out of scope). The Phase 0.5 pipeline
(`src/00b_external_proxies.py`) is **built and fully exercised** end-to-end against a schema-compliant
synthetic GTM fixture (`src/synthetic_gtm_fixture.py`, `fixtures/synthetic/gtm/`); its real `--mode full`
execution is **BLOCKED (data)** until a raw GTM export is supplied under `data/raw/external/gtm/`.

**Resume condition:** drop the raw GTM mock-history export (canonical `gtm_mock_history.csv`) into
`data/raw/external/gtm/`, then run `python src/00b_external_proxies.py --mode full`.

---

## Security EDP board — AVAILABLE (not a blocker) · 2026-10-17

The **Phase-4 security axis** does **not** use the blocked GTM fuzzy proxy. It uses a separately supplied
**EDP board** export (`data/raw/external/gtm/GTM_BDB_EDP_Snapshots_20261016.csv`, sha256 `4664b369…63b4`),
which is **present and permitted** (Conor 2026-10-17; `notes/rules.md` §6). The Phase-4 stage
(`src/06_security.py`) and the Phase-1 board audit extension therefore run for real on this Pi; their
`--mode full` execution is **not** data-blocked. **One open caveat** (documented, not a blocker): the
board→Combine exact `nfl_id` WR/TE match rate is ≈0.757, below `external.board.min_match_rate` (0.85), because
the board spans a broader prospect pool than the 2027 subset — logged in `notes/decisions.md` D37 for the
cycle-2 validator; the implemented go/no-go minima are evaluated separately.
