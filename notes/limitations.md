# Limitations & threats to validity — BDB27

Initial register (Phase 0/1). Every threat carries a mitigation; this file grows with each phase and feeds
the writeup's Limitations section.

## Data & design

1. **Drill order is confounded with drill type.** Within a combine session the protocol is fixed
   (`FORTY_YARD_DASH` first, then positional skill drills, then 3-cone/shuttle); only the relative order of
   the two agility drills varies (D5). *Mitigation:* never use raw order as the fatigue exposure; identify
   from **within-drill, within-player repeated attempts** plus load/rest timing.
2. **Combine day = position group.** Days are position-staggered, so "later day" is confounded with
   position/ability. *Mitigation:* restrict the study to **one position group — DB (D13)**; there is then no
   between-day, between-position exposure to confound the fatigue slope. (This is the single-position design
   that satisfies TASK.md Phase 1; WR is excluded because its in-game intensity is scheme-dependent.)
3. **Selection bias (opt-outs are non-random).** Participation is voluntary and correlated with draft
   stock. Results-level opt-out: `forty` 16.9%, `three_cone` 64.7%, `short_shuttle` 60.6%. *Mitigation:*
   use 40-yd (lowest opt-out); report selection explicitly; never claim population representativeness.
4. **Only players who reached NFL games have game data** (Phase 5 selection limitation). *Mitigation:*
   state it; descriptively compare matched vs unmatched prospects (audit e, full run).
5. **Lost attempts.** 32% of player-drill groups have an attempt-numbering gap (DB 40-yd: 27.5%).
   A gap = lost data. *Mitigation:* impute **load only** with the drill-level median, flag imputed rows,
   exclude them from performance observations, and refit with observed-only load (Phase 2/3 robustness).
6. **Small n.** Under the all-drills family the DB population is **122 players, all with ≥3 observed
   attempts** (`h_family_scope.csv`; DB 122/122), so the Phase-5 link threshold is met — but the *timed*
   battery is much thinner (DB timed_battery: 26/108 reach ≥3) and genuine per-player slope precision remains
   the binding concern. *Mitigation:* within-player late-minus-early baseline; shrunken slopes;
   pre-registered reliability stop (<0.2 → null).

## Measurement

7. **All-drills family is broad (D12).** The family pools `FORTY_YARD_DASH`, the agility drills and the
   position-specific `SKILL_DRILLS_*` blocks; the skill sub-drills include route/technique work of
   heterogeneous intensity, so "maximal-effort" is a looser construct than a pure timed battery.
   *Mitigation:* the model keys on `C(drill)` and standardises **within drill** (Phase 2), so non-maximal
   drills contribute load/rows without biasing the within-drill contrast; the tighter **timed_battery**
   (40 + 3-cone + shuttle) is pre-defined in config for a sensitivity refit.
8. **Performance measurement error / slope reliability.** Per-player fatigue slopes are noisy.
   *Mitigation:* model-based reliability (slope variance / (slope var + mean squared SE)); ≥3 observed
   attempts required for the Phase 5 link; error-in-both-slopes correction (regression calibration /
   bivariate hierarchical) in Phase 5.
9. **Provided `dis` column unreliable** (under-counts path 9–65%, D4). *Mitigation:* recompute distance
   from `x,y`; sanity-check against official splits.
10. **Coordinate frame is the local Combine frame, not the NFL field.** *Mitigation:* never reuse NFL
    field assumptions; use per-drill extents (`notes/schema.md`, audit f).
11. **Time zone unverified** (naive timestamps). *Mitigation:* avoid time-of-day exposures until confirmed
    (D11).
12. **Frame gaps / jitter.** *Mitigation:* interpolate gaps <0.5 s only; flag attempts with gaps inside the
    peak-speed/accel window; flag speeds >12 yd/s and duplicate attempts (Phase 2).

## In-game side (Phase 4/5)

13. **Sample is non-representative** (2²⁰-row prefix). *Mitigation:* never tune/report from sample;
    game-side work is full-run only (PENDING FULL RUN).
14. **Threshold provenance.** Must be "FULL" in full runs; frozen before Phase 5. *Mitigation:* runtime
    assert; `00_thresholds.py`.
15. **Draft position control: the nulls are STRUCTURAL "undrafted", not missing.** `players.draft_overall_pick`
    and `players.draft_round` are null for the **same 127 / 510** prospects (the undrafted), so the >20%-null
    exclusion rule — reserved for **non-structural** missingness — does **not** fire. *Mitigation:* **keep**
    draft position as a Phase-5 control with **"undrafted" encoded as its own level** (a dummy `undrafted`
    flag, or a capped/ranked pick with an explicit "undrafted" category); optionally report a complete-case
    sensitivity. See `assumptions.md` A12.
16. **Duplicate attempt captures (A5r).** 9 `(nfl_id, drill_name, attempt)` tuples carry 2 `event_id`s
    (all `attempt=1`). *Mitigation:* Phase 2 flags/​deduplicates duplicate attempts; the ≥3 link-threshold
    counts are unaffected (identical under `event_id` vs attempt-slot). See `assumptions.md`.
17. **Population LOCKED to DB only (D13).** The study population is locked to **DB only** (human decision,
    2026-10-09); **WR is excluded** because WR in-game intensity is scheme-dependent (between-system noise in
    the decay signal). *Mitigation:* the single-position design keeps the day≈position confounding closed
    (limitation #2) and **satisfies TASK.md Phase 1 ("select ONE position group")** — no deviation; no
    position terms are pre-registered (Phase 3 uses the spec formula as written). If the full-run matched-N
    for DB is too small for the Phase 5 link, it is a blocker (write to `blockers.md`), not a cue to broaden
    scope.
18. **Effort definition sensitivity.** *Mitigation:* sensitivity analysis on alternate thresholds/metrics;
    placebo test (combine slope should not predict early-game output).

## Phase 2 (Combine features) — added 2026-10-09

19. **[RESOLVED (D19, 2026-10-10)] Attempt-numbering unit differs by draft class → phantom imputed attempts (2025).** The locked Phase-2
    design standardises and imputes at `attempt_level = drill_name`, matching 2023/2024, where `attempt`
    restarts per `(player, drill_name)`. In the **2025** class `attempt` is instead a
    **per-`(player, drill_type)` block counter** (one player ran `SKILL_DRILLS_WR` once through with attempts
    `1..17` across 17 *different* `drill_name`s). Applying the `drill_name`-level gap rule to 2025 therefore
    **fabricates** lost attempts: imputed rows = 2023 144 + 2024 118 + 2025 **7874** = 8136, vs ≈338 under a
    year-aware unit; for the **DB** study population 1617 imputed rows (49+32+**1536**) vs ≈130 — ~92% phantom,
    inflating the primary exposure `prior_load_yd`. *Mitigation:* the pipeline implements the locked rule
    **exactly** and emits a runtime **WARNING** + diagnostics (`imputed_rows_{2023,2024,2025}`,
    `imputed_share_of_observed`, `attempt_numbering_restart_warning`) when imputed rows exceed
    `features.impute_warn_share` of observed. Raised as blocker **B5** (`notes/blockers.md`) with a recommended
    year-aware fix; **must be decided before Phase 3 / the full run**.
    *Resolution (D19, 2026-10-10).* The numbering unit is now **detected empirically per `(nfl_id, drill_type)`**
    (`features.attempt_level: empirical`; rule in `decisions.md` D19), so gap-imputation, `first_attempt` and
    `prior_load_yd` all use the detected unit and the phantom 2025 imputation is **eliminated**: 2025 imputed rows
    **7,874 → 148** (DB **1,536 → 28**); 2023 (144) / 2024 (118) unchanged; total 410 of 6,310 observed (share
    0.065 — warning now OFF). A per-row `attempt_unit` provenance column is emitted. **Residual threat:** only the
    detection heuristic's ambiguity for **single-`drill_name` blocks**, which is **unit-invariant** (grouping by
    `drill_name` == grouping by `drill_type`), so mis-detection there has **no effect** on imputation or load.
20. **Lost-attempt imputation is load-only and model-free.** Missing `(player, drill_name)` attempt numbers get
    the player's median observed `effort_cost_yd` for that drill (fallback: drill-name global median); no
    performance values are emitted and imputed rows are excluded from standardisation. *Mitigation:*
    `is_imputed`/`impute_source` flags; `prior_load_observed_yd`/`prior_load_observed_efforts` (observed-only)
    columns support the Phase-3 observed-only robustness refit; imputed counts reported. (Subject to threat #19.)
21. **Rest time spans imputed attempts (or is undefined at a session start).** `rest_s` = this attempt's start
    − end of the immediately preceding **observed** attempt; when an imputed attempt sits between them — or the
    attempt is session-first — `rest_spans_imputed = 1` (the true predecessor is lost/undefined). *Mitigation:*
    the flag lets Phase 3 exclude/split; `rest_s` is kept as a column and reported (distribution + Pearson
    correlation with `prior_load_yd`) under POLICY `rest_use = dropped_from_primary_kept_as_feature` — it is
    **used** (declared a Phase-3 robustness covariate), not silently ignored.
22. **Frame interpolation assumes linear motion over short gaps.** Gaps (`Δt > 1.5×0.1 s`) under 0.5 s are
    linearly interpolated onto the expected 0.1 s grid; gaps ≥ 0.5 s are **excluded** from distance/speed/accel
    (`flag_large_gap`); gaps inside the peak-speed/peak-accel window are flagged and left raw. *Mitigation:*
    flags + `n_gaps`/`max_gap_s`. The provided combine file has **no** frame gaps (every Δt = 0.1 s), so this
    path is exercised only by synthetic tests; re-check on any re-cut of the data.
23. **Broad-family standardisation within `drill_type`.** `perf_z` etc. are z-scored within
    `(drill_type, position)`, so heterogeneous skill sub-drills share a standardisation group (see threat #7).
    *Mitigation:* `C(drill)` in the Phase-3 LMM absorbs drill identity; group `(n, mean, sd)` are written to
    `standardization_params.csv`; groups with `n < min_std_group_n` or sd = 0 are set to NaN and flagged.
