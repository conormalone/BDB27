# The shallow break the Combine does not test

## 1. The football question

**The Combine does not test the turn that decides most defensive-back reps.** Across three seasons of tracking, 30–60° breaks are 13.26% of valid DB game turns; across the 2023–2025 Combine classes they are 2.21% of valid DB turns. That is a 6.01× shortfall in share [FULL]. The deficit reverses at the top: 120–150° plants are 42.32% of Combine DB turns against 19.51% of game turns, so the Combine supplies 0.46× of that band's game share [FULL].

The drill battery is built for the deep plant: the 3-cone and the shuttle ask a player to decelerate hard, sink and reverse. Games mostly ask for a single plant at 3–5 yd/s, a 30–60° change of direction and immediate re-acceleration.

The question is narrow. Does the Combine test the turn the job asks for? On the full 2023–2025 data, at the resolution of turn angle, no.

**Figure 1.** `outputs/figures/fig_the_hole.png` [FULL] — valid DB turns by band, Combine share against game share; the 30–60° band is the hole.

## 2. Data audit

**Two tracking files, one clock, and one missing field decide what can be claimed.** Both settings are about 10 Hz. The Combine file is 463,189 rows over 510 attendees from the 2023–2025 classes, carrying 424,784 player frame steps, every one of them exactly 0.1 s [FULL]. The game file is 22,654,290 rows covering 1,000 games, 72,615 plays, 496 tracked players and 10,080 player-games across 2023–2025 [FULL].

Three schema facts shape the method. `dir` is verified as degrees 0–360 with 0 at +y and clockwise rotation, so every kinematic quantity is derived from smoothed x/y and never from `dir`. Orientation (`o`) exists in the game file and is absent from the Combine; it is used only to report contamination, never inside the turn definition. `dis` has no documented unit and cannot serve as a distance check, so distance comes from x/y alone (TB1).

The 2,000-word ceiling is a working assumption, not a verified 2027 rule (`notes/rules.md` L1); the five-figure cap is binding from the task. The study population is Combine attendees who recorded NFL tracking: 510 attendees, 496 tracked, not NFL defensive backs [FULL]. That selection is stated, not estimated.

## 3. Kinematics and turn detection

**One detector runs on both settings, because the comparison is meaningless if the measurement changes between them.** Positions are smoothed with a Savitzky–Golay filter (9-frame window, third order) as primary, with five alternatives on every attempt so each turn carries a smoother-to-smoother range. Velocity, speed, heading, angular rate and both accelerations are differentiated once from smoothed x/y; provided `dir`, `s` and `a` are cross-checks only.

A turn is a heading change of at least 30° across 0.5 s, with speed above 2.5 yd/s at entry and at exit, segmented from the approach speed maximum to the exit speed maximum. Segmenting on speed maxima matters: at 10 Hz heading is undefined as speed passes through zero, and a fixed window records a hairpin as a shallow turn. Each turn yields entry, apex and exit speed, turn angle, duration, path radius, peak lateral acceleration, speed retention and regain time. Plants and arcs are separated by apex retention.

Reversal-class turns are measured as plants — apex, exit and regain — and never as v·ω, because a circle fitted to a hairpin reports a radius that makes the acceleration non-physical. Frame gaps are flagged and interpolated only outside the turn core.

The detector is validated against drill geometry rather than against itself. Cone positions are estimated from the density of detected turns on half the attempts and the detector is scored on the other half, with a point model for the 3-cone and a line model for the shuttle. That design is implemented; its numbers are `[PENDING]` a FULL-provenance run. The Combine file is complete, but the comparison runs inside a stage whose thresholds must carry FULL provenance, and no sample-stamped number may be quoted.

## 4. The envelope

**Turning capacity is estimated as an upper envelope, because an observed turn is not a maximal one.** A player's average cut is a choice; his ceiling is the quantity that could transfer between settings. The model is a random-effects quantile regression of peak lateral acceleration on apex speed at the 0.75 quantile, with one speed slope per position group and a random intercept per player, so a player with few usable turns is shrunk toward his group rather than reported as a noisy number.

Uncertainty is a player-weighted bootstrap — a bootstrap share, not a posterior probability. The friction-circle form, combining tangential and lateral acceleration under one limit, is fitted as a sensitivity at the same quantile and is never primary. Plant capacity is fitted separately as two surfaces over entry speed and turn angle — speed retention as an upper envelope, time to regain entry speed as a lower one.

The feasible region is a grid over entry speed and turn angle; a cell is inside when predicted speed retention clears 0.90. Cells whose full entry-speed interval lies inside the Combine's observed range are marked observed, the rest extrapolated, and the two are never pooled into one share.

The between-player variance component decides whether any player-level statement is possible. On full data that fit has not been run `[PENDING]`.

## 5. The demand map

**Demand is counted, not assumed.** Every valid DB turn in the three-season file is a demand observation: 58,664 turns from 115 DB players over 2,234 player-games [FULL]. At band resolution that distribution is the demand map, and it is a result.

| Band | Combine | Share | Game | Share | Ratio |
|---|---:|---:|---:|---:|---:|
| 30–60° | 26 | 2.21% | 7,777 | 13.26% | 6.01× |
| 60–90° | 237 | 20.10% | 13,077 | 22.29% | 1.11× |
| 90–120° | 159 | 13.49% | 13,016 | 22.19% | 1.65× |
| 120–150° | 499 | 42.32% | 11,444 | 19.51% | 0.46× |
| 150–180° | 128 | 10.86% | 8,137 | 13.87% | 1.28× |
| >180° | 130 | 11.03% | 5,213 | 8.89% | 0.81× |
| Total | 1,179 | | 58,664 | | |

Shares are of each setting's own valid turns [FULL]. The Combine is not short of big-angle volume; it is short of the shallow band at any speed and over-produces the deepest plant.

The shortfall holds in every group at 30–60° (Combine share against game share): DL 4.60% vs 9.76% (2.12×), WR 8.62% vs 14.45% (1.68×), TE 10.54% vs 14.23% (1.35×), OL 6.51% vs 9.34% (1.43×) [FULL].

**Figure 2.** `outputs/figures/fig_all_positions.png` [FULL] — the 30–60° share in each of the five groups, Combine against game.

The denominator is all valid DB game turns, not the snap-classified subset. Per-season valid DB turns are 8,016 (2023), 21,275 (2024) and 29,373 (2025) [FULL], but the 2023 file is about a third the size of 2025's for the same game count, so season rates are not comparable — read the pooled figure. That is 26.3 valid DB turns per player-game against 9.7 per Combine player [FULL, arithmetic]. The cell-level map is `[PENDING]`, and tracked players are the drafted group — a limit on demand as much as supply.

## 6. Coverage

**Turn Coverage answers a team's question, but not yet a scout's.** Coverage is the demand-weighted share of a player's feasible region: how much of what his position is asked to do in games falls inside what the Combine says he can do. Observed and extrapolated cells are reported separately and never merged into one headline number.

The Cut Card is the one-page version: a speed-by-angle heatmap, the player's region with uncertainty, the demand overlaid, and the largest gaps. It is an appendix artefact. It is not the headline because a per-player score needs identified between-player capacity, and that question is unanswered until the variance fit runs on full data `[PENDING]`. Until then coverage is a group-level statement, and a number attributed to an individual would be his group's value wearing his name.

## 7. Validation

**Three tests decide whether the story holds, and none has been run on full data.**

Transfer: the correlation between Combine capacity and game capacity, per position, as a fraction of the noise ceiling each setting's own reliability allows. A raw correlation is uninterpretable without that ceiling. It is repeated under every smoother.

Feasibility: the share of game turns outside the Combine-derived region, by position and speed range, observed and extrapolated separately. That game turns rarely exceed the Combine region is a test, not a premise. If a large share fall outside it at speeds the Combine actually observed, the region is too tight to be a scouting boundary.

Outcome value: for DL, whether coverage adds to a stopwatch-and-size baseline — 3-cone, shuttle, 40, height, weight — under player-grouped cross-validation. This stays an appendix check, not a confirmatory claim, because DL demand sits in a narrow band of entry speed.

Around those: definition sensitivity across smoother, window, minimum angle, bin width and retention tolerance; a permutation that shuffles player envelopes within position, where any signal should vanish; a placebo where height and weight alone should not reproduce the result. The smallest detectable effect is read off the power grid once the full-data player count is known.

A null is informative only if its interval excludes the smallest effect worth finding. The story is falsified if transfer sits at zero relative to the ceiling, or if game turns routinely fall outside the region at observed speeds.

## 8. Uses for scouts and coaches

**The result a team can use today is a statement about the test battery, not about a player.** A club can compare its drill set against the demand distribution and see which bands are tested repeatedly and which by accident: the shallow break is, at best, one incidental detection per player. That is an argument for changing a station, and it needs no envelope.

**Figure 3.** `outputs/figures/fig_drill_angle_scatter.png` [PLACEHOLDER_SAMPLE] — one dot per valid DB Combine turn at its measured angle, drills in median order, the 30–60° band marked; the detector's 30° floor censors each row's shallow end.

The tempo break is the concrete proposal `[PROPOSAL]`: a 45° break at speed to both sides, six reps, three each way, an 8–10 yd approach into a 3–5 yd/s break, a 5 yd re-acceleration through a gate, about 90 s, on the existing 3-cone footprint. It buys two things the battery lacks — repeated measurement of the same turn within a player, which is what per-player reliability requires, and coverage of a band the battery only samples by accident. It does not rank prospects, and a 45° break read against a 30–45° / 45–60° edge is a boundary sample, not a clean mid-band one.

**Figure 4.** `outputs/figures/fig_drill_schematic.png` [SCHEMATIC] — the tempo-break geometry and the cells it would fill; a drawing, carrying no measurement, and a design proposal awaiting sign-off.

A scout's interim use is restraint: silence on that turn is not evidence against it.

## 9. Limitations

**The chips see the turn and not the reason for it.** Contact is invisible: a defensive back who breaks slowly may be reacting to a body in his path, and nothing separates that from capacity.

Heading at 10 Hz is noisy, and the smoother is the most consequential choice in the pipeline; every conclusion is reported under each candidate.

With no orientation field in the Combine, plants cannot be separated from backpedals and shuffles there, though the game side can flag contamination. Combine entry speeds sit below game speeds, so part of the demand map is extrapolated by construction and is always reported separately.

The tracked players are the drafted group, so the demand map describes who reached the league. Pressure labels are vendor-modelled, `player_play` is a role-event table, `dis` remains unusable for distance, and per-season tracking coverage is uneven: the 2023 file is about a third the size of 2025's for the same game count.

## What is still missing before submission

- **Envelope (section 4).** `src/03_envelope.py --mode full`, once `notes/prereg.md` is frozen. If between-player variance estimates to zero, no player-level claim survives.
- **Cell-level demand map (section 5).** `src/04_demand_coverage.py --mode full`. If most demand mass is extrapolated, the map reads outside the Combine range and must say so.
- **Coverage and Cut Card (section 6).** The same stage's coverage artefacts. If variance stays at the boundary, the card is the group region and no individual score may be quoted.
- **Transfer, feasibility, outcome value (section 7).** `src/05_validate.py --mode full`. A transfer correlation near zero against the ceiling, or a large outside-region share at observed speeds, removes the claim that Combine capacity describes game capacity.
- **Detector geometry (section 3).** The FULL-provenance comparison run. Below the gate, the turn table is untrustworthy and every count above weakens.
- **Tempo break (section 8).** Conor's sign-off on `notes/drill-proposal.md`. Without it the drill stays a proposal.
- **Word cap.** The 2027 rules page, signed in (`notes/rules.md` L1). Below 2,000, sections 2 and 3 absorb the cut. As it stands the draft overruns the working assumption, and stays over it until the pending sections are cut or filled.
