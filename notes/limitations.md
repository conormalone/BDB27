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
   peak-definition and smoothing window. *Audit (g) evidence:* 40-yd-dash `peak_speed` test-retest ICC
   = 0.975 (reliable); however speed/timing splits measured from **movement onset at 10 Hz** are weak
   (`time_to_10yd` 0.216, `time_to_20yd` 0.403) and cutting-drill metrics are unreliable (≈0.1–0.3).
   Because an x/y-derived peak is a **max over frames**, it is inflated by per-frame position noise —
   relevant to any ceiling/effort ratio (motivates the pre-registered margin in `prereg.md` §6).
   - **Phase-2 envelope outliers (D29, 2026-10-12).** The 2-parameter envelope is unidentifiable on a few
   noisy/thin 40-yd-dash attempts (slow start / tracking noise): the wide `tau` bound (0.05–10 s, kept
   audit-consistent with prereg §4) lets the fitted ceiling extrapolate to ~18 yd/s. Affects **2/418 players**
   (flagged `env_vmax_implausible` in the per-player table: raw ceiling > 1.5x the player's mean peak).
   Reported with SEs and treated as a noisy reference, not ground truth; a physically-tighter bound was
   rejected because it flips the pre-registered VIF selection (D29).
   - **Residual collinearity among retained features (D29).** `peak_speed` and `speed_at_20yd` remain
   collinear (VIF ≈ 17 within the retained set) — one construct (raw speed) at two scales; flagged per
   prereg §3.4, not silently retained as independent evidence.

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
15. **Line-count ≠ record-count for `player_play.csv`.** 11,234 quoted `play_description` fields contain
    embedded newlines; `wc -l` (327,019) over-counts the true 314,197 records. *Mitigation:* never size or
    count this file by lines; use a real CSV parser / DuckDB (audit-corrected `schema.md`).

## Modelling & validation (Phases 4–5, D31 2026-10-12)

16. **The Phase-4 unit of analysis is the in-game effort, over *all* route-snap efforts** (not only the
    fixed-denominator top-k tail). The pre-registered top-k/`route_snap_count` tail (§2) is a Phase-3
    **descriptive** summary; conditioning the *regression* on it would discard every player on the synthetic
    fixture (< 100 route snaps), making the estimate unexercisable. Quantile regression at the τ ladder is
    what isolates the upper tail of effort speeds. *Mitigation:* eligibility is reported per player; the
    fallback chain and the τ-ladder read govern any tail claim.
17. **Player-level controls can be near-collinear with the ceiling on a small sample.** Numeric controls
    (`age, height, weight, bmi`) and the ceiling both vary mainly *between* players; with few players the
    controlled model is weakly identified and a slope can be unstable. *Mitigation:* redundant controls are
    **VIF-pruned** (`models.control_vif_max`), never the primary feature; the **player-clustered bootstrap**
    flags instability (`boot_se > |estimate|`) and the pre-declared fallback chain (`notes/prereg.md` §7)
    **nulls** the reported estimate rather than forcing a number. On the committed 8-player fixture the pooled
    fit is unstable and correctly reports a null; the real run (~144 WR/TE) is the powered analysis.
18. **Measurement error attenuates the slope.** The ceiling is estimated from thin Combine repeats (dash
    median 2 attempts, `env_vmax` ICC ≈ 0.64). *Mitigation:* the `icc_attenuation` correction (Phase 5) divides
    the slope by the feature reliability; the uncorrected and corrected slopes are both reported.
19. **Draft position is a categorical control** (`draft_position` = draft round tier `R1..R7`, with
    `undrafted` an explicit level); the models are fit **with and without** it, as pre-registered.
20. **Synthetic-fixture numbers are never findings.** The binding/null fixture satisfies the schema and
    recovers a *planted* ceiling (binding exceedance 0.109 vs planted 0.10), but at 8 players it is
    under-powered for a controlled quantile fit; its CV improvement (mean dominated by one exploding fold;
    robust **median** improvement small) is a fixture artefact. All fixture outputs are stamped
    `SYNTHETIC FIXTURE (not a finding)`.

## Data quality (D35, 2026-10-15)

21. **Game tracking-speed contamination in the top tail — impossible per-frame samples
    despiked, the effort retained.** `peak_speed` is a `max()` over per-frame speed (`step distance / dt`, recomputed
    from `x,y`); a single noisy or duplicated frame inflates the effort's peak far above any human
    speed. On the real data this produced impossible peaks concentrated in the fastest efforts: **351
    efforts had a `> 12.5 yd/s` peak**, and for **78 of the 101 eligible players** the single fastest
    effort was such a spike — i.e. exactly the top-k tail that drives the exceedance verdict (pooled
    exceedance 0.75%, top-5 tail ~56%, which crosses the 0.25 reframe threshold and flips the verdict).
    *Mitigation:* a config-driven frame-level **despike** at `game.speed_cap_yds` (12.5 yd/s = 25.6 mph,
    above every real in-game route speed; NGS in-game tops ~21–22 mph) applied in the shared kinematics
    SQL (game side only). A greedy forward scan **discards only the impossible speed sample** and
    re-measures the next frame from the last *retained* frame, then recomputes the peak and accel from
    the retained frames. The **effort is always kept** (a degenerate effort with <2 retained frames
    reports 0.0), so no effort is lost.
    *Honest residual:* removal is **per-frame and greedy** — a corrupted frame is skipped and its
    neighbour is re-measured from the last good frame, **not** dropped, so a genuinely fast neighbour
    keeps its (valid) speed and any surviving exceedance in the top tail is therefore **conservative**
    (the despike cannot inflate the exceedance rate). The bound is a physically-justified data-QC
    bound, **not** an analysis knob, and is never tuned to change a result (`notes/decisions.md` D35;
    `notes/prereg.md` stays FROZEN).

## Security / incentive exposure (D38, 2026-10-17)

22. **Incentive exposure is not measured intent.** The design operationalises *incentive exposure* — a
    slipping draft stock gives a stronger **reason** to invest in the speed mechanism. It cannot observe
    whether anyone actually invested more, tried harder, or intended to. *Mitigation:* the copy is fixed by
    D38: "incentive exposure" and "consistent with an incentive mechanism" are allowed; "tried harder",
    "intended to", "motivated to" and "gamed the Combine" are **not**. Any causal language is off-limits.
23. **The sort key is a pre-Combine construction, not a random assignment.** `moved_down` vs `high_secure`
    is defined from the EDP trend (1wk-pre − 2wk-pre) with the **D40 asymmetric threshold** — `moved_down`
    iff the player fell **3+ EDP spots** — or, absent a trend, from within-position tertiles of
    `log(EDP_pre)`. `high_secure` is **everyone else with a trend: stable *or* rising**, so it mixes
    flat and rising players and is **not** a "low EDP" group. In the trend stratum the sort key is
    movement only — the pre-Combine *level* is not used — so a 1-spot faller, a flat player and a riser
    can sit in `high_secure` at very different EDP levels, while a 3-spot faller is `moved_down`.
    Players are not randomised. *Mitigation:* the assignment is deterministic and pre-Combine only
    (never the game outcome), a +5 threshold is reported as a declared sensitivity, the pooled model
    carries a **stratum fixed effect**, and trend-only / level-only panels are reported separately.
    It is a contrast, not a causal effect.
24. **Level-fallback is a declared second rule.** No 2-week snapshot ⇒ the group comes from a tertile of the
    pre-Combine EDP level, so a minority of the board is classified by a different rule than the rest
    (164/783 players have a 1-week-pre snapshot but no 2-week baseline; 127 have neither pre snapshot).
    *Mitigation:* every output labels the stratum; cutoffs and counts are reported; the middle tertile is
    `unclassified` and excluded from the contrast (retained in the continuous model so no information is
    thrown away). A player near a tertile boundary can flip with a small EDP change.
25. **Selection on being drafted (and on playing).** Game tracking exists only for players who reached the
    NFL and appeared in games. At-risk players are less likely to appear, and appearance can correlate with
    the outcome. *Mitigation:* the appearance model + IPW refit (E) with a truncation grid; the estimate is
    reported with and without weights, and the truncation sensitivity shows how much the answer depends on
    the tails. IPW corrects only for selection on **observables** (pre-Combine EDP, position, size, Combine
    speed); selection on unobservables is not identified. H1 stays exploratory unless E supports it.
26. **Post-Combine information is an outcome, never an exposure.** Post-Combine EDP and the pre→post change
    reflect the Combine itself. *Mitigation:* a runtime assertion refuses them in the assignment path; the
    only place they appear is the labelled exploratory Analysis-A appendix.
27. **Measurement error in the Combine feature attenuates the group and speed coefficients.** `env_vmax`
    has test–retest ICC ≈ 0.64 (the least reliable retained feature); `peak_speed`/`speed_at_20yd` ≈ 0.97.
    *Mitigation:* both naive and ICC-attenuation-corrected estimates are reported (FWL + moment-matrix
    correction with a player-clustered bootstrap SE), never a silent replacement. The correction is an
    approximation and depends on the pre-declared ICC; with attenuation the **group** coefficient is only
    partially identified.
28. **Multiple comparisons.** B has 3 scopes × 4 specs; C 3 scopes; F 5 tests; G 3 scopes × 4 τ. *Mitigation:*
    the headline is the single pre-declared WR primary coefficient; everything else is a labelled panel. No
    multiplicity-adjusted claim is made for the exploratory panels; treat their p-values as descriptive.
29. **Small groups in the replication and in the split strata.** TE and the level-fallback stratum can fall
    below `security_models.sort.min_group_n` (15). *Mitigation:* thin populations are reported with a note
    rather than fitted silently; heterogeneous effects are not claimed from a thin group.
30. **Ceiling reliability limits the gap definition.** The gap uses `env_vmax`, whose error is largest for
    thin 40-yd-dash repeats. *Mitigation:* utilisation tails (G) and the exceedance rate are reported with
    Wilson CIs; the ceiling is treated as a noisy reference, not ground truth.
31. **Skipper analysis (D) is exploratory and thin.** Skipping is voluntary and rare; the size+drills
    prediction cannot separate injury skips from strategic ones. *Mitigation:* reported as exploratory, null
    with a reason below `min_n`, and never used as evidence for the incentive mechanism.
32. **Synthetic fixture numbers are never findings.** The planted/null/selection regimes prove the plumbing
    and the sign conventions only. The fixture's declared ICC file is a pipeline input, not a property of
    the fixture, so the attenuation correction is expected to overshoot there.
33. **Opportunity/role differences are a *level* threat, not a movement one.** The "fringe guy gets fewer
    max-effort routes" story applies when groups differ in *level*. The primary contrast sorts on
    pre-Combine **movement** (D40: a fall of 3+ EDP spots vs stable-or-rising), so both groups can be
    starter-calibre — a player slipping 3 spots keeps his reps, especially with an elite 40.
    *Mitigation:* snap share, route family and depth are controls; the trend-only panel is free of the
    level confound; the level-fallback stratum is where the confound can bite, which is why it is a
    separate, labelled panel and never the headline. Note that `high_secure` mixes stable and rising
    players, so it is a "not falling 3+" group rather than a like-for-like "no news" control.
34. **Combine peaking inflates the ceiling (the denominator).** The gap is game speed relative to a
    Combine-measured ceiling, so a player who invests in the Combine itself — tapers, nails the start —
    raises his own ceiling and *widens* the gap with no change in game ability. A negative `group_coef` is
    therefore consistent with two stories (pressure hurts game output, or pressure produced a bigger
    Combine number). *Mitigation:* the **peaking markers** (best-minus-mean attempt gap, attempt count)
    enter model F; a positive `group_coef` with quiet peaking markers is the cleaner incentive evidence,
    and the writeup must not read a negative sign as one mechanism.

35. **E (selection) can be unidentified, and is then reported as a labelled null.** The appearance logit
    can separate or nearly separate — at-risk players are systematically less likely to appear — which
    makes the coefficients and odds ratios explode or become non-finite. *Mitigation (D43):* the odds
    ratios are computed through a clipping helper (exponent ±700, overflow reported as `inf` / `>1e300`),
    and `|coef| > 20`, `se > 50`, non-convergence or a non-finite coefficient turn E into a labelled null
    with the reason (the IPW refit is reported unavailable rather than forced). E is non-fatal by design:
    B/C/D/F/G are written regardless. A null E means the selection correction is **not** available for that
    run, so the unweighted estimate stands and H1's selection caveat (limitation 25) applies in full.

36. **The dose-response panel is exploratory and post hoc.** It was specified *after* the binary cut had been
    examined, and it looks at the same exposure continuously (linear, spline, explicit knot at 5, bins,
    segmented) under seven capital treatments. *Mitigation:* it cannot change `b_primary.csv`, the
    pre-registered test or any frozen value; every row, table and figure is labelled exploratory / post hoc;
    the panel is a **supplementary** figure, not one of the 5 writeup figures; and no multiplicity-adjusted
    claim is made across its cells (treat the p-values as descriptive, as in item 28).

37. **Dose and draft capital are collinear by construction.** `edp_trend` moves with the EDP level, and the
    level is the draft slot. A "dose effect" can therefore be draft position in disguise — later picks are
    worse players, and worse players may realise less of their ceiling for reasons that have nothing to do
    with pre-Combine movement. *Mitigation:* every specification is fitted unadjusted, with `overall_pick`,
    with the round band, with pick plus the pre-Combine level, and inside three declared round bands; the
    undrafted are kept as their own level (and excluded, with the reason stated, where a raw pick is
    required). `SUMMARY.md` states in one sentence, generated from the numbers, whether the slope survives
    — "the dose slope is draft position in disguise" is an acceptable and reportable outcome. What this
    cannot fix: a within-band fit uses a much smaller sample (and is marked thin below the floors), and
    conditioning on realised draft slot is conditioning on a **post-Combine** outcome, so the adjusted
    estimates are a confound probe, not a causal adjustment (the same caveat as D48/§10).

38. **The dose bins are small, and the extremes are where the noise is.** `<=0 / 1-2 / 3-4 / 5-9 / 10+` puts
    most players in two bins and can leave 1-2 and 3-4 below `dose.min_players` (15) in a position scope.
    *Mitigation:* a bin below the floor is reported with its counts and descriptive means and **no
    contrast** (never a forced number); when any fixed bin is thin a five-bin **quantile** variant is fitted
    as well and labelled with its actual `[lo, hi]` ranges. Bins are closed on the upper edge and form a
    total partition, so a fractional `edp_trend` cannot fall through a gap. The binned means in the figure
    carry player-clustered 95% CIs and their `n_players` labels, so a thin bin is visible rather than
    persuasive.

39. **The sign wording in `TASK.md` H1 and the PI's stated expectation disagree, and this panel does not
    resolve it.** H1 says a slipping player (`moved_down`) realises a *higher* share of his ceiling (a
    *positive* coefficient on movement); the PI's stated expectation is the opposite — a slipping player
    realises *less* (a *negative* coefficient per EDP spot fallen). *Mitigation:* the pre-registered binary
    test keeps its own (H1) wording and is not reinterpreted; the dose panel states its own convention
    explicitly and identically in every row (`sign_convention`), at the top of `SUMMARY.md`, and on the
    figure, and reports the sign it actually estimates rather than the sign the hypothesis wanted. The
    discrepancy is recorded in D39/DV-7 (fixture), D50 and DV-9. A reader must not compare the sign of a
    `dose_fits.csv` coefficient with the sign of `b_primary.csv`'s `group_coef` without reading both
    conventions.
