# Blockers — BDB27

**Status: 0 ACTIVE — B5 RESOLVED (D19, 2026-10-10).** Phase 1 cleared the gate (GO); Phase-2 code/tests are complete. The **data-semantics discrepancy** between the old locked `attempt_level: drill_name` design and the 2025 class was **resolved by D19** (empirical per-`(nfl_id, drill_type)` numbering-unit detection, human-approved 2026-10-10). Recorded 2026-10-09; resolved 2026-10-10.

Per `TASK.md`, a blocker stops work and must be written here. The following were checked and are **not** blocking:

- **Timing/order field present?** Yes — `time` (TIMESTAMP) + `attempt`, agreement 0.9992.
- **A position group qualifies?** Yes — **DB** under the all-drills family (D13; DB 122/122 reach ≥3 observed attempts).
- **Reliability below threshold?** Not yet measured (Phase 3; pre-registered stop <0.2).
- **Thresholds provenance not "FULL" in a full run?** N/A this cycle (Phase 0/1 only).

## Watch-items (documented, not blockers)

1. **Competition caps unverified at source.** Kaggle pages are JS-rendered; caps captured from live-page
   snippets + secondary mirrors (`notes/rules.md` §6). **Action:** re-verify word/figure caps on the
   competition page before writing the final writeup. Safe posture adopted (≤2,000 words, ≤5 figures).
2. **Time zone unverified.** `time` is naive; assumed UTC (`decisions.md` D11). **Action:** confirm before
   using time-of-day as an exposure.
3. **Full game-tracking files absent.** `game_tracking_2023/24/25.csv` not on the drive; game-side audit
   (e) and Phase 4/5 need them. Marked PENDING FULL RUN — expected, not blocking Phase 0/1.
4. ~~**Interpretation of "family of repeated maximal-effort drills".**~~ **RESOLVED (D12):** human decision
   (2026-10-09) sets the family = **ALL drills**; recorded in `decisions.md` D12. Supersedes D3's drill pick.
5. **Position scope LOCKED to DB only (D13).** DB is the locked study population (human decision, Conor 2026-10-09; `audit.study_population: DB`). WR is excluded (in-game intensity is scheme-dependent → between-system noise). The single-position design **satisfies TASK.md Phase 1 ("select ONE position group")** — not a deviation. Table: `outputs/audit/h_family_scope.csv`.
6. **Matched Combine→NFL count for DB = PENDING FULL RUN.** Needs
   `game_tracking_{2023,24,25}.csv` (absent). Not blocking Phase 0/1; needed before the Phase 5 link
   (if the DB matched-N is too small, that is a blocker). See `pending_full_run.md` P1/P8.
7. **Draft position control: structural "undrafted", not missing (A12).** `players.draft_overall_pick` and `draft_round` are null for the **same 127/510** prospects (the undrafted); the >20%-null exclusion rule applies to **non-structural** missingness only, so it does **not** fire → draft position is **KEPT** as a Phase-5 control, with "undrafted" encoded as its own level (recorded `assumptions.md`, `limitations.md`, `decisions.md`). Not a blocker; the spec makes draft position a control where available.

---

# Cycle 3 — RESOLVED blocker (B5, resolved 2026-10-10 by D19)

## B5 — `attempt` numbering unit differs by draft class (2023/24 vs 2025): the locked `attempt_level: drill_name` premise is violated for 2025 · 2026-10-09 (RESOLVED 2026-10-10, D19)
**Status:** **RESOLVED (D19, 2026-10-10)** — the numbering unit is now detected empirically per `(nfl_id, drill_type)` before gap-imputation / `first_attempt`; see the Resolution paragraph at the end of this section.

**What.** The locked Phase-2 design (`decisions.md` D16/D18) and `notes/schema.md` §2 assume `attempt` "restarts per `(player, drill_name)`", and the numbering-gap imputation (D18) is applied at that level. **Evidence contradicts this for the 2025 draft class.**

- **2023 / 2024** — `attempt` restarts per `(nfl_id, drill_name)`. Gap-groups (max > distinct): **121 / 81**; max attempt **6**. (e.g. `GAUNTLET_DRILL` attempts 1,2 for one player.)
- **2025** — `attempt` is a **per-`(nfl_id, drill_type)` block counter**. Example: nfl_id **58968** ran `SKILL_DRILLS_WR` once through with attempts **1..17** spread across 17 **different** `drill_name`s (`OVER_SHOULDER_ADJUST`=1, `GAUNTLET_DRILL`=2,3, `SLANT_ROUTE_LEFT`=4, … `RED_ZONE_FADE_RIGHT`=17). Within any single `drill_name` only one number is observed, yet `max(attempt)=17`. The same player has **zero** genuinely lost reps.

**Impact (measured on the combine Parquet, complete data).**

| Level | 2023 | 2024 | 2025 | Total | DB-only total |
|---|---:|---:|---:|---:|---:|
| spec `drill_name` (implemented) | 144 | 118 | **7874** | **8136** | **1617** (49+32+**1536**) |
| year-aware (`drill_type` for 2025) | 96 | 94 | 148 | ≈338 | ≈130 |

The spec rule makes **~92% of the DB study-population imputed rows phantom**, which directly inflates the primary exposure `prior_load_yd` (and `prior_load_efforts`) for the 37/122 DB players from the 2025 class. `first_attempt` (= `attempt==1`) is likewise degenerate for 2025 skill drills (only the block's first rep is flagged).

**Why it is a blocker (not silently fixed).** The design is marked LOCKED and "do not re-litigate"; changing the level is a methodology call, not a coding call.

**Recommendation (for the PM).** Make the numbering unit **year-aware**: `attempt_level` = `drill_name` for 2023/2024, `drill_type` for 2025 (equivalently, derive the unit per player by detecting where `attempt` restarts at 1 and is contiguous). Re-derive `first_attempt`, numbering-gap imputation, and `prior_load_yd`/`prior_load_efforts` accordingly, then re-run Phase 2 and Phase 3.

**Current handling.** `src/02_features.py` implements the locked rule exactly and emits a runtime **WARNING** + diagnostics (`imputed_rows_{2023,2024,2025}`, `imputed_share_of_observed`, `attempt_numbering_restart_warning`) whenever imputed rows exceed `features.impute_warn_share` (0.5) of observed rows. Sample run: warning **tripped** (8136/6310 = 1.29). Choosing to read the sample DB rows would be wrong (sample discipline, D8) — the blocker is raised on the **full-combine** evidence (combine data is complete, not sampled).

**Resolution (2026-10-10, D19).** The `attempt` numbering unit is now **detected empirically per `(nfl_id, drill_type)` block** (`features.attempt_level: empirical`, rule: `drill_name` restart unit iff ≥2 distinct `drill_name`s and ≥ `features.attempt_restart_min_drill_names` (2) of them start at `attempt==1`), replacing the hardcoded `drill_name` premise. Detection reproduces the classes cleanly: 2023 160/160 and 2024 177/177 multi-`drill_name` blocks → `drill_name`; 2025 0/173 → `drill_name` (173 → `drill_type`). Effect: **2025 imputed rows 7,874 → 148** (DB study population **1,536 → 28**); 2023 (144) and 2024 (118) unchanged; total imputed 410 of 6,310 observed (share 0.065, warning now **OFF**). `first_attempt` is redefined as the player's earliest *observed* rep of that `drill_name` (imputed → 0); a new `attempt_unit` provenance column is emitted per row; ordering uses `unit_first_start_rank`. `test_2025_numbering_fix` + the updated `test_invariants`/`test_attempt_unit_detection` are green, and the Phase-1 audit tests still pass. Full record: `decisions.md` **D19**.

---

# Cycle 8b — Phase 3d warm-up phenotype: ambiguities surfaced by the Coder (NOT hard blockers)

## B6 — D23 §1's "drill+session_rank-ADJUSTED" coefficient is under-specified; the `deconf_pass` verdict flips on it · 2026-10-11
**What.** D23 §1's PASS/FAIL rule needs a single "drill+order-adjusted population coefficient",
but D23 does not pin down (a) whether the adjusted model keeps the Phase-3 `load²` term, nor
(b) whether the rule must hold for **all** accel-outcome cells or only the declared **primary**
cell (`prior_load_efforts × peak_accel_z`), nor (c) whether the EB analogue ("within-drill
centring") replaces the FE estimate when the fallback chain selects EB. These choices change
the verdict.

**Evidence (real DB data, `outputs/model/warmup_deconfounding.csv`; raw = fallback-chain
population coefficient; adj = pooled OLS `outcome ~ load_c [+ load2_c] + first_attempt +
C(drill_type) + session_rank`; EB-within = within-drill-centred load → EB meta-slope).**

| cell | raw | adj (w/ load²) | retain | adj (no load²) | retain | EB-within | retain | within rep1→rep2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **L5×O2 (primary)** | 0.1056 | **+0.0845** | **0.80** | +0.0340 | **0.32** | +0.0771 | 0.73 | +0.068 |
| L5×O4 | 0.0979 | +0.0710 | 0.72 | +0.0216 | 0.22 | +0.1373 | 1.40 | +0.050 |
| L4×O2 | 0.0155 | **−0.0132** | n/a (neg) | −0.0178 | n/a (neg) | +0.0105 | 0.68 | +0.068 |
| L4×O4 | 0.0263 | +0.0032 | 0.12 | −0.0067 | n/a (neg) | +0.0191 | 0.73 | +0.050 |
| L1×O2 | 0.00129 | +0.00019 | 0.15 | −0.00093 | n/a (neg) | +0.0018 | 1.42 | +0.068 |
| L1×O4 | 0.00244 | +0.00146 | 0.60 | +0.00054 | 0.22 | +0.0032 | 1.31 | +0.050 |

**Verdict sensitivity.** Primary cell: `deconf_pass = True` (with `load²`), `False` (without
`load²`), `True` (EB-within analogue). Across **all** accel cells (strict reading): `False`
(the `prior_load_accel` accel cells have a **negative** adjusted coefficient). The
`prior_load_accel` cell — one of D21's three gate cells — does **not** survive the
drill+session adjustment. The within-drill rep-1→rep-2 battery contrast is **positive on every
accel outcome** (≈+0.05…+0.07 z), which supports the warm-up direction for the within-drill (W)
identification (D23's declared *primary* identification).

**Coder's resolution (documented, not silent).** The literal reading — "the population model
with an explicit `session_rank` term AND `C(drill_type)` fixed effects" — keeps the Phase-3
`load²` term → **primary-cell `deconf_pass = True`**. All variants are emitted in
`warmup_summary.json` (`headline.deconf_pass_no_load2`, `.deconf_pass_eb_within`,
`.deconf_pass_all_accel`) and in `SUMMARY_3d.md`. **PM action requested:** confirm which reading
governs the headline; if the strict/all-accel or no-load² reading governs, `deconf_pass = False`
and the honest fallback (metric-robust fatigue null) is the finding.

**Resolution (2026-10-11, D24).** The **strict** all-accel-cells reading **governs** (D24): the rule must hold for **every** accel-outcome cell, so `deconf_pass = False` and the finding is the **metric-robust fatigue null (D20/D21)** — the warm-up phenotype is **not claimed** (exploratory only). The primary-cell / no-load² / EB-within variants remain reported in the sensitivity table.

## B7 — the saturating-curve "reps to reach peak" phenotype is boundary-censored · 2026-10-11
**What.** D23's headline phenotype fits a 3-parameter saturating curve per player and reports the
population **median** `sat_reps_to_peak`. On the real DB data the exponential curve is frequently
at/near the profile grid's flat limit: **46 of 122 players (37.7%)** are pinned at the upper grid
bound (`rtp_grid_max = 200`) — i.e. no identifiable peak within the session — and the median over
*all* players (17.1 reps) is driven by the grid bound. The induced reliability is borderline
(`sat_reps_to_peak` R = 0.256; hyperbolic alt R = 0.210). `sat_ceiling`/`sat_rate_k` do **not**
clear the gate (R = 0.00 / 0.02).
**Coder's handling.** Reported as computed (median over all fitted players = 17.1 reps) **plus**
the censored fraction and the median over identified players only, in `warmup_summary.json`
(`headline.frac_censored_at_grid_max`, `.median_reps_to_peak_identified`) and `SUMMARY_3d.md`.
**PM action requested:** decide whether to report the censored median, the identified-only median,
or to treat the saturating phenotype as non-identifiable.

**Resolution (2026-10-11, D24).** The headline number is the **identified-only median 4.14 reps**; the all-player median (17.1) is explicitly flagged as **cap-driven** — 46/122 players (37.7%) are pinned at the grid cap 200 (no identifiable peak). The saturating phenotype is reported as **exploratory/borderline** (see D24).

**Not hard blockers:** the pipeline runs end-to-end, is deterministic (byte-identical across two
`--force` runs) and all tests pass; these are verdict-interpretation ambiguities the Coder is
flagging rather than silently resolving.
