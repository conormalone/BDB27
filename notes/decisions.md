# Decisions log — BDB27

Append-only. Each entry: what was decided, why, and the evidence/constraint behind it. Binding spec = `TASK.md`.

---

## D1 — Cycle scope: Phase 0 + Phase 1 (audit gate) · 2026-10-09
**Decision.** This cycle delivers Phase 0 (setup/rules/schema) and Phase 1 (the **go/no-go audit**, questions a–g) and the spec-layout scaffold. Phase 2 (Combine features) is **not** started this cycle.
**Why.** TASK.md frames Phase 1 as a hard gate ("stop … if no group qualifies"); the audit must be signed off before feature engineering. Scope is deliberately bounded.

## D2 — Process: coder/reviewer/validator could not be spawned; PM executed inline · 2026-10-09
**Decision.** The PM role owns `diagnose → spawn coder → spawn reviewer + validator`. In this run **no `sessions_spawn` tool was available** (sub-agent at depth 1/1 with a policy-filtered tool set: read/write/edit/exec/… only). Rather than stall, the PM **executed the audit directly** and performed **inline self-review + validation** (schema assertions, edge-case checks, determinism re-run, adversarial checks on the (c)/(d)/(f) findings).
**Why / caveat.** This is a **deviation** from the letter of `ROLES.md`/`TOOLS.md` forced by the environment. Flagged to Main: if independent coder/reviewer/validator passes are mandatory, this cycle's artifacts need a separate review pass. All methodology calls remain the PM's (D3–D11).

## D3 — Recommended position group = **DB**; drill family = **FORTY_YARD_DASH** · 2026-10-09
> ⚠️ The **drill choice** below (`FORTY_YARD_DASH`) is **SUPERSEDED by D12** (family = ALL drills, 2026-10-09). The **position-group choice** (DB) is **CONFIRMED by D13** (population = **DB only, LOCKED**, 2026-10-09; WR excluded). Kept for history.

**Decision.** Primary study unit = **defensive backs**, primary repeated maximal-effort drill = the **40-yard dash**.
**Evidence (audit b/c):** 40-yd is the only drill with broad repeats — DB 93 of 102 runners have ≥2 attempts (195 attempts), ahead of DL 84, OL 84, WR 82, TE 32. `THREE_CONE_DRILL` / `SHORT_SHUTTLE` are far too sparse per position (≤13 players with ≥2 attempts) and 61–65% opted out at the results level. 40-yd also has the lowest opt-out (16.9%).
**Why DB specifically:** the largest 40-yd repeat cohort and a high-intensity in-game role — which matters for the Phase 4/5 in-game decay link.
**Secondary:** keep 3-cone/shuttle descriptively only; do not pool them into the primary model.

## D4 — `dis` is unreliable; recompute distance from x/y · 2026-10-09
**Decision.** Do **not** use the provided `dis` column as path distance. Recompute distance by integrating `x,y` per frame (Phase 2); keep `dis` only as an advisory cross-check.
**Evidence (audit d):** summed `dis` under-counts the x/y path by a median **9% (40-yd)** to **40–65% (skill drills)**; 80–100% of attempts differ >5%. The x/y trajectories are smooth (max 10 Hz step 1.2 yd; no teleports), so x/y is the trustworthy source. The low 40-yd correlation (r=0.19) is a low-variance artefact (all 40-yd attempts ≈40 yd).

## D5 — Order is confounded with drill type; identify from within-drill repeats · 2026-10-09
**Decision.** Do **not** lead with raw drill order as the fatigue exposure. Identification rests on **within-drill, within-player repeated attempts** (attempt 1 vs 2+) plus **load/rest timing**.
**Evidence (audit c):** `FORTY_YARD_DASH` is always the first drill (rank sd = 0; pairwise order 100% fixed); the only cross-player order variation is `SHORT_SHUTTLE` ↔ `THREE_CONE_DRILL` (≈50/50) — two *different* drills, so still drill-confounded. This matches the de-risk memo (order ≈ drill name, η²≈0.85).

## D6 — Missing values: `NA` → NULL at read time · 2026-10-09
**Decision.** All CSV→Parquet conversions pass `nullstr=['NA','']`.
**Why.** Audit found that `read_csv_auto` else keeps `"NA"` as a **string**, hiding real missingness (first run reported 0 nulls in `combine_results`). After the fix: `three_cone` 64.7%, `short_shuttle` 60.6%, `bench_reps` 62.4%, `forty` 16.9% null. Logged as a fixed bug.

## D7 — Figure cap: adopt the stricter internal limit · 2026-10-09
**Decision.** Cap figures at **≤5** and the writeup at **≤2,000 words**.
**Why.** Official rules say "no more than 2,000 words and fewer than 10 tables or figures"; `TASK.md` imposes 5 figures. The stricter cap satisfies both. No rule conflict (a stricter self-limit is not a conflict).

## D8 — Sample discipline · 2026-10-09
**Decision.** Every sample-mode artifact carries the string `UNVALIDATED SAMPLE OUTPUT` (a `provenance` column in each table + `outputs/audit/PROVENANCE.txt` + banners). Never interpret/tune against sample results. Game-side audit (e) is **skipped** in sample mode and marked **PENDING FULL RUN**.
**Why.** TASK.md rules; the game file is a non-representative 2²⁰-row prefix (schema.md §5).

## D9 — External data: default OFF · 2026-10-09
**Decision.** Use only the provided 2027 dataset in the primary pipeline. If an external source is ever used it must be cited, rule-compliant and logged here first.
**Why.** Official rules permit external data (with obligations), but `TASK.md` says "no external data unless permitted"; default-off is the safe reading.

## D10 — Attempt-numbering gaps treated as lost data (Phase 2 plan) · 2026-10-09
**Decision.** Any gap in attempt numbering = **lost data**. Impute **load only** (add the drill-level median cost), flag imputed rows, and exclude them from performance observations (TASK.md Phase 2).
**Evidence (audit g):** overall 32.1% of player-drill groups have a numbering gap; for the 40-yd dash by position: DB 27.5% (17.6% missing attempt 1), DL 25.8%, WR 17.0%, TE 17.6%, OL 6.3%. Non-trivial → imputation and the robustness refit (observed-only load) both matter.

## D11 — Time zone unverified · 2026-10-09
**Decision.** Treat `time` as **naive, assumed UTC** (de-risk memo: on-field testing at clock hours ≈13:00–21:00 US-Eastern). Do not build any exposure on absolute time-of-day until verified.
**Evidence (audit a):** timestamps carry no TZ offset (`Z`/`+hh:mm` absent).

---

## Gate decision (Phase 1)

| Gate condition (TASK.md) | Result |
|---|---|
| Required timing field present | **PASS** — `time` (TIMESTAMP) + `attempt` present; attempt~time agreement 0.9992 (1/1315 pairs off, 1 violation) |
| A position group qualifies | **PASS** — *(superseded by D12/D13: family = ALL drills, population = DB only)* — original Cycle-1 result: DB, 40-yd dash (D3) |

**VERDICT: GO.** ~~Proceed to Phase 2 (Combine features) for DB / 40-yard dash.~~ **SUPERSEDED by D12/D13** (family = ALL drills; population = DB only, LOCKED) — see the Cycle-2 section below.
**Recorded caveat:** the fatigue effect is **not** identified from drill *order* (D5); the study's credibility rests on within-drill repeat attempts, load/rest covariates, and honest reporting of a possible null. Phase 3 has a pre-registered reliability stop (<0.2 → report null).

*No blocker raised (see `blockers.md`).*

---

# Cycle 2 — all-drills family + position scope (supersedes D3's drill choice)

> Process note (extends D2): this cycle was again executed **inline by the PM** — no `sessions_spawn`/child agents at depth 1/1. Changes were **self-reviewed inline**; an **independent review will be run separately by Main**. All sample artefacts remain stamped `UNVALIDATED SAMPLE OUTPUT`.

## D12 — Drill family = **ALL drills** (supersedes D3's drill choice); "≥3 observed attempts" read as TOTAL across the family · 2026-10-09
**Decision (human, supersedes D3's drill pick).** The repeated maximal-effort drill **FAMILY = ALL drills** — every `drill_type` (`FORTY_YARD_DASH`, `THREE_CONE_DRILL`, `SHORT_SHUTTLE`, and the position-specific `SKILL_DRILLS_{WR,DB,DL,OL,TE,LB}` blocks), **pooled**. This supersedes D3's choice of `FORTY_YARD_DASH` alone. D3's *position-group* choice is **confirmed by D13** (population = DB only, LOCKED; WR excluded).
**Why (Conor, 2026-10-09).** (i) The 40 alone caps at **≤2 attempts/player** (audit b: DB 102 runners, max 2), so it can never reach the ≥3 observed attempts the Phase 5 link requires; pooling the whole family lifts per-player counts past 3. (ii) The Phase-3 model already keys on `C(drill)` and Phase 2 standardises performance **within drill**, so pooling *different* drills is statistically defensible — drill identity is absorbed by the fixed effect.
**Reading of the spec's "≥3 observed attempts per player" (recorded explicitly — ambiguous, chosen reading stated):** read as **TOTAL observed attempts across the family**, **not** per single drill. Chosen because (a) the human rationale above only holds under pooling; (b) the Phase-3 LMM fits `(1 + load | player)` over *all* of a player's attempts with `C(drill)` as a factor, so "attempts per player" is naturally the pooled row count; (c) a per-drill reading would permanently disqualify the 40-yd dash (max 2) and leave only a handful of `drill_name`s with ≥3, contradicting the decision to broaden the family. Encoded as `audit.min_attempts_for_link: 3` + `audit.attempt_count_unit: event_id`.
**"Observed attempt" definition.** One observed `event_id` (schema.md: "one `event_id` = one drill attempt"); excludes imputed attempts (Phase 2; none exist at audit stage). **Robustness:** ≥3 counts are **identical** under `event_id` vs distinct `(drill_name, attempt)` slots (verified per position for both families), so the A5r duplicates below do not change the threshold.
**Evidence (audit h).** Under the all-drills family **100%** of players in every position group reach ≥3 observed attempts — DB 122/122, DL 118/118, OL 121/121, TE 42/42, WR 107/107 (median 10–17). The ≥3 constraint is therefore **non-binding** under all-drills; position scope is decided by design/power instead (D13).
**Caveat (limitation, logged).** The all-drills family is broad: the `SKILL_DRILLS_*` sub-drills include route/technique drills of heterogeneous intensity, so "maximal-effort" is a looser construct than the timed battery. Kept because the human decision is explicit and the model handles it via `C(drill)` + within-drill standardisation; the tighter alternative (**timed_battery** = 40 + 3-cone + shuttle) is pre-defined in `config.yaml` for sensitivity.

## D13 — Position scope: **DB only, LOCKED** (human decision; WR excluded) · 2026-10-09
**Decision (human, Conor 2026-10-09; supersedes the earlier DB+WR draft).** The study population is **DB only**. Family = ALL drills (D12). WR is **EXCLUDED**. Encoded in `config.yaml` as `audit.study_population: DB` and returned by `recommend()` with `position_group_locked: True`. Evidence: `outputs/audit/h_family_scope.csv`.
**Why DB only / why WR is excluded.** DB is a high-intensity, high-speed in-game role that matches Phase 4's high-intensity-decay question. WR in-game intensity is **scheme-dependent** (route depth / target share vary by offensive system), so pooling WR would inject **between-system noise** into the decay signal. A **single** position group also keeps the day≈position design clean (limitations #2) and **satisfies TASK.md Phase 1's "Select ONE position group"** — so this is **not** a deviation from the spec.
**Evidence (audit h; combine-side = full combine data, game-side = PENDING FULL RUN).**
- Family **all_drills** — DB players reaching ≥3 observed attempts: **DB 122/122 (100%)**. The ≥3 link threshold is non-binding for DB.
- Family **timed_battery** — ≥3: DB 26/108 (24.1%).
- Matched Combine→NFL count for DB: **PENDING FULL RUN** (no full game files; the sample is a non-representative 2²⁰ prefix, D8).
**No position terms are pre-registered.** Because DB-only is a *single* position group, the Phase-3 LMM is the spec formula as written — `perf_z ~ load + load^2 + first_attempt + C(drill) + (1 + load | player)` — with no `C(position)` / `load:C(position)` terms. (D14 is withdrawn.) Identification rests on within-drill repeated attempts + load/rest timing (D5).
**If the full-run matched-N for DB is too small for the Phase 5 link**, that is a **blocker** (TASK.md: "no position group qualifies") — write to `blockers.md` rather than silently broadening scope. `h_family_scope.csv` retains the DB-only / DB+WR / DB+WR+DL+OL counts as *evidence*, not as a fallback plan.

## B1/B2/B3/B4 — reviewer lower-priority items closed · 2026-10-09
- **B1 (drill family)** — **resolved by D12** (all-drills); not re-litigated.
- **B2 (audit_b mixed-granularity mislabel)** — **closed.** `audit_b` summed `(nfl_id, drill_name)` series groups but labelled the result `players_ge2_attempts`, so it could exceed `players_with_drill` (DB `SKILL_DRILLS_DB`: 227 vs 122). Every player count is now a **DISTINCT-player** count (`players_ge1/ge2/ge3_attempts`); the series-group count is a separate, correctly-named column `player_drillseries_groups`. Regression test `test_audit_b_fix` asserts `players_ge2_attempts ≤ players_with_drill`.
- **B3 (missing assertions; A5 violated)** — **closed.** `grep assert src/` was empty despite `assumptions.md` claiming runtime assertions. `verify_assumptions()` now implements A1–A12 with real `assert`s (hard invariants fail loudly) and writes `outputs/audit/assumptions_check.csv`. **A5 is genuinely violated:** `event_id → (nfl_id, drill_name, attempt)` is 1:1 (A5f PASS) but **9 tuples map to 2 `event_id`s** (A5r FAIL — duplicate attempt captures, all at `attempt=1`). `assumptions.md` corrected; A5r recorded as a known data-quality finding (Phase 2 is specified to *flag* duplicate attempts, so it is non-fatal). Surfaced **A12**: the `draft_overall_pick`/`draft_round` NULLs are the **structural "undrafted"** category (127/510; the two NULL sets coincide) → **KEEP as a Phase-5 control** (N1 fix; see `assumptions.md` A12 detail).
- **B4 (magic numbers / config hygiene)** — **closed.** Order-flip threshold `0.05` → `audit.order_flip_threshold`; added `audit.min_attempts_for_link: 3` (the spec's Phase-5 requirement, previously absent) and `audit.attempt_count_unit`; moved Phase-2-only keys `session_gap_s`, `peak_speed_sanity_yds` from `audit` → `features`; added `audit.max_combine_bytes` (referenced by A11 but missing) and used it; added `audit.families` + `audit.candidate_populations`. `sample_row_count` is now **enforced** (check **S1**: sample game rows ≤ 2²⁰ cap) rather than decorative. `primary_drill_type` retained but annotated legacy/superseded (D12).

## Cycle-2 gate (re-affirmed)

| Gate condition (TASK.md) | Result |
|---|---|
| Required timing field present | **PASS** — `time` (TIMESTAMP) + `attempt`; agreement 0.9992; assertions A2/A3 PASS |
| A position group qualifies | **PASS** — all five groups qualify under the all-drills family (100% ≥3 observed attempts) |

**VERDICT: GO (unchanged).** Family = ALL drills (D12). Position scope = **DB only, LOCKED** (D13; human decision 2026-10-09; WR excluded — scheme-dependent in-game intensity).
**No blocker raised** — matched-N per population is PENDING FULL RUN, not blocking (see `blockers.md`, `pending_full_run.md`).

## D15 — Deferred TASK.md "Engineering" gaps (dtype downcasting; stage checkpointing) · 2026-10-09
**Decision.** Record two TASK.md "Engineering" requirements that are **not yet applied**, deferred to Phase 2+ (where the tracking feature stage makes them material):
(i) **dtype downcasting** — Parquet is written via DuckDB `COPY` with ZSTD compression but **no explicit float64→float32 / int64→int32 downcast** is applied (TASK.md: "Downcast dtypes").
(ii) **stage-level checkpointing** — only the CSV→Parquet **conversion** is checkpointed (`to_parquet` skips when the cache exists); **stage outputs are not** — re-running `01_audit.py` re-executes every query even when its output tables exist (TASK.md: "Stages checkpoint to disk and skip finished work unless `--force`").
**Why deferred.** At Phase 0/1 the audit is cheap (sample run ≈6 s, ≈380 MB) and the Combine Parquet is small (83.8 MB), so the cost is immaterial; both become material at Phase 2 (per-frame tracking features) and on the full run.

## D14 — (stub; withdrawn) · 2026-10-09
**Decision.** Withdrawn before adoption — no design change was made under this number. Recorded **only** so the cross-reference in **D13** ("(D14 is withdrawn.)") resolves to a real, append-only entry. No action required.

---

# Cycle 3 — Phase 2 (Combine features)

## D15 (partial resolution) — dtype downcasting + stage checkpointing landed in `02_features.py` · 2026-10-09
**Decision.** The two deferred TASK.md "Engineering" items from D15 are now **implemented** in `src/02_features.py` (the stage where they first became material):
(i) **dtype downcasting** — outputs are written via DuckDB `COPY … (FORMAT PARQUET, COMPRESSION ZSTD)` with explicit `TRY_CAST`/`CAST`: float64→`FLOAT` (float32), int64→`INTEGER` (int32), timestamps→`TIMESTAMP` (a `COLUMNS` schema table is the single source of truth).
(ii) **stage checkpointing** — the whole feature stage is skipped when `outputs/features/combine_features.parquet` exists unless `--force`; a `02_features:checkpoint` row is still appended to `outputs/run_log.csv` on the skip. Per-stage `timed_stage` rows (`:convert`, `:kinematics`, `:impute_load`, `:standardize`, `:vif`, `:write`) log wall time + peak RAM.
**Still open.** The **Phase-1** audit (`01_audit.py`) keeps the earlier behaviour (only the CSV→Parquet conversion is checkpointed); retro-fitting stage checkpoints there is cosmetic and remains optional.

## D16 — Phase-2 unit levels (config-driven) · 2026-10-09
**Decision (locked by the PM brief; implemented exactly).** Encoded in `config.yaml` under `features`:
- `attempt_level: drill_name` — attempt numbering unit (used for `first_attempt` and numbering-gap imputation).
- `standardize_level: drill_type` — `perf_z` is a z-score **within `(drill_type, combine_position)`** (rationale: most `drill_name`s have ~1 attempt/player, so per-`drill_name` groups would have sd=0; `drill_type` is the family/C(drill) unit and yields sd>0 — verified: every group in the sample run has `group_ok=1`).
- `t90_baseline_level: drill_type` — "the player's best" = that player's max observed `peak_speed_yds` within the drill_type.
- `performance_metric: peak_speed_yds` — the primary standardised metric.
**Why.** Recorded from the PM brief; standardisation level chosen to avoid degenerate (sd=0) groups. **Caveat surfaced in D18/`blockers.md`:** the `attempt_level: drill_name` premise does **not** hold for the 2025 class.

## D17 — Phase-2 kinematics, gaps, standardisation, first_attempt + VIF · 2026-10-09
**Decision (locked; implemented).**
- **Kinematics from `x`/`y`/`time` only** (the provided `s`/`a`/`dis` are **advisory cross-checks** only). Per segment: Δt, Euclidean distance `d`, speed `v=d/Δt`, accel `a=(v_i−v_{i−1})/((Δt_i+Δt_{i−1})/2)`. `peak_speed_yds=max v`, `peak_accel_yds2=max signed a`, `t90_s=ΣΔt` over counted segments with `v ≥ t90_frac·best`, `effort_cost_yd=Σd` over counted segments.
- **Frame gaps:** a gap is `Δt > gap_detect_factor·expected_dt_s` (1.5×0.1=0.15 s). Gaps inside the peak-speed segment or peak-accel window are **flagged and left raw**; **elsewhere** gaps `< interp_max_gap_s` (0.5 s) are linearly interpolated onto the expected grid, gaps `≥0.5 s` are **not** interpolated → segment excluded and `flag_large_gap`.
- **Sanity flags:** `flag_speed_outlier` (>`peak_speed_sanity_yds`=12), `flag_duplicate_attempt` (>1 `event_id` per (nfl_id, drill_name, attempt); 9 A5r tuples → 18 rows; flagged, not dropped), `flag_out_of_order` (time order disagrees with attempt order), `flag_std_group_too_small`.
- **Standardisation:** `perf_z` (primary = `peak_speed_yds`) + `peak_accel_z`, `t90_z`, `effort_cost_z`, z-scored within `(drill_type, position)` over **observed** attempts only (ddof=0); a group with `n < min_std_group_n` (3) or sd=0 → NaN + flag; group (n, mean, sd) written to `standardization_params.csv`.
- **`first_attempt`** = 1 iff original `attempt == 1` within (player, drill_name). **VIF** of {`prior_load_yd`, `first_attempt`} on study-population observed rows (statsmodels, with constant), flagged if > `vif_flag_threshold` (5.0); also `corr(load, first_attempt)` → `vif_report.csv`. Sample result: **VIF=1.089 (< 5, no flag)**, corr=−0.286.
**Why.** Recorded from the PM brief; the sanity thresholds live in `config.yaml` (no magic numbers).

## D18 — Phase-2 imputation, sessions/ordering, prior load, rest (and the 2025 attempt-numbering finding) · 2026-10-09
**Decision (locked; implemented).**
- **Numbering-gap imputation (lost data; load only):** for each `(nfl_id, drill_name)`, missing numbers = `{1..max(attempt)} \ observed`; each becomes a row with `is_imputed=1`, `event_id=NULL`, all kinematics/perf/clock NULL, `effort_cost_yd` = `player_drill` median (fallback `drill_global`). Imputed rows carry **no** performance and are excluded from standardisation.
- **Sessions:** split a player's observed timeline where consecutive attempt starts differ by `> session_gap_s` (7200 s); `session_id=f"{nfl_id}:{k}"` (k by time). *(Real combine data is single-session: max observed gap 3159 s < 7200 s.)*
- **Ordering & cumulative prior load:** within (player, session) order by `(drill_first_start_rank, attempt)`; `prior_load_yd` = Σ `effort_cost_yd` of **all** (observed+imputed) earlier attempts; `prior_load_efforts` = count of them; **plus** `prior_load_observed_yd`/`prior_load_observed_efforts` (observed-only) for the Phase-3 observed-only robustness refit.
- **Rest:** `rest_s` = start − end of the immediately preceding **observed** attempt in the session; session-first → NaN. `rest_spans_imputed=1` when an imputed attempt lies between the preceding observed attempt and this one **or** when the attempt is session-first (no observed predecessor). POLICY `rest_use=dropped_from_primary_kept_as_feature`: kept as a column and reported (distribution + Pearson corr with `prior_load_yd`, sample −0.19); **not** in the pre-registered Phase-3 primary LMM; declared a Phase-3 robustness covariate (USED).
**FINDING (2025 attempt numbering — contradicts the D16 premise).** The PM brief (and `notes/schema.md` §2) state `attempt` "restarts per `(player, drill_name)`". This holds for the **2023/2024** classes (per-`drill_name` restart; 121/81 gap-groups; max attempt 6). It **does NOT hold for the 2025 class**, where `attempt` is a **per-`(player, drill_type)` block counter**: e.g. nfl_id 58968 ran `SKILL_DRILLS_WR` once through with attempts `1..17` across 17 *different* `drill_name`s, so within any single `drill_name` only one number is observed but `max(attempt)=17`. Applying the D18 `drill_name`-level gap rule to 2025 therefore **fabricates** phantom lost attempts. Impact: imputed rows = **2023 144 + 2024 118 + 2025 7874 = 8136** under the spec level, vs **96 + 94 + 2025 148 = 338** under a `drill_type` level for 2025; for the **DB study population** the spec rule yields **1617** imputed rows (49+32+**1536**) vs ≈**130** — i.e. ~**92%** of DB imputed load is phantom, corrupting the primary exposure `prior_load_yd`.
**Action taken.** The pipeline implements the **locked** rule **exactly** and additionally emits a runtime warning + diagnostics (`imputed_rows_{2023,2024,2025}`, `imputed_share_of_observed`, `attempt_numbering_restart_warning`) when the imputed share exceeds `features.impute_warn_share`. The discrepancy is raised as a **blocker** (`notes/blockers.md`) with a recommendation (make the numbering unit **year-aware**: `drill_name` for 2023/24, `drill_type` for 2025) for the PM to decide **before** Phase 3 / the full run. Not changed unilaterally (design marked LOCKED; "do not re-litigate").

## D19 — Phase-2 attempt-numbering unit is **empirical per (player, drill_type)**; supersedes D16/D18's `drill_name` premise · 2026-10-10
**Decision (human-approved fix; Conor 2026-10-10).** The `attempt` numbering unit for gap-imputation and
`first_attempt` is **detected empirically per `(nfl_id, drill_type)` block**, not hardcoded by year. A block is a
**per-`drill_name` restart** unit (`attempt_unit='drill_name'`) iff it has **≥2 distinct `drill_name`s AND
≥ `features.attempt_restart_min_drill_names` (2) of them have `min(attempt)==1`**; otherwise it is a
**per-`drill_type` block counter** (`attempt_unit='drill_type'`). Single-`drill_name` blocks are unit-invariant.
**Why (evidence — full combine data, 6,310 observed attempts).** D16/D18 locked `attempt_level: drill_name`,
which matches 2023/2024 (`attempt` restarts per `(nfl_id, drill_name)`) but is **violated by 2025**, where
`attempt` is a per-`(nfl_id, drill_type)` block counter. Implemented literally it fabricates phantom reps and
inflates `prior_load_yd`.
- Spec rule (`drill_name`): imputed rows 2023 144 / 2024 118 / **2025 7,874** (1,749 groups); **DB 2025 = 1,536**
  of 1,617 DB-imputed (~92% phantom).
- Empirical unit: imputed rows 2023 144 / 2024 118 / **2025 148** (424 blocks, 104 with a gap); **DB 2025 = 28**;
  nfl_id 58968 (WR) = **0**.
- Worked example: 58968 ran `SKILL_DRILLS_WR` once through, attempts 1..17 across 17 distinct `drill_name`s
  (OVER_SHOULDER_ADJUST=1 … RED_ZONE_FADE_RIGHT=17), real timestamps 23:48:41→00:50:17; zero genuinely lost reps.
- Detection validated clean: 2023 160/160 and 2024 177/177 multi-`drill_name` blocks → `drill_name`; 2025 0/173
  → `drill_name` (173 → `drill_type`), reproducing 148 exactly.
**Consequences.** (i) gap-imputation recomputed at the detected unit → 2025 imputed load falls to the genuine
~148; imputed rows still carry **load only** (no performance/clock). (ii) `prior_load_yd`/`prior_load_efforts`
recomputed; `prior_load_observed_yd`/`prior_load_observed_efforts` kept (observed-only) as the pre-registered
Phase-3 **robustness** refit. (iii) ordering uses real timestamps; imputed reps (no timestamp) are slotted only
within their detected unit via `unit_first_start_rank`. (iv) new provenance column `attempt_unit`. (v)
`first_attempt` redefined below.
**`first_attempt` (definition documented + construct flagged to Main).** = **1 iff the rep is the player's
earliest *observed* rep of that `drill_name` (by `attempt_start_time`; ties by min `attempt`)**; imputed rows = 0.
Rationale: the original `attempt==1` is degenerate under block numbering (only the block's first rep). **Construct
flag:** under block numbering this flags ~one rep per distinct `drill_name` (the warm-up of each skill/task),
whereas the unit-consistent alternative ("first attempt of the *numbering unit*": `attempt==1` within `drill_name`
for restart units, within `drill_type` for block units) flags exactly the block's first rep (the session's true
warm-up). The per-brief definition is implemented; **confirm the warm-up construct before Phase 3**. For
2023/2024 the change is limited to the 121/81 gap-groups where attempt 1 was lost (first observed rep now flags 1).
**Not changed:** `standardize_level`, `t90_baseline_level`, `performance_metric`; all other D16/D18 choices stand.
D16/D18's `attempt_level: drill_name` premise is superseded by this entry.

---

# Cycle 6 — Phase 3 (Combine fatigue model)

## D20 — Phase-3 model decisions (fallback chain, load² criterion, reliability, permutation, stop rule) · 2026-10-10
**Context.** `src/03_combine_model.py` fits the pre-registered within-player fatigue model on the DB-only
study population (`combine_features_study.parquet`; model rows = `is_imputed=0 AND perf_z IS NOT NULL`:
**122 players, 1,432 observations**; real DB data; combine file is complete so both modes fit for real —
`--mode` only flips the provenance label). All thresholds/params live in the new `config.yaml` **`model:`**
section (no magic numbers). Methodology is the PM's; encoded exactly.

**1. Convergence rule (implemented literally).** Every LMM tries `model.lmm_optimizers` in order
`[lbfgs, bfgs, cg, powell]`; a fit is **clean/converged** iff `res.converged is True` **and** no
`statsmodels.tools.sm_exceptions.ConvergenceWarning` is outstanding (category-based, not message-based).
`MixedLM` raises `ConvergenceWarning("The MLE may be on the boundary of the parameter space.")` whenever a
RE variance/co-variance diagonal `< 0.01` (statsmodels `mixed_linear_model.py:2433-2436`); this is treated
as **not clean** (the RE structure has collapsed), per the spec's "no ConvergenceWarning is outstanding".

**2. Fallback chain + which path the real data took.** `model.fallback_order =
[lmm_random_slopes, lmm_uncorrelated_re, per_player_eb, random_intercept]`; stop at the first *clean*
estimator. On the real data:
- `lmm_random_slopes` (`(1 + load_c | player)`): **FAILS** — `lbfgs/bfgs/cg` `converged=False`; `powell`
  `converged=True` but a boundary `ConvergenceWarning` → not clean. (The MLE sits at the boundary:
  random-slope variance ≈ 0 ⇒ no between-player slope heterogeneity.)
- `lmm_uncorrelated_re` (`vc_formula = (1|player) + (0+load_s|player)`, centred/rescaled load):
  **FAILS** — `lbfgs`/`powell` `converged=True` but both emit the boundary warning (vc variance `< 0.01`).
- `per_player_eb` (per-player OLS slopes + empirical-Bayes shrinkage): **ACCEPTED** — selected.
- `random_intercept`: not run.
Which optimiser converged (per estimator) is logged in `combine_model_diagnostics.csv`
(`converged_lmm_random_slopes`/`_uncorrelated_re`/`_per_player_eb` = `failed`/`failed`/`accepted(ols)`).
The uncorrelated-RE parameterisation is genuinely diagonal (separate variance component), matching the spec's
`(1|player) + (0+load|player)`.

**3. load² spread criterion (documented, no magic numbers).** Include `load2` iff
`n_unique(prior_load_yd) >= model.load2_min_unique (20)` **AND** `IQR(prior_load_yd) >=
model.load2_min_iqr_yd (10.0 yd)`. Real DB data: **n_unique = 1,329, IQR = 244.45 yd → load² INCLUDED**
(evidence recorded in diagnostics: `load2_included`, `load_n_unique`, `load_iqr_yd`). Load (and load²) are
centred for stability (`load_c = load − mean`, `load2_c = load_c² − mean(load_c²)`).

**4. Reliability definition (spec item 6).** `reliability = tau2 / (tau2 + mean_i(SE_i²))`.
- LMM path: `tau2` = fitted random-slope variance `G_22`; `SE_i` = posterior (BLUP) SE of player i's slope =
  `sqrt(diag((Z_i' V_i⁻¹ Z_i + G⁻¹)⁻¹))` (statsmodels `random_effects_cov`).
- EB path: `tau2 = max(0, Var_obs(slopes) − mean_i(SE_i²))` (the spec's `eb_tau2_method: dl` formula; the
  Q-based DL estimator is additionally reported for comparison); `SE_i` = per-player OLS slope SE.
- Reliability is computed for **every estimator fitted**; the **final estimator's** reliability drives the
  stop rule. If `< model.reliability_min_players (20)` players yield usable slopes, reliability is forced to 0.

**5. permutation (`perm_stat: meta_slope`) + why.** Target = the population load slope of the final
estimator. Statistic = the **inverse-variance-weighted mean per-player load slope** (fast + deterministic;
a per-permutation refit of the full LMM would be 1,000× the fit cost and non-determinism-prone). The
`(load, load2, first_attempt, drill)` block is shuffled **within each player** (keeping `perf_z` per row);
since the statistic depends only on the permuted load, the joint block shuffle is equivalent to shuffling
load alone. `np.random.default_rng(seed + model.perm_seed_offset)`, `perm_n = 1000`,
`p = (1 + #{|stat_perm| ≥ |stat_obs|}) / (perm_n + 1)`. **Odd/even split-half is NOT used** (spec).
Real data: `stat_obs = −0.000444`, **perm p = 0.0290** (2,000+ within-player order shuffles give a
comparable tail; see `combine_model_permutation.csv`).

**6. Stop rule outcome (the finding).** Final estimator `per_player_eb`, `tau2 = 0`, `mean_se² = 4.5e-6`,
**reliability = 0.0 < 0.2 → `stop_rule_triggered = true` → the NULL is reported as the finding** in
`SUMMARY.md` (honestly; no forced result). The pooled population load coefficient is **−0.000444 yd⁻¹**
(SE 0.000160, p = 0.0055; permutation p = 0.029) and the within-player baseline late−early difference is
0.459 z (t = 6.66, p = 8.4e-10) — i.e. there *is* an average decline — **but there is no reliable
*between-player* slope variation** (tau2 = 0), so per-player slopes cannot be trusted for the Phase-5 link
at this population. Slopes are still emitted (flagged) for completeness.

**7. Robustness refits.** `observed_only_load` (uses `prior_load_observed_yd`), `without_first_attempt`, and
both together. On the real data all three select `per_player_eb`; `observed_only_load` coef **−0.000481**
(p 0.0045) vs main **−0.000444** (p 0.0055) — small, same sign. **Caveat:** under the EB path the
`without_first_attempt` refit is *numerically identical* to the main fit, because per-player OLS slopes do
not use `first_attempt` (it only enters the LMM fixed part, which is not selected here); the refit is
retained for completeness and would bite only if an LMM were accepted. **D19 `first_attempt` is FINAL — not
redefined.**

**8. Phase-5 handoff (`combine_player_slopes.parquet`/`.csv`).** Shrunken (**empirical-Bayes**) per-player
slopes: `slope_raw` = per-player OLS slope on `load_c`; `slope_shrunk` = `w·slope_raw + (1−w)·mu` with
`w = tau2/(tau2+SE_i²)` (the *shrinkage weight*); `mu` = inverse-variance-weighted population mean; the CI
uses the **per-player OLS SE** (`slope_se`) so intervals are non-degenerate when `tau2 = 0`
(`slope_shrunk ± z·slope_se`, normal, `player_slope_ci_level = 0.95`). `method` records the **population
estimator** fitted; `reliability` its value; `meets_link_min_attempts` = `n_attempts_model ≥
model.link_min_attempts (3)` (all 122 real DB players meet it).

**9. Cross-drill-group correlation.** Per `(player, drill_type)` slopes on centred load for players with
`>= xdrill_min_attempts (3)` attempts in each of ≥2 drills, pairwise Pearson across shared players requiring
`>= xdrill_min_players (10)`. Real data: **all pairs have 0 shared players** (the 40-yd dash / 3-cone /
shuttle max out at ≤2 observed attempts per player, so no player has ≥3 in two different drills); the table
is emitted with `meets_min_players = false` — an honest null, not a computed correlation.

**10. Guard added (robustness).** A (near-)constant `perf_z` field produced numerically-noisy per-player
slopes (~1e-16) and a spurious reliability ≈ 1; `per_player_slopes` now **skips players with exactly zero
outcome variance** (constant performance ⇒ slope unidentifiable ⇒ reliability 0 ⇒ stop rule fires).
Discovered and fixed during the Phase-3 test cycle.

**Determinism.** Verified byte-identical across two `--force` sample runs (all 11 `outputs/model/` files).
DuckDB `threads=1`; fixed seeds; every table sorted; the LMM coefficient values are *not* written to any
artefact unless the LMM is the selected estimator (the real data selects EB, so all written numbers come from
deterministic EB/baseline/permutation paths).

**11. Phase-5 link gating fix (`skip_phase5_link`) · 2026-10-10.** `skip_phase5_link` is now set to
`stop_rule_triggered OR estimator == random_intercept` (assigned in `run_pipeline` immediately after the
stop rule is evaluated). Reason: `tau2 = 0` ⇒ zero between-player predictor variance ⇒ the Phase-5 link must
be skipped (the EIV / regression-calibration correction is undefined at reliability 0). Previously the
diagnostics reported `stop_rule_triggered = True`, `reliability = 0.0`, yet `skip_phase5_link = False` — an
inconsistency flagged by independent review and fixed here; all other headline numbers are unchanged
(estimator `per_player_eb`, population load coef −0.000444, p 0.0055, perm p 0.029).

---

# Cycle 7 — Phase 3b metric panel (pre-registered)

## D21 — Pre-registered metric panel: does a better load metric rescue per-player slope reliability ≥ 0.2? · DECLARED 2026-10-10T16:29:33Z (2026-10-10 17:29 IST)
**Declaration order (no HARKing).** This entry and the `config.yaml` **`panel:` block** were written and
committed to the working tree **BEFORE any panel result was computed or viewed**. The thresholds, weights and
formulas below are **FIXED**; they are not tuned against results. Purpose: test whether a better load metric
rescues the per-player slope **reliability ≥ 0.2** gate (the Phase-5 gate) versus the flat, metric-robust
Phase-3 null (D20: reliability 0.0, stop rule fires).

**Load metrics** — all **cumulative prior load since session start**, mirroring Phase-2 `prior_load_yd`
semantics (observed + imputed; imputed rows carry **no** performance value and are **not** model observations
but **DO** contribute cumulative load). Computed from the 10 Hz frames (`combine_tracking.parquet`).
- **L1 `prior_load_yd`** — cumulative distance (yards) [existing primary].
- **L2 `prior_load_hmld`** — cumulative **HMLD** (yards) = Σ_i dist_i·[1(v_i > v_hs) + 1(|a_i| > a_th AND v_i ≤ v_hs)]
  (the declared **threshold-sum**: high-speed distance + accel/decel distance-equivalent).
- **L3 `prior_load_hsd`** — cumulative high-speed distance = Σ_i dist_i·1(v_i > v_hs).
- **L4 `prior_load_accel`** — cumulative accel/decel distance-equivalent = Σ_i dist_i·1(|a_i| > a_th AND v_i ≤ v_hs).
- **L5 `prior_load_efforts`** — cumulative effort count [existing].
- **L6 `elapsed_session_s`** — elapsed session time to attempt start (seconds) [existing].
- **L7 `prior_load_mp`** — **di Prampero metabolic-power variant (robustness)**: cumulative distance (yards)
  where instantaneous metabolic power P > `mp_threshold_wkg` (25.5 W/kg).
**Decomposition (item 5).** The distance axis splits into a **speed** component (L3 = HSD) and an
**accel/decel** component (L4); both are reported. (L1 = high-speed distance + low-speed distance; L3+L4 is
the high-intensity analogue, and by construction L2 = L3 + L4 at the per-attempt level.)

**Thresholds / weights (SI; FIXED, declared before results):**
- `speed_hs_mps: 5.5` (≈6.0145 yd/s); `accel_th_mps2: 2.0`; `mp_threshold_wkg: 25.5`.
- Clipping (physically-impossible artifacts exist up to ~26 yd/s and ~44 yd/s²): `speed_clip_mps: 11.0`
  (≈12.03 yd/s); `accel_clip_mps2: 13.72` (≈15.0 yd/s²). **Clip BEFORE thresholds**; clipped-frame counts logged.
- Smoothing before the 2nd derivative: **Savitzky–Golay on x,y** (`smooth_window_frames: 7`,
  `smooth_polyorder: 3`); speed/accel are computed from the **smoothed positions**; cross-checked against the
  provided `s`/`a` with agreement (mean abs diff, correlation) reported. (For attempts with fewer frames than
  the window, the effective window is the largest odd window ≤ n, reduced only as needed to stay valid.)
- Unit conversion: 1 yd = 0.9144 m (`m_per_yd: 0.9144`); g = 9.81 m/s² (`g_mps2`).
- **di Prampero/Osgnach energy cost** (source: P. E. di Prampero et al., *J Appl Physiol* 2005; modelled by
  Osgnach et al., *Med Sci Sports Exerc* 2010 for soccer match-play): ES = arctan(a/g);
  EC(J/kg/m) = 155.4·ES⁵ − 30.4·ES⁴ − 43.3·ES³ + 46.3·ES² + 19.5·ES + 3.6; P(W/kg) = EC·v (v m/s, a m/s²).
  Coefficients stored descending in ES (`ec_coef`) and evaluated with `np.polyval`; a is the signed
  (smoothed) acceleration so deceleration yields the lower-energy branch.
- **load² rule (metric-agnostic, per cell):** include load² iff `n_unique(load) ≥ 20` **AND** `IQR(load) > 0`;
  `n_unique`/`IQR` recorded per cell. (This is the panel's declared rule; Phase-3's `model.load2_*` rule is
  unchanged for `03_combine_model.py`.)
- **Panel cell rule** = the **SAME** Phase-3 fallback chain (`model.fallback_order`) + reliability formula
  (`tau2 / (tau2 + mean_i(SE_i²))`) + stop rule (`model.reliability_stop_threshold = 0.2`); per-cell
  permutation p (≥1000 within-player shuffles, `meta_slope` statistic, `panel.perm_seed_offset`).

**Outcome metrics** (per-attempt performance, standardised within (drill_type, combine_position) — Phase 2
already emits these): **O1 `perf_z`** (peak speed; current primary) · **O2 `peak_accel_z`** (peak acceleration)
· **O3 `t90_z`** (time above 90% of best).

**Panel** = every (L, O) combination (**L1–L7 × O1–O3 = 21 cells**). Per cell record:
`load_metric, outcome_metric, estimator, optimizer, load_coef, load_se, load_p, perm_p, tau2, mean_se2,
reliability, reliability_met (≥0.2), stop_triggered, skip_phase5_link, n_players, n_obs, load2_included,
load_n_unique, load_iqr`.

**Implementation notes (non-methodology).** New metrics are computed in `src/03b_metric_panel.py` from
`data/interim/parquet/combine_tracking.parquet` + the Phase-2 study frame (`combine_features_study.parquet`,
DB-only); `03_combine_model.py` gains an optional `outcome_col` parameter (default `model.outcome ⇒ perf_z`)
so its behaviour/outputs are unchanged. Per-attempt new-metric distance is summed over segments with
`0 < dt ≤ features.distance_gap_max_s` (the same distance basis as `effort_cost_yd`). The study-frame ordering
is reproduced deterministically and **validated** by recomputing `cumsum(effort_cost_yd)` and requiring it to
equal the Phase-2 `prior_load_yd`. Imputed rows take the player-drill median (fallback: drill-level median) of
each new metric, matching Phase 2's `_impute_rows`.
**Scope.** No Phase-4/5 work; `01`–`03` behaviour and existing outputs must remain byte-identical. This panel
is a robustness/exploratory battery on the **combine** side only; results reported honestly (nulls included).
Not committed until an independent Reviewer + Validator sign off.

---

**D21 — independent Reviewer + Validator sign-off · 2026-10-10.**
The declared metric panel was verified by an independent Reviewer+Validator (combined) **before** commit:
**PASS, no blocking defects**.
- *Independent re-derivation:* all 21 cells rebuilt by calling the model code directly on the panel frame
  match `outputs/model/metric_panel.csv` exactly (estimator/optimizer/load_coef/SE/p/tau2/mean_se2/
  reliability/stop/perm_p; zero mismatches). Per-attempt HSD/accel/HMLD/MP recomputed from the raw 10 Hz
  frames with an independent implementation match to <1e-9. The L1/O1 cell reproduces Phase-3's headline
  exactly (coef −0.000444004477416772, reliability 0.0, perm_p 0.028971).
- *Construction:* `entity_type='PLAYER'` only; acceleration from **savgol-smoothed** positions (not raw
  double-difference); clipping **before** thresholds; SI→yard conversions and the di Prampero/Osgnach EC
  polynomial verified; every threshold/weight present in `config.yaml` and matching D21 verbatim; the
  HMLD = HSD + accel decomposition holds per-attempt and cumulatively.
- *Determinism:* two `--force` sample runs give byte-identical `metric_panel*` outputs. *Tests:*
  `test_metric_panel.py` 7/7 + `test_audit.py` 6/6, `test_features.py` 8/8, `test_combine_model.py` 7/7.
- *Regression:* the `03_combine_model.py` change is additive-only; its outputs are byte-identical and D20's
  headline (estimator `per_player_eb`, load coef −0.000444, reliability 0.0) is unchanged.
- **Headline:** **3 of 21 cells** clear reliability ≥ 0.2 — `prior_load_accel×peak_accel_z` (0.5357),
  `prior_load_efforts×peak_accel_z` (0.5934), `prior_load_efforts×t90_z` (0.5568) — all via
  `lmm_uncorrelated_re` with a **positive** load coef, i.e. a **potentiation/warm-up** direction, **not**
  fatigue. No fatigue-direction cell clears the gate; the population null is metric-robust.
- **Caveats carried to the report (honest).** (i) The three gate cells clear the threshold **only under the
  LMM fallback** (`lmm_uncorrelated_re`); under the deterministic EB estimator their reliabilities are
  0.0 / 0.0 / 0.0778 — all < 0.2 — so the "rescue" rests on the LMM variance-component estimate, not a
  robust slope signal. (ii) 2 of the 3 gate cells use `peak_accel_z`, whose Phase-2 definition
  (`peak_accel_yds2`) is the **instantaneous central-difference max**, not the ~0.3–0.5 s average assumed
  in the brief (Phase-2 committed at Cycle 4; flagged, not changed).
