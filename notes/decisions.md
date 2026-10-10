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
