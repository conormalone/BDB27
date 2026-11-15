# What we did not establish

The consequence test would ask whether a defensive back's 30–60° break retention predicts the separation of the receiver he covers, match fixed at the break frame before the fit. The primary obstacle is that the exposure is not a stable player trait: ICC(1) is 0.0034 across 7,777 turns from 113 players, and restricting to the drill's own entry speeds (3–5 yd/s, 3,385 turns) while adjusting for entry speed and angle lifts it only to 0.0141. At 0.0141 a player mean is ≈30% reliable, so any correlation with the Combine is attenuated to roughly half its true size, and a weak one cannot be read as the battery missing information. Attribution is the second obstacle: 61.3% of band turns carry no labelled opponent because `player_play` holds only the 497 prospects (115 defensive backs), capping coverage at 38.7%. Composition compounds it: of the 7,777 turns, 5,197 are on pass plays and 2,580 on runs, and runs over-produce the band (15.5% of run turns against 12.4% of pass turns), so a coverage-only validation would describe a subset. A usable test needs ρ ≥ 0.072, i.e. about 163 turns per player against the ~30 available (≈18,000 drill-speed turns against 3,385), or, for the effect actually observed, ≈590 player clusters.

## Provenance

Word count of the paragraph above: **205** (body only, excluding this heading and the provenance block).

Every reliability number is written by `scripts/band_reliability.py` from
`src/bdb27/band_reliability.py` to
`outputs/band_reliability/reliability.json`, with sha256 digests of both inputs
and both outputs in `outputs/band_reliability/manifest.json`. The ICC(1) itself
is **imported** from `bdb27.fallback_validation.icc1` (D203), not reimplemented,
so the two artefacts report the identical estimator.

| Number in paragraph | Value(s) | Artefact |
|---|---|---|
| Band | 30–60° | `TASK.md`; `notes/fallback-validation-spec.md` §1.2 |
| Band turns | 7,777 | `outputs/band_reliability/reliability.json` → `sample.band_turns`; `outputs/fallback_validation/reliability.json` → `sample.n_turns` |
| Invalid band rows dropped | 1 (7,778 − 7,777) | `outputs/band_reliability/reliability.json` → `sample.band_rows_before_valid`, `sample.band_rows_invalid_dropped` |
| Band ICC(1) | 0.0034 | `outputs/band_reliability/reliability.json` → `scenarios[name=all_band].icc` (0.003382); D203 |
| Band players | 113 | `scenarios[name=all_band].players` |
| Drill-speed window | 3–5 yd/s | `TASK.md` §3; `notes/fallback-validation-spec.md` §3 |
| Drill-speed turns | 3,385 | `scenarios[name=entry_3_5].turns` |
| Drill-speed ICC(1) | 0.0093 | `scenarios[name=entry_3_5].icc` (0.009321) |
| Drill-speed k̄ | 30.0 | `scenarios[name=entry_3_5].kbar` (29.956) |
| Drill-speed + pass turns | 2,232 | `scenarios[name=entry_3_5_pass].turns` |
| Drill-speed + pass players | 108 | `scenarios[name=entry_3_5_pass].players` |
| Drill-speed + pass ICC(1) | 0.0085 | `scenarios[name=entry_3_5_pass].icc` (0.008460) |
| Pass-only turns | 5,197 | `scenarios[name=pass_only].turns`; `outputs/turn_context/summary.json` → `play_type.pass` |
| Pass-only players | 111 | `scenarios[name=pass_only].players` |
| Pass-only k̄ | 46.8 | `scenarios[name=pass_only].kbar` (46.820) |
| Pass-only ICC(1) | 0.0046 | `scenarios[name=pass_only].icc` (0.004559) |
| Adjustment R², whole band | 0.406 | `adjustment.all_band.r_squared` (0.405784) |
| Adjusted ICC, whole band | 0.0037 | `adjustment.all_band.icc` (0.003685) |
| Adjustment R², 3–5 yd/s | 0.146 | `adjustment.entry_3_5.r_squared` (0.146425) |
| Adjusted ICC, 3–5 yd/s | 0.0141 | `adjustment.entry_3_5.icc` (0.014055) |
| Reliability of a player mean at k=30, ρ=0.0141 | ≈0.30 | `derived.reliability_of_player_mean[0].R` (0.2995) |
| Attenuation ceiling √R | ≈0.55 ("roughly half") | `derived.attenuation_ceiling_sqrt_R.at_adjusted_rho` (0.5473) |
| ρ needed for R=0.70 at k=30 | 0.072 | `derived.rho_needed_for_R_0_70_at_k30` (0.072165) |
| Turns per player for R=0.70 at ρ=0.0141 | ≈163 | `derived.k_needed_for_R_0_70_at_adjusted_rho` (163.7 at the artefact's ρ; 163.2 at the rounded 0.0141) |
| Drill-speed turns needed | ≈18,000 | `derived.turns_needed_for_R_0_70.turns_needed` (18,497 at 163.7 × 113; one significant figure) |
| Turns held | 3,385 | `scenarios[name=entry_3_5].turns` |
| Player clusters for the observed effect | ≈590 | `derived.clusters_for_observed_effect.clusters_required` (590.7) |
| Turns with no labelled opponent | 4,768 | `notes/outcome-link-feasibility.md` (coverage table, both `[FULL]` blocks) |
| Share with none | 61.3% | 4,768 / 7,777 = 61.309% (`notes/outcome-link-feasibility.md`) |
| Cohort | 497 prospects; 115 defensive backs | `notes/outcome-link-feasibility.md` (cohort); `TASK.md` §2 |
| Turns with ≥1 candidate | 3,009 | `notes/outcome-link-feasibility.md` (coverage table) |
| Share with ≥1 | 38.7% | 3,009 / 7,777 = 38.691% |
| Pass turns in band | 5,197 | `outputs/turn_context/summary.json` → `play_type.pass`; D205 |
| Run turns in band | 2,580 | `outputs/turn_context/summary.json` → `play_type.run`; D205 |
| Band share of pass turns | 12.4% | `outputs/turn_context/demand_scope.csv` → `pass.band_share_of_scope` (0.1237) |
| Band share of run turns | 15.5% | `outputs/turn_context/demand_scope.csv` → `run.band_share_of_scope` (0.1549) |

### Derived quantities — formula and inputs

**(A) Scenario ICCs.** Frame: `setting == 'game'`, `position_group == 'DB'`,
`30 <= abs_turn_angle < 60`, `valid == True`; then `play_type` is joined from
`outputs/turn_context/turn_context.parquet` on
`(game_id, play_id, nfl_id, frame_start)`, deduplicated on that key and joined
`many_to_one` (three turn keys repeat because two segmentations of the same turn
share the key; both rows carry the same play). `ICC(1)` is
`bdb27.fallback_validation.icc1(apex_retention, nfl_id)`, one-way random effects,
unbalanced ANOVA moments.

Reproduction of the brief's table: all four scenario counts, all k̄ values and
three of four ICCs reproduce exactly. **Pass-only is the exception:** the brief
quotes 0.0047; the file gives 0.004559 → **0.0046**. The ordinary (non-pass)
3–5 yd/s ICC reproduces at 0.009321 → 0.0093, and the adjusted values reproduce
at 0.003685 and 0.014055. The 7,777 depends on the `valid` filter: the parquet
holds 7,778 band rows, one with `valid == False`.

**(B) Reliability of a player mean and its attenuation.**
`R = k·ρ / (1 + (k−1)·ρ)`. At `k = 30`, `ρ = 0.014055`:
`30 × 0.014055 = 0.42164`; `1 + 29 × 0.014055 = 1.40759`; `R = 0.2995`.
At `ρ = 0.009321`: `R = 0.2201`. A correlation with a perfectly measured
criterion observed through this exposure is `|r_obs| = √R · |r_true|`, so
`√0.2995 = 0.5473` — roughly half.

**(C) What a usable test needs.** For `R = 0.70` at `k = 30`:
`ρ = R / (k − (k−1)R) = 0.70 / (30 − 29 × 0.70) = 0.70 / 9.70 = 0.07216`.
Turns per player at `ρ = 0.014055`:
`k = R(1−ρ) / (ρ(1−R)) = 0.70 × 0.98595 / (0.014055 × 0.30) = 163.7`
(163.2 using the rounded 0.0141; the paragraph says ≈163). Across 113 players
that is 18,497 turns — ≈18,000 at one significant figure — against 3,385 held.

**(D) Clusters for the effect actually observed.** From D204's geometric
estimate, `b = −0.38` yd per unit retention, 95% CI `[−1.08, +0.33]`, 84
clusters. `SE = (0.33 + 1.08) / (2 × 1.959964) = 0.3597`;
`SE_needed = 0.38 / (1.959964 + 0.841621) = 0.1356`;
`n_req = 84 × (0.3597 / 0.1356)² = 590.7` → **≈590**.

**Caveat.** `ρ` is a plug-in point estimate with its own interval (D203's
bootstrap CI for the band ICC is `[−0.001, 0.008]`), so the reliability,
attenuation and sample-size targets above are order-of-magnitude statements, not
design guarantees. They are printed for scale, not as a threshold any test here
was required to clear.
