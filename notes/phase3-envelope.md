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
   group, one Gaussian random intercept per player (partial pooling within position), EM
   for the between-player variance, SEs and p05/p95 from a **player-weighted bootstrap**.
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

**Uncertainty is a player-weighted bootstrap**: 200 replicates; each draws one Exp(1) weight
per player (applied to all of that player's turns) and, where the smoother min/max columns
exist, redraws each turn's demand/retention uniformly inside its smoother range. SEs are the
replicate SD; p05/p95 are replicate percentiles.

**This is not a posterior.** No sampler, no prior on the capacity curve, no posterior draws.
The stage therefore reports **bootstrap SEs** where `TASK.md` says "posterior SEs"; the
manifest says `"uncertainty": "player_weighted_bootstrap"`, `"posterior": false` and
`"uncertainty_kind": "bootstrap_share_not_posterior"`, and no artefact or label calls a
bootstrap share a probability. Upgrading to a genuine hierarchical Bayesian fit is a
prereg decision to be taken before a full run (see below); it is not faked here.

Convergence is reported honestly: the **primary** stop is a windowed stability certificate on
the estimate, not on the objective alone. Each inner solve is warm-started from the previous
EM cycle, and it certifies when the fitted values `Xβ + u` move by less than
`envelope.irls_stability_tol` (1e-3) in relative L2 norm over the last
`envelope.irls_stability_window` (10) iterations, scaled by `max(||y||₂, 1)`; the relative
objective-gain test (`envelope.irls_obj_tol`, still 1e-7) remains a secondary stop that also
certifies. A coefficient delta is only a non-certifying safety stop. The measured certificate
fires in 41–89 iterations per group on the sample, within 0.15%–0.52% (L2) of an
800-iteration reference (D147). `converged` is `inner_converged and em_converged`, so an EM
cycle that never settles is reported as `stop_reason=em_not_settled` and is not counted as a
converged fit.

## Files written

| file | content |
|---|---|
| `envelope_players.parquet` | one row per (setting, player): capacity, slope, retention, regain, SEs, shrinkage, flags, counts, model-free refs |
| `envelope_fit.csv` | one row per (setting, group, model, form, quantile): coefficients, SEs, random-effect SD, `prior_used`, iterations, `converged`, `stop_reason`, `objective`, `obj_gain_last`, `stability_last`, `em_converged`, capacity distribution |
| `envelope_modelfree.csv` | per-bin upper quantiles and top-k means, position and player level |
| `feasible_region.parquet` | one row per (setting, player, cell): predicted retention, p05/p95, `inside_share`, `inside_point`, `inside_share_ge_min`, `observed`, turn counts |
| `feasible_region_summary.csv` | the same collapsed per group and **separately for observed and extrapolated cells** |
| `manifest.json` | provenance, config fingerprint, estimator block, counts, settings, Combine entry-speed range, open decisions, warnings |

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
| `envelope.between_player_sd_prior` | 1.5 yd/s² | only used when the EM moment is non-finite or floored; the row says `prior_used` |
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

## What `notes/prereg.md` must fix before a full run (not written here)

1. The envelope form: `a_max(v) = a0 + a1(v − v_ref)` and whether the friction-circle
   total-demand form is primary or sensitivity.
2. The **primary capacity measure** (arc capacity vs plant capacity) — chosen by audit
   reliability and counts, **never by outcomes**, with the reliability numbers that decide it.
3. The quantile (0.75 vs 0.90) and the speed-retention **tolerance** (currently 0.90).
4. The bin edges for the feasible region, shared with the demand map.
5. Whether a genuine hierarchical **Bayesian** fit with posterior SEs is required (TASK.md
   says "posterior SEs"; this stage reports bootstrap SEs and says so).
6. The observed/extrapolated rule and the minimum turns per player for a `reliable` flag.

## Sample run — PLACEHOLDER_SAMPLE, not results

Filled from the stage's own output on the host sample; see the run-log row in
`outputs/run_log.csv` for wall time and peak RSS.

TBD (written after the recovery run).
