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
