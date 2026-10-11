# Limitations & threats to validity — BDB27 (ceiling-binding task)

Initial register (Phase 0, 2026-10-11). Every threat carries a mitigation; this file grows with each phase
and feeds the writeup's Limitations section. **Resets the prior (combine-fatigue) register**, which is
archived at git tag `archive/combine-fatigue-v1`.

## Scope & claim limits

1. **Claims are limited to players who reached the NFL.** In-game tracking (and therefore the whole
   game side) exists only for prospects who played NFL games, so everything is conditional on
   Combine **AND** on-field. *Mitigation:* state the selection plainly; report matched Combine↔NFL counts
   (audit e); never generalise to all combine participants.
2. **Never claim Combine athleticism predicts NFL success.** That framing is saturated (combine tests
   explain ~2.6% of year-1 game performance; Cook et al. 2020) and is explicitly out of scope. The
   question is **mechanistic**: is the physical **ceiling** a bound on in-game max-effort output, and how
   close do players get to it. *Mitigation:* keep every headline about **binding / closeness**, not success.

## Measurement

3. **Ceiling is estimated from Combine tracking, not measured directly.** The envelope fit
   `v(t)=vmax·(1−exp(−t/tau))` on straight-line drills is a **model**, with per-player fit error and
   shrinkage. *Mitigation:* hierarchical shrinkage; report per-player SE; treat the ceiling as a
   **noisy reference**, not ground truth.
4. **Different frames and units (Combine vs. game).** Combine `x,y` are in a local frame; game `x,y`
   are the NFL 120×53.3 yd field. Ceilings (Combine) and in-game efforts (game) must be compared in a
   **common unit** before any exceedance/ratio. *Mitigation:* recompute all kinematics from `x,y`;
   never reuse field assumptions; fix a documented unit conversion in config.
5. **The provided `dis` column is unreliable** (under-counts path 9–65%). *Mitigation:* recompute
   distance from `x,y`; keep `dis` only as an advisory cross-check.
6. **Sampling / digitisation error in tail estimates.** Max-effort quantities live in the **upper tail**
   of a noisy 10 Hz signal; peaks are sensitive to frame alignment. *Mitigation:* top-k over a **fixed
   route-snap count** (not "best-ever"); measurement-error correction in Phase 5; sensitivity to the
   peak-definition and smoothing window.

## Design & identification

7. **Exceedance may be common → the ceiling may not be a hard bound.** If in-game efforts routinely beat
   the Combine ceiling, "bound" is the wrong frame. *Mitigation:* the **reframe rule is pre-declared** in
   `notes/prereg.md` §6 (re-purpose the ceiling as a **scaling reference** — "how close to ceiling" —
   rather than a bound); decided **before** results, not after.
8. **Deep vs. breaking routes are heterogeneous and scheme-dependent.** Route type, depth and
   coverage vary by system/opponent. *Mitigation:* condition on the provided route/coverage fields
   (`pass_length`, `route_ran`, `team_coverage_man_zone/_type`, `coverage_assignment*`); report deep and
   breaking separately; treat as **descriptive stratification**, not causal.
9. **Participation / opt-outs are non-random.** Combine drill participation is voluntary and correlated
   with draft stock (`forty` 16.9% null; cone/shuttle ~60%+ null). *Mitigation:* prefer the lowest-opt-out
   drills; report selection explicitly; sensitivity to missing-feature handling.
10. **Draft position is a control, not a confound to hide.** The `draft_overall_pick`/`draft_round` NULLs
    are the **structural "undrafted"** category (its own level), not missing data. *Mitigation:* model
    **with and without** draft position (Phase 4); "undrafted" encoded as an explicit level.

## Engineering / data scale

11. **The game-tracking sample is non-representative** (2²⁰-row prefix). It cannot exercise game-side
    matching, route-snap counts, or Phase-3 scale. *Mitigation:* game-side work is full-run only
    (`PENDING FULL RUN`); sample outputs are stamped `UNVALIDATED SAMPLE OUTPUT` and never interpreted.
12. **Thresholds must be `FULL` in full runs** and frozen before the models. *Mitigation:* runtime
    assert (`00_thresholds`); never derive from the sample.
13. **Scale/RAM.** Full game tracking is ~2.9 GB across three seasons. *Mitigation:* Parquet + DuckDB
    streaming, process **per game/player**, write small summary tables; see `notes/scale.md`.
14. **Matched-N / route-snap sufficiency.** If matched receivers or route snaps fall below the config
    minima, the study cannot proceed. *Mitigation:* go/no-go gate (audit) → **blocker** if failed; a
    synthetic schema-compliant set with a **planted binding ceiling** exercises the pipeline if the real
    matched players are insufficient.
