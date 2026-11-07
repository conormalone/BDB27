# Phase 3 — the turn envelope: lateral capacity and plant capacity (stage 03)

Status: **implemented** (arc capacity, plant capacity, feasible region, model-free
cross-check, per-player table). Every number on this page comes from a stage run or from
the tests; sample-mode values are stamped `PLACEHOLDER_SAMPLE` and are **not results**.

Binding spec: `TASK.md` @ `52015cd`, section **3. Turn envelope (capacity)**, plus the
engineering rules (checkpoint + `--force`, wall time and peak RAM to `outputs/run_log.csv`,
downcast dtypes, sample output stamped `PLACEHOLDER_SAMPLE`, full mode refuses unless
`outputs/thresholds.json` is `FULL`).

## What the stage does

Input: `outputs/turns/turns.parquet` only (written by stage 02). Output:
`outputs/envelope/`. One code path for `--mode sample|full`, one config.

1. **Capacity observations.** Demand = `peak_lateral_accel_yd_s2` (v·ω at the apex), the
   conditioning variable = apex speed. Kept when the turn is `valid`, both are finite,
   apex speed ≥ `envelope.apex_speed_floor` and demand > 0; demand above
   `envelope.winsor_lateral_yd_s2` is winsorised, not dropped. Every exclusion is counted
   by reason in the manifest.
2. **Arc capacity** `a_max(v) = a0 + a1·(v − v_ref)` — a **random-effects quantile
   regression** at `envelope.quantile`: fixed intercept and speed slope per position
   group, one Gaussian random intercept per player (partial pooling within position),
   **moment-matched between-player variance** fixed before the fit (D148), SEs and p05/p95
   from a **player-weighted bootstrap**.
   The friction-circle form `sqrt(a_tan² + a_lat²) ≤ a_max(v)` is fitted as a **form
   sensitivity at the same quantile** (`form=friction_circle_upper`) and is never the
   primary definition (D143).
3. **Plant capacity** — two surfaces in (entry speed, |turn angle|), linear with an
   interaction: **speed retention** (upper envelope, quantile `envelope.quantile`) and
   **time to regain entry speed** (lower envelope, `envelope.regain_quantile`, fitted as an
   upper quantile of −time). Same random-effects structure.
4. **Model-free cross-check.** Per speed bin, the upper quantiles (0.75, 0.90) and the top-k
   mean of the demand, at position and player level. Never used to fit, never used to
   choose anything.
5. **Feasible region.** The `bins.entry_speed × bins.turn_angle` grid; a cell is inside when
   predicted **speed retention** at the cell midpoint ≥ `feasibility.retention_tolerance`
   (0.90). `inside_share` is the share of player-weighted bootstrap replicates that clear
   the tolerance — **a bootstrap share, not a posterior probability**. Cells whose entry-
   speed interval lies fully inside the valid Combine entry-speed range are `observed`; the
   rest are `extrapolated`. They are always stored with the flag and summarised separately.
6. **Per-player table** with uncertainty: `capacity_at_{2,4,6,8}`, slope, retention and
   regain coefficients, shrinkage weight, `reliable`/`fully_pooled`/`single_setting` flags,
   counts and the model-free references. No "best turn" is ever reported.
7. **Separate settings.** `combine`, `game` and `combined` are fitted independently (the
   transfer comparison in phase 5 needs the separate ones; the card uses the combined one).

## Estimator and uncertainty — stated precisely

The fitted quantity is the conditional **upper quantile** of lateral demand, not a mean: a
turn is not necessarily maximal, so the mean understates capacity. The random intercept
shrinks each player toward his position group, so a player with few usable turns is
reported as pooled rather than as a noisy individual number.

**The between-player variance is moment-matched, not estimated by EM (D148).** For every
player `j` in a (setting, position_group, measure) cell:

```
q_j = the model-free 0.75 upper quantile of that player's usable turns
      (np.quantile linear; capacity = winsorised peak lateral acceleration,
       retention = winsorised speed retention, regain = −time to regain entry speed)
s_j = bootstrap SE of q_j, resampling that player's own turns with replacement
      (tau2_quantile_bootstrap_reps, default 200; deterministic seed per
       setting/measure/player)
tau2 = max(0, Var(q_j) − mean(s_j^2))        Var with ddof = 1
```

A player with fewer than `tau2_min_turns_per_player` (2) usable turns is **excluded and
counted** (`n_players_excluded`); he is never given a fake `s_j = 0`. The yardstick `q_j` is
**not speed-adjusted** — it is a raw per-player upper quantile, so part of its spread is the
entry-speed mix inside that player's turns, not pure capacity. This is a stated limitation,
not a corrected quantity. The fitted model then supplies the speed curve; `tau2` only sets
the amount of between-player pooling.

`tau2` has three statuses, reported everywhere: **`INTERIOR`** (`Var(q_j) > mean(s_j^2)`),
**`AT_BOUNDARY`** (`Var(q_j) <= mean(s_j^2)`, so `tau2` is exactly 0), and
**`INSUFFICIENT_PLAYERS`** (fewer than two contributing players; `tau2 = 0`). The floor is 0
and nothing else: there is no prior, no EM loop, no positive floor. `AT_BOUNDARY` is a
finding — a group where the data cannot distinguish players from each other's measurement
error — never an error; it does not make the stage exit non-zero (D149).

An SE and p05/p95 for `tau2` come from a **player-cluster bootstrap**
(`tau2_bootstrap_reps`, default 200): resample players with replacement, recompute
`max(0, Var(q*) − mean(s*²))` each time.

**The fitted model uses the moment `tau2` and runs one IRLS pass.** `fit_re_quantile` takes
`tau2` as an input, with no EM cycle; `_solve_re_irls` is unchanged (warm-start hooks,
windowed stability certificate, objective secondary stop) but is called once. The
sensitivities — the friction-circle form and the 0.90 quantile — are fitted at the same
`tau2` as the primary capacity component, so those comparisons change one thing only.

**Held-out predictive-loss cross-check (D150).** For each production component a grid of
candidate `tau2` is scored by player-grouped K-fold CV: the grid always contains 0, the
moment estimate, and `tau2_grid_sd_fractions × tau2_grid_scale_sd²` (default 1.5² ×
[0, 0.25, 0.5, 0.75, 1, 1.5, 2]). Folds are `min(tau2_heldout_folds, n_players)` assigned by
sorted player id modulo K. Each candidate is fitted on the training players at that fixed
`tau2` and scored on the held-out players with `u = 0` (fixed effects only) by the mean
check loss at the fit quantile. The argmin is reported as
`tau2_heldout_optimal`; ties go to the smallest `tau2`. **The production value is always the
moment estimate.** The cross-check never selects, never averages, and is never swapped in
when it looks better. The two routes are said to agree when
`|sqrt(tau2_moment) − sqrt(tau2_heldout)| <= tau2_agree_tol_sd_frac × grid scale SD`, or
both are 0, or the held-out optimum lies inside the moment p05/p95. A disagreement is
reported as both values, `routes_agree=false`, a `variance_route_disagreement` player flag in
that group, and both numbers in the stage headline. **Why a new-player CV can prefer
`tau2 = 0` even when players really differ:** a held-out player's random effect is unknown,
so the best prediction for him is the **marginal** quantile; a large `tau2` lets the training
random effects absorb between-player spread and does not help a player the model has never
seen. The cross-check therefore answers "what predicts a new player", not "how much do
players differ" — which is exactly why it is a check, not the selector.

**Uncertainty is a player-weighted bootstrap at the fixed production `tau2`**: 200
replicates; each draws one Exp(1) weight per player (applied to all of that player's turns)
and, where the smoother min/max columns exist, redraws each turn's demand/retention uniformly
inside its smoother range. SEs are the replicate SD; p05/p95 are replicate percentiles.

**This is not a posterior.** No sampler, no prior on the capacity curve, no posterior draws.
The stage therefore reports **bootstrap SEs** where `TASK.md` says "posterior SEs"; the
manifest says `"uncertainty": "player_weighted_bootstrap"`, `"posterior": false` and
`"uncertainty_kind": "bootstrap_share_not_posterior"`, and no artefact or label calls a
bootstrap share a probability. Upgrading to a genuine hierarchical Bayesian fit is a
prereg decision to be taken before a full run (see below); it is not faked here.

Convergence is reported honestly: the **primary** stop is a windowed stability certificate on
the estimate, not on the objective alone. A solve certifies when the fitted values `Xβ + u`
move by less than `envelope.irls_stability_tol` (1e-3) in relative L2 norm over the last
`envelope.irls_stability_window` (10) iterations, scaled by `max(||y||₂, 1)`; the relative
objective-gain test (`envelope.irls_obj_tol`, 1e-7) remains a secondary stop that also
certifies. The coefficient delta is only a non-certifying safety stop, set at 1e-9 so it
cannot preempt the windowed certificate on a cold start. `irls_max_iter` is 120; the
measured worst case on the sample is 67 iterations. `converged` now means the single solve
certified — there is no `em_converged` and `stop_reason=em_not_settled` can no longer occur.
Any fit that still fails is named per group in the stage stdout and in
`manifest.convergence.explanations`, with `stop_reason`, iterations, `n_obs`, `n_players` and
the final objective gain.

## Files written

| file | content |
|---|---|
| `envelope_players.parquet` | one row per (setting, player): capacity, slope, retention, regain, SEs, shrinkage, flags, counts, model-free refs |
| `envelope_fit.csv` | one row per (setting, group, model, form, quantile): coefficients, SEs, random-effect SD, fixed `tau2`, `tau2_se/p05/p95`, `tau2_status`, `tau2_route`, `tau2_heldout_optimal`, `routes_agree`, `tau2_source_measure`, iterations, `converged`, `stop_reason`, `objective`, `obj_gain_last`, `stability_last`, capacity distribution |
| `variance_components.csv` | one row per (setting, position_group, measure): `n_players`, `n_players_excluded`, `tau2`, `tau2_se`, `tau2_p05/p95`, `tau2_status`, `tau2_route`, `tau2_heldout_optimal`, `heldout_loss`, `routes_agree`, `grid` (JSON), `loss_curve` (JSON), `note` |
| `envelope_modelfree.csv` | per-bin upper quantiles and top-k means, position and player level |
| `feasible_region.parquet` | one row per (setting, player, cell): predicted retention, p05/p95, `inside_share`, `inside_point`, `inside_share_ge_min`, `observed`, turn counts |
| `feasible_region_summary.csv` | the same collapsed per group and **separately for observed and extrapolated cells** |
| `manifest.json` | provenance, config fingerprint, estimator block, `variance` block (method, formula, floor, route, cross-check, reps, grid, components), convergence explanations, counts, settings, Combine entry-speed range, open decisions, warnings |

Parquet carries `provenance`, `label`, `config_sha` as file metadata and no timestamp, so
the same inputs and seed give byte-identical tables.

## Configuration choices (each is a decision, logged in `notes/decisions.md`)

| key | value | why |
|---|---|---|
| `envelope.quantile` | 0.75 | count-driven: median usable turns per player is 10 (Combine) / 32 (game); 0.90 is ~1 turn per player and unstable (D140) |
| `envelope.quantile_sensitivity` | `[0.75, 0.90]` | 0.90 is fitted and reported beside 0.75, never primary |
| `envelope.capacity_form` | `linear_centred` | linear in speed, centred at the reference; other values are refused, not ignored (D143) |
| `envelope.capacity_reference_speed` | 4.0 yd/s | near the Combine turn-apex median, so the intercept is a reached speed |
| `envelope.apex_speed_floor` | 1.5 yd/s | below it v·ω is dominated by discrete heading steps (D141) |
| `envelope.winsor_lateral_yd_s2` | 35.0 | above ~3.5 g the 10 Hz derivative dominates any real cut |
| `envelope.random_effects` | `intercept` | partial pooling of the level only; a random slope needs counts this sample does not have |
| `envelope.tau2_method` | `moment_matched` | the only supported route; any other value is refused (D148) |
| `envelope.tau2_quantile_bootstrap_reps` | 200 | resamples of a player's own turns for the yardstick SE `s_j` |
| `envelope.tau2_min_turns_per_player` | 2 | minimum turns for a player to enter the moment; below it he is excluded and counted, never given a fake `s_j = 0` |
| `envelope.tau2_bootstrap_reps` | 200 | player-cluster bootstrap for the `tau2` SE and p05/p95 |
| `envelope.tau2_grid_scale_sd` | 1.5 | held-out grid scale, squared before use |
| `envelope.tau2_grid_sd_fractions` | `[0, 0.25, 0.5, 0.75, 1, 1.5, 2]` | candidate `tau2` values; 0 and the moment estimate are always included |
| `envelope.tau2_heldout_folds` | 5 | player-grouped CV folds = `min(5, n_players)`, assigned by sorted player id modulo K |
| `envelope.tau2_heldout_irls_max_iter` | 40 | cross-check refits need the same estimand, not the last decimal |
| `envelope.tau2_agree_tol_sd_frac` | 0.25 | agreement band between the moment SD and the held-out optimum |
| `envelope.irls_delta_tol` | 1e-9 | non-certifying safety stop, low enough never to preempt the certificate |
| `envelope.irls_max_iter` | 120 | measured worst case is 67 iterations for a cold-start solve |
| `envelope.bootstrap_reps` | 200 | 200 replicates give a usable p05/p95; the count is reported |
| `envelope.modelfree_*` | 0.90 / top-3 / 1.5–8 yd/s bins | the model-free cross-check required by the spec |
| `feasibility.retention_tolerance` | 0.90 | the spec's example tolerance; **frozen by prereg** |
| `feasibility.observed_rule` | `cell_inside_combine_range` | a cell is observed only if its whole entry-speed interval is inside the valid Combine range (D142) |
| `feasibility.cell_statistic` | `midpoint` | one prediction per cell; the tolerance is evaluated at the midpoint (D146) |
| bins | `bins.entry_speed`, `bins.turn_angle` | the same bin edges the demand map (stage 04) must use |

## Deliberately absent (and why)

* **No posterior.** See above. Bootstrap only, labelled as such everywhere.
* **No censored/Tobit likelihood.** The spec allows a censored-style likelihood, a
  high-quantile likelihood or top-k; a high-quantile random-effects model plus a model-free
  top-k cross-check was chosen because it is estimable with 10–32 turns per player and does
  not need a censoring model for "how hard the player *could* have turned".
* **No best-turn statistic anywhere.** Shrunken estimates with SEs only.
* **No left/right asymmetry.** Capacity is a function of |turn angle|; direction is not
  estimated (no spec requirement, and counts per side are too thin in the Combine drills).
* **No random slopes, no player-level angle curvature.** Intercept-only pooling.
* **No outcome information of any kind.** The primary capacity measure is provisional:
  prereg chooses it by audit reliability and counts, never by outcomes.
* **Regain time is estimated on uncensored turns only.** `regain_exhausted` turns (16.9% of
  plant turns in the Combine sample, 41.4% in games) are dropped, not right-censored, so
  the lower envelope of regain time is conditional on regaining speed (D145, limitation).
* **No coverage, no demand weighting, no transfer.** Those are stages 04–05.

## What prereg must freeze (not written here; `notes/prereg.md` stays absent)

This section names the choices a future prereg must fix verbatim. **No prereg document is
written by this stage** — full mode refuses while `notes/prereg.md` is absent, by design.

1. **The `tau2` route.** The production between-player variance is
   `moment_matched`: `tau2 = max(0, Var(q_j) − mean(s_j^2))`, with `q_j` the model-free
   0.75 per-player yardstick and `s_j` its player-turn bootstrap SE. No EM, no prior, no
   iteration.
2. **The held-out cross-check's role.** `tau2_heldout_optimal` from player-grouped K-fold
   held-out check loss is **reported, never used to select** and never averaged with the
   moment estimate. It is a pre-declared check on the variance route.
3. **The floor at 0 and `AT_BOUNDARY`.** `tau2` is never floored above 0; `AT_BOUNDARY`
   (`Var(q_j) <= mean(s_j^2)`) is a first-class reported status and is never an error or a
   stage failure. `INSUFFICIENT_PLAYERS` (<2 contributors) is reported separately.
4. **The disagreement-reporting rule.** If the moment and held-out routes disagree by more
   than `tau2_agree_tol_sd_frac × tau2_grid_scale_sd` in SD units, both values are reported,
   `routes_agree=false`, and every player in the group carries a
   `variance_route_disagreement` flag. The production value does not change.
5. **The primary-measure rule.** The primary capacity measure (arc capacity vs plant
   capacity) is chosen by audit **reliability and counts, never by outcomes**, with the
   reliability numbers that decide it recorded.
6. The envelope form `a_max(v) = a0 + a1(v − v_ref)` and whether the friction-circle
   total-demand form is primary or sensitivity.
7. The quantile (0.75 vs 0.90) and the speed-retention **tolerance** (currently 0.90).
8. The bin edges for the feasible region, shared with the demand map.
9. Whether a genuine hierarchical **Bayesian** fit with posterior SEs is required (TASK.md
   says "posterior SEs"; this stage reports bootstrap SEs and says so).
10. The observed/extrapolated rule and the minimum turns per player for a `reliable` flag.

## Sample run — PLACEHOLDER_SAMPLE, not results

Filled from the stage's own output on the host sample; see the run-log row in
`outputs/run_log.csv` for wall time and peak RSS.

**Timing.** 143.5 s wall, 206.3 MB peak RSS (the pre-change run was 588.3 s; removing the
EM cycle and fitting at a fixed `tau2` is why the cost fell). 8,425 usable capacity
turns, 1,170 player-setting rows, 3 settings fitted.

**Fits.** 85/85 point fits converged (44 by the windowed stability certificate, 41 by the
objective stop); 0 non-converged, 0 `em_not_settled` (the EM stop reason no longer exists).

**Variance route.** 51 components (17 per measure across 17 setting × group cells), of which
43 are `AT_BOUNDARY`, 8 `INTERIOR`, 0 `INSUFFICIENT_PLAYERS`; 18 components have
`routes_agree=false`. The table below is **PLACEHOLDER_SAMPLE** and must not be interpreted.

| setting | group | measure | tau2 (PLACEHOLDER) | status | held-out optimum |
|---|---|---|---:|---|---:|
| combine | DB | arc_capacity | 0.000 | AT_BOUNDARY | 1.688 |
| combine | DL | arc_capacity | 0.000 | AT_BOUNDARY | 0.000 |
| combine | OL | arc_capacity | 0.000 | AT_BOUNDARY | 3.375 |
| combine | TE | arc_capacity | 0.000 | AT_BOUNDARY | 1.688 |
| combine | WR | arc_capacity | 0.000 | AT_BOUNDARY | 0.000 |
| game | TE | arc_capacity | 3.443 | INTERIOR | 4.500 |
| game | DB | plant_retention | 0.008 | INTERIOR | 1.125 |
| game | TE | plant_retention | 0.015 | INTERIOR | 0.000 |
| combined | OTHER | arc_capacity | 0.196 | INTERIOR | 0.563 |

Full per-component values are in `outputs/envelope/variance_components.csv`; none of them is
a result until the full run and the frozen prereg.
