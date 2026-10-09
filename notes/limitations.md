# Limitations & threats to validity — BDB27

Initial register (Phase 0/1). Every threat carries a mitigation; this file grows with each phase and feeds
the writeup's Limitations section.

## Data & design

1. **Drill order is confounded with drill type.** Within a combine session the protocol is fixed
   (`FORTY_YARD_DASH` first, then positional skill drills, then 3-cone/shuttle); only the relative order of
   the two agility drills varies (D5). *Mitigation:* never use raw order as the fatigue exposure; identify
   from **within-drill, within-player repeated attempts** plus load/rest timing.
2. **Combine day = position group.** Days are position-staggered, so "later day" is confounded with
   position/ability. *Mitigation:* restrict to one position group (DB); no between-day exposure.
3. **Selection bias (opt-outs are non-random).** Participation is voluntary and correlated with draft
   stock. Results-level opt-out: `forty` 16.9%, `three_cone` 64.7%, `short_shuttle` 60.6%. *Mitigation:*
   use 40-yd (lowest opt-out); report selection explicitly; never claim population representativeness.
4. **Only players who reached NFL games have game data** (Phase 5 selection limitation). *Mitigation:*
   state it; descriptively compare matched vs unmatched prospects (audit e, full run).
5. **Lost attempts.** 32% of player-drill groups have an attempt-numbering gap (DB 40-yd: 27.5%).
   A gap = lost data. *Mitigation:* impute **load only** with the drill-level median, flag imputed rows,
   exclude them from performance observations, and refit with observed-only load (Phase 2/3 robustness).
6. **Small n.** DB 40-yd: 93 players with ≥2 attempts → few attempts per player (mostly 2). *Mitigation:*
   within-player late-minus-early baseline; shrunken slopes; pre-registered reliability stop (<0.2 → null).

## Measurement

7. **Performance measurement error / slope reliability.** Per-player fatigue slopes are noisy.
   *Mitigation:* model-based reliability (slope variance / (slope var + mean squared SE)); ≥3 observed
   attempts required for the Phase 5 link; error-in-both-slopes correction (regression calibration /
   bivariate hierarchical) in Phase 5.
8. **Provided `dis` column unreliable** (under-counts path 9–65%, D4). *Mitigation:* recompute distance
   from `x,y`; sanity-check against official splits.
9. **Coordinate frame is the local Combine frame, not the NFL field.** *Mitigation:* never reuse NFL
   field assumptions; use per-drill extents (`notes/schema.md`, audit f).
10. **Time zone unverified** (naive timestamps). *Mitigation:* avoid time-of-day exposures until confirmed
    (D11).
11. **Frame gaps / jitter.** *Mitigation:* interpolate gaps <0.5 s only; flag attempts with gaps inside the
    peak-speed/accel window; flag speeds >12 yd/s and duplicate attempts (Phase 2).

## In-game side (Phase 4/5)

12. **Sample is non-representative** (2²⁰-row prefix). *Mitigation:* never tune/report from sample;
    game-side work is full-run only (PENDING FULL RUN).
13. **Threshold provenance.** Must be "FULL" in full runs; frozen before Phase 5. *Mitigation:* runtime
    assert; `00_thresholds.py`.
14. **Effort definition sensitivity.** *Mitigation:* sensitivity analysis on alternate thresholds/metrics;
    placebo test (combine slope should not predict early-game output).
