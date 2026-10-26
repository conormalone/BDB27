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

## DB-decay task (2026-10-25 pivot, D51) — threats

Scope of this block: the active task only. Items 1–39 above belong to the archived ceiling-binding/security
work; where they still apply they are cross-referenced rather than repeated. Every item below says either how
it is mitigated or that it is **not mitigable and is reported as a limitation**.

40. **Selection is "reached the NFL and played", not "was drafted".** The claim limit in `TASK.md` says
    "only draftees have game tracking", but the data says otherwise: of the 118 Combine DBs, **24 have no
    draft slot** (structural "undrafted" level) and **all 24 appear in `player_play`**, while **3 drafted DBs
    do not**. So the population is everyone who reached a roster *and* took a defensive snap — a survivorship
    filter of unknown strength, correlated with ability, health and depth-chart luck.
    *Mitigation:* state the population in those words (never "draftees only"); report 118 / 115 / 94 / 24
    counts and the frame-level matched count (`c_matched_levels.csv`, `PENDING FULL RUN` for tracking);
    never generalise to combine participants or to drafted-but-never-played DBs. *Residual:* selection on
    unobservables (who sticks) is not identified — **not mitigable**.

41. **The ceiling is a single feature with non-trivial measurement error.** `a0` fails the ICC screen, so the
    ceiling is `vmax` alone, whose test-retest ICC is **0.686 with a 95% CI of [0.548, 0.788]**
    (`g_feature_icc.csv`) — the interval's lower bound is **below** the 0.60 retention threshold. The ceiling
    is therefore a noisy denominator sitting under every effort fraction.
    *Mitigation:* hierarchical EB shrinkage with a carried SE; the SE is propagated into every downstream
    estimate; the measurement-error-corrected (attenuation) estimate is reported **alongside** the naive one
    (prereg §2.3, §5); `EF > 1` is reported, never clipped, so the ceiling's calibration is visible.
    *Residual:* attenuation correction is an approximation and depends on the estimated ICC; with a single
    feature there is no second indicator to triangulate it — **partially identified, reported as such**.

42. **`a0` is unusable, so the acceleration-moderated hypothesis is untested.** ICC 0.288 (CI [0.071, 0.480])
    means a player's fitted initial acceleration is not a stable property of the player at this sample size.
    *Consequence:* the load × ceiling interaction is tested on `vmax` only; the question "does a player with a
    better *start* decay less?" is **not answered**. *Mitigation:* report `a0`'s ICC as the reason (D53);
    do not run a half-tested interaction or reinterpret a `vmax` interaction as an acceleration effect.
    **Not mitigable with this data** — reported as a narrowing of the question.

43. **The provided game acceleration is invalid, and derived acceleration is a smoothing artefact.**
    Game `a` is not `ds/dt` (r ≈ 0.003, bias +1.73 yd/s², 88% of frames outside a 5% tolerance), so
    acceleration is derived from smoothed speed. But a Savitzky–Golay fit at 10 Hz necessarily **suppresses
    very short bursts**: an acceleration spike lasting < ~0.2 s cannot survive the filter, which is precisely
    why the acceleration episode branch requires a 0.2 s sustain. Short explosive movements may therefore be
    missed as episodes.
    *Mitigation:* both branches (speed and acceleration) must fire independently, so a short burst that also
    raises speed is still captured; the smoothing window is fixed in advance and sensitivity-tested (a
    narrower window is a declared sensitivity, not a re-spec); truncated units are flagged and excluded from
    peak-derived quantities (`notes/schema.md` §8). *Residual:* **not fully mitigable** — the 10 Hz sampling
    is the floor.

44. **No opponent co-location, so matchup demand cannot be conditioned on.** `player_play` holds only the 497
    combine-class players (2.73 rows/play) and the tracking sample has 1.16 players/play (0 plays with 22
    tracked). A DB covering a WR1 on a vertical is not doing the same job as one covering a check-down, and
    this cannot be measured here (D56).
    *Mitigation:* the **within-player, within-game contrast** (early vs late, matched on play type, alignment,
    margin band) differences out the player's role; alignment, man/zone and play type are controls;
    play-type/alignment strata are reported. *Residual:* a **mid-game change in role** (injury elsewhere,
    garbage time) is not differenced out, and coverage quality itself is unobserved — **not mitigable**;
    reported as the main identification caveat.

45. **Role and pacing: snaps are not effort.** A DB with many snaps may be a starter playing soft coverage, or
    a sub playing special packages; snap count is a role proxy, not a work measure. Load built from episodes
    avoids counting idle snaps, but the two are correlated by construction.
    *Mitigation:* the primary load is the **prior high-intensity episode count** (not snaps) and the secondary
    is **`x`/`y` path length**; `snaps_in_drive` and `seconds_since_rest` are separate controls; the
    all-efforts model is a declared secondary so a max-effort-only result can be compared against the full
    set; the within-player contrast removes between-player role differences. *Residual:* pacing *within* a
    player (saving himself for a long drive) is exactly the behaviour the design is trying to measure and
    cannot be separated from "role" — **reported as an interpretive limitation**, not a bias to be corrected.

46. **Substitution and survivorship within games.** **21.0%** of DB-games have the DB playing Q1 but not Q4
    (`e_attrition.csv`: ALL 0.2095; CB 0.2227, S 0.1957; 75.8% play all four quarters). Missing late snaps are
    not random: injury, blowout rotation and benchings all correlate with the outcome.
    *Mitigation:* report results restricted to DBs who played all four quarters, plus a version weighting by
    the probability of remaining in the game; the Q1-not-Q4 share is reported per position group; per-quarter
    tracking coverage is `PENDING FULL RUN` (P21) so the tracking-side version of the same check is still
    open. *Residual:* IPW corrects only for selection on observables — **not mitigable** for unobserved
    injury/benching.

47. **Game-script effects.** Score margin, pace and play-calling change effort in ways that are not "decay":
    a DB in a blowout may coast (or press), and a team trailing by 3 runs a different defence than one
    trailing by 24.
    *Mitigation:* pre-registered garbage-time exclusion (Q4 and |margin| ≥ 21; any play |margin| ≥ 28),
    penalty-nullified plays excluded, score-margin **band** as a control (0–7/8–14/15–21/22+), quarter
    control, and a declared sensitivity excluding the final two minutes of each half. *Residual:* game script
    inside the non-garbage window still varies — **partially mitigable**; the relaxed-rule sensitivity
    quantifies how much the exclusion matters.

48. **19 of 118 DBs have no 40-yard-dash attempt and therefore no ceiling.** They cannot enter the primary
    analysis at all (`a_dbs_missing_40.csv` lists the ids). Combine participation is voluntary and
    non-random, so this is a selection threat, not a data-cleaning detail.
    *Mitigation:* the excluded ids are published with the reason; included vs excluded DBs are compared on
    position group, class year and (where present) draft slot so the direction of the selection is visible;
    no imputed ceiling is ever used. *Residual:* **not mitigable** — the excluded players are unobservable on
    the exposure of interest, and if fast players are more likely to run the 40 the retained sample is
    selected on the ceiling itself.

49. **Lateral alignment cannot be derived from tracking.** The sample has no ball/lateral reference
    (`n_lateral_reference_available = 0`), so outside-corner vs slot-corner comes from
    `player_play.lined_up_position` only. Agreement between the `lined_up_position` family and the
    tracking-derived family is **92.3%** for the safety/non-safety split (n = 2,842) but the **3-class lateral
    variant** is only **80.9%** on n = 947 (`c_alignment_agreement.csv`) — i.e. the axis that matters most
    for corner alignment is the least verifiable.
    *Mitigation:* alignment enters as a 4-level control with an explicit `other`/`missing` level; the
    alignment-subgroup sensitivity is pre-declared; the 92.3% / 80.9% agreement numbers are reported next to
    the control rather than hidden. *Residual:* misclassification is likely non-differential with respect to
    load (it is a pre-snap label), so it should attenuate toward zero rather than create a spurious decay —
    **argued, not proven**; reported as a limitation.

50. **Per-player slopes are noisy and mostly unreportable.** With 20–60 episodes per DB, an individual slope's
    SE can exceed its estimate.
    *Mitigation:* the model-based reliability threshold `var(slope)/(var(slope)+mean SE²) ≥ 0.50` is fixed in
    advance (prereg §11), EB shrinkage is applied, and players below threshold are reported as a **count**,
    with no individual numbers. Explicitly **not** an odd/even split-half. *Residual:* the surviving slopes
    are a selected (better-covered, higher-snap) subset — reported as such.

51. **Short follow-up for the 2025 class.** The 2025 class has ~1 NFL season of games while 2023 has ~3, so
    games/DB and efforts/DB are class-dependent (audit c: `db_seasons_median` = 2 for CB/S). A class effect
    in the model is therefore partly a **follow-up-length** effect.
    *Mitigation:* class year is reported per DB; per-class descriptive panels; the primary model does not
    claim a trend over classes; the minimum-effort floors are applied **per player**, so a thin 2025 DB is
    excluded rather than contributing noise. *Residual:* **not mitigable** — the data cannot be extended.

52. **Thresholds could be tuned against the sample.** Every effort cut-off and the garbage-time rule are
    placeholders in sample mode; a number chosen because it "looks right" on the sample would be a
    sample-tuned design presented as pre-registered.
    *Mitigation:* `00_thresholds` stamps provenance `PLACEHOLDER_SAMPLE` or `FULL`, stage 03 **asserts**
    `FULL` in full mode, and the prereg is frozen before the first full run (it is `DRAFT — NOT FROZEN`
    today, and the full run is gated on that). Sample outputs are stamped `UNVALIDATED SAMPLE OUTPUT`, the
    gate cannot return GO in sample mode (D57), and no threshold may be edited after seeing full-data
    results without a dated deviation entry. *Residual:* **process control only** — it prevents tuning, it
    does not detect tuning that was never written down; the audit trail is the mitigation.
