# Dose-response stage — implementation contract (D50)

> **Status:** binding implementation spec for `src/10_dose_response.py`, this cycle only.
> **Label:** exploratory / post hoc. It cannot change the pre-registered binary result.
> **Owner:** PM. Implemented by the Coder role; signed off by Reviewer + Validator.

## 0. Why

The pre-registered primary exposure is binary — `moved_down` iff `edp_trend >= 3.0`, with a declared
`panel_threshold_T5` at 5.0. Both cuts are arbitrary and the T3→T5 change relabels only a few players
while moving p a lot: the threshold is not doing real work. This stage models **dose continuously** and
reports linear, spline, binned and segmented (kink) specifications on the same outcome.

**This does not replace the binary result.** `b_primary.csv` and the pre-registered test stay exactly as
they are. This panel is labelled exploratory / post hoc everywhere.

## 1. Reuse, never reimplement

Import `07_security_models.py` by path (`importlib.util.spec_from_file_location`, as
`08_route_robustness.py` does) and reuse, unchanged:

* `load_exposures`, `load_envelope`, `load_player_summary`, `load_topk`, `load_snap_counts`,
  `load_covariates`, `load_reliability`, `scoped_snap_counts`, `load_attempt_markers` (if needed);
* `build_effort_rows` (with `keep_unclassified=True`) — this is the realisation/ceiling construction;
* `design`, `ols`, `_num`, `_complete`, `_inputs`, `fixture_dir`, `_route_family`;
* `fwl_attenuation` as the template for the corrected linear slope (see §7).

The outcome is **exactly** block B's: row-level `y = log(peak_speed)` on the top-k efforts, with
`log_env_vmax` as the ceiling regressor, the same controls, and `statsmodels` OLS with
`cov_type="cluster"`, `groups = nfl_id`.

**Do not change** `src/07_security_models.py`, `src/security_features.py`, `TASK.md`, `notes/prereg.md`.

## 2. Population and exposure

* Rows: `build_effort_rows(topk, assign, envelope, cov, psum, cfg, keep_unclassified=True, **row_kwargs)`
  filtered to `edp_trend is not None` (finite). That is the **trend stratum** by construction: the
  level-fallback players have no `edp_trend` and must not enter a dose model.
* `d = edp_trend` = 1-week-pre EDP − 2-weeks-pre EDP. **Positive = the EDP number rose = the stock fell**
  (worse / less secure). Negative = the player rose up the board.
* Season scopes (panel axis): **`all_seasons`** primary and **`rookie_only`** sensitivity, both fitted in
  one invocation (loop panels internally, two `build_effort_rows` calls). Season-type scope
  (`game.scope.primary` = REG+POST vs `sensitivity`) stays on the existing `--scope` CLI knob.
* Position scopes: **WR** primary, **TE** replication, **pooled**. All three per (season_scope).
* Thin discipline: a cell is **fitted** only if `n_obs >= dose.min_obs` (40) **and**
  `n_players >= dose.min_players` (15). Otherwise the row is emitted with `thin=True`, null coefficients
  and a `note` reason. A thin **bin** still reports its counts and descriptive means, but no contrast.

## 3. Specification family (per season_scope × scope × feature × capital_treatment)

`y = b0 + b_c*ceiling + f(d) + controls`

* controls = `security_models.primary.controls` minus `stratum` (constant inside the trend stratum —
  state this in the note; do **not** add a stratum FE): `route_family` (categorical), `pass_length`,
  `snap_share`, `position` (categorical, only meaningful in pooled), `height`, `weight`,
  `years_since_combine`.
* ceiling = `log_env_vmax` (primary) / `log_peak_speed` / `log_speed_at_20yd` (robustness).
* clusters = `nfl_id` (distinct players).

### 3.1 `dose_linear`
`f(d) = b_d * d`. Emit `term="slope"`, coef/se/p/ci. Also emit the ICC-attenuation-corrected slope in
`coef_corrected/se_corrected/p_corrected` (§7).

### 3.2 `dose_spline` (3 df)
Natural cubic spline in **Harrell restricted-cubic** parameterisation (`rcs`), basis columns `k-1` for
`k` knots; `df = k - 1`. Knots at `dose.knots_3df` quantiles (default `[0.05, 0.35, 0.65, 0.95]`,
4 knots → 3 df). Emit `term="overall"` with `test_stat` = Wald chi-square over all basis columns,
`p`, `df`. Emit the curve (§5).
Fallbacks (each logged in `note`, never silent): fewer than 5 distinct `d` values → linear (df 1);
after knot de-duplication fewer than 3 knots → linear; rank-deficient basis → drop the last column
until full rank, reporting the reduced df.

### 3.3 `dose_spline_knot5`
Same basis with an **explicit knot at `dose.knot5` = 5.0**: knots =
`unique(sort([q10, 5.0, q90]))` (default `knot5_percentiles: [0.10, 0.90]`), df = len(knots) − 1
(normally 2). If 5.0 lies outside the observed range, fall back to `[q10, q50, q90]` and say so in the
note. Emit `term="overall"` + `df`. **This is a shape question ("is the curve steeper past 5?"), not a
rival model** — say so in SUMMARY.

### 3.4 `dose_bins`
Fixed bins on `d` (integer EDP spots), from `dose.bins`:
`<=0`, `1-2`, `3-4`, `5-9`, `10+`. Reference = `<=0`.
Fit `y ~ ceiling + C(bin, ref=<=0) + controls`; per-bin contrast vs the reference.
Emit one `dose_fits` row per bin, `term="bin:<label>"`, plus the detailed `dose_bins.csv` (§4).
If any fixed bin has `< min_players` distinct players, **also** fit the quantile-binned variant
(`dose.quantile_bins`, 5 bins) and emit it with `binning="quantile"`; label each bin with its actual
`[lo, hi]` range. Fixed bins are always emitted.

### 3.5 `dose_kink`
`f(d) = b1*d + b2*d*I(d >= 5)`. Emit three rows:
`term="slope_below_5"` = b1; `term="slope_above_5"` = b1 + b2 with
`se = sqrt(V11 + V22 + 2*V12)`; `term="slope_change_at_5"` = b2.

## 4. Capital confound (mandatory)

Every dose model is fitted under each `capital_treatment` in `dose.capital.treatments`:

| treatment | meaning |
|---|---|
| `unadjusted` | dose + existing controls only |
| `pick_linear` | + `overall_pick` (raw pick number). Undrafted rows (no pick) are **excluded** for this treatment; say so in the note. |
| `round_band` | + `draft_position` as a categorical (`R1..R7`, `undrafted`) |
| `pick_plus_level` | + `overall_pick` + `log_edp_pre` (level is a separate confound from capital; undrafted excluded) |
| `within_R1` | slope estimated **only** inside round 1 |
| `within_R2_R3` | only inside rounds 2–3 |
| `within_R4plus` | only inside rounds 4–7 **and** undrafted (label the band `R4plus_or_undrafted`) |

`overall_pick` source: real → `players.parquet`, column `draft_overall_pick`; synthetic → the fixture
`covariates.csv` column `overall_pick`. Round band: `cov[...]["draft_position"]` (`R1..R7` /
`undrafted`). Never invent a pick for an undrafted player.

**If the slope dies when capital is controlled, that is the finding.** SUMMARY must state it plainly in
one sentence: "the dose slope is draft position in disguise."

## 5. `dose_curve.csv` (plotting grid)

For `spec in {dose_linear, dose_spline, dose_spline_knot5, dose_kink}`, `feature = env_vmax`,
`capital_treatment in {unadjusted, pick_linear, round_band}`, on a grid of `dose.grid_n` (61) points
spanning `[min(d), max(d)]` of the fitted rows:

* `fitted_log(x) = mean_i(X_i β) + g(x) − mean_i g(x_i)` (dose part only is varied; everything else is
  held at its sample-mean fitted value, so the curve is centred on the adjusted mean).
* `realisation_pct(x) = 100 * exp(fitted_log(x) − mean(log_ceiling))`.
* CI: linear contrast `g(x) − mean_i g(x_i)` with `res.cov_params()`; `±1.96*se` on the log scale, then
  exponentiated. Emit `realisation_ci_lo/hi`.

## 6. `dose_bins.csv`

Per season_scope × scope × binning × capital_treatment × bin:
`n_players` (distinct), `n_obs`, `mean_gap` (= mean of `y − log_ceiling`), `se_gap` (player-clustered),
`mean_realisation_pct = 100*exp(mean_gap)`, `realisation_ci_lo/hi` (from `mean_gap ± 1.96*se_gap`,
exponentiated), `ref_contrast`/`ref_se`/`ref_ci_lo`/`ref_ci_hi` (vs `<=0`, from the fitted model),
`thin`, `note`. A thin bin carries counts and means but null contrast.

## 7. Reliability (ICC) treatment

* Report `reliability_icc` + source on every fit (from `load_reliability`).
* **Linear spec only:** mirror `fwl_attenuation` with a 2-regressor moment matrix on
  `(x = ceiling, d = dose)` where `Var(x)` is scaled by the ICC λ — i.e. `S* = [[λ·Var(x), Cov(x,d)],
  [Cov(x,d), Var(d)]]`, `β* = S*⁻¹ s`, player-clustered bootstrap for the corrected SE/p (reuse
  `security_models.primary.attenuation_reps` and `attenuation_seed_offset`). Emit
  `coef_corrected/se_corrected/p_corrected`; if λ is missing/outside (0,1] emit nulls + a note.
* Non-linear specs: naive only, with the ICC reported and a note saying the correction is applied to
  the linear spec (the like-for-like replacement for the binary contrast).

## 8. Feature robustness

`dose_linear` and `dose_spline` are repeated on `peak_speed` and `speed_at_20yd` as well as
`env_vmax`. Other specs run on `env_vmax` only. SUMMARY must state explicitly whether the dose slope
**inherits the fragility** of the existing effect: `env_vmax` ICC ≈ 0.64 vs ≈ 0.97 for the other two.
The statement must be generated from the fitted numbers (e.g. "the slope appears with all three
features" / "the slope appears only with env_vmax"), plus the ICC values.

## 9. `dose_fits.csv` columns (stable order, `provenance` first)

`provenance, season_scope, scope, spec, feature, capital_treatment, term, coef, se, p, ci_lo, ci_hi,
test_stat, df, n_obs, n_players, thin, note, reliability_icc, coef_corrected, se_corrected,
p_corrected, sign_convention`

`sign_convention` is a constant plain-words string in every row:
> "coef = change in log(game peak speed) per +1 EDP spot fallen, holding the Combine ceiling fixed;
> NEGATIVE = a slipping player realises LESS of the ceiling (the PI's stated expectation); the
> pre-registered TASK.md H1 wording predicts POSITIVE — see D50."

## 10. Figure — `fig_dose_response.png`, 300 dpi

Single panel. x = `edp_trend` (spots slid, positive = stock fell). y = realised % of the Combine
ceiling. **Zero-based y-axis, no axis break, no dual axis.**

* Binned means ± 95% CI (season_scope=all_seasons, scope=WR, binning=fixed, capital=unadjusted),
  with `n_players` labels.
* Spline curve (df 3, all_seasons/WR/unadjusted) + CI band.
* Vertical dashed reference lines at 3 and 5, labelled.
* Annotation: linear slope per spot, 95% CI, p, and N.
* Title carries the provenance stamp (`SYNTHETIC FIXTURE (not a finding)` / `UNVALIDATED SAMPLE OUTPUT`
  / `FULL`) and the words "exploratory — post hoc".

Written by the stage into its own output dir. The notebook §12 renders the same figure and saves it to
`outputs/figures/fig_dose_response.png` **only** when the source is FULL; otherwise it goes under
`outputs/figures/synthetic/` and is stamped.

## 11. Synthetic fixtures

Extend `src/synthetic_security_game_fixture.py` with four regimes under the **existing** root
`fixtures/synthetic/security_game/`: `dose`, `dose_null`, `dose_kink`, `dose_capital`. Keep the existing
`planted|null|selection` regimes byte-identical (new RNG draws only inside the new-regime branches).
Add `overall_pick` to `covariates.csv` **deterministically** (no new RNG draws for old regimes).

Planted effects (in the gap equation, so the target equals the fitted slope):

| regime | planted |
|---|---|
| `dose` | `gap += trend_slope * edp_trend`, `trend_slope = -0.002` |
| `dose_null` | `trend_slope = 0` |
| `dose_kink` | `0` below 5, `-0.006` per spot above 5 |
| `dose_capital` | no direct dose effect; `overall_pick` drives the gap and `edp_trend` is generated from the pick ⇒ unadjusted slope ≠ 0, capital-adjusted ≈ 0 |

`meta.json` records the planted truth for the tests.

## 12. Tests — `src/tests/test_dose_response.py` (standalone runner + pytest-collectable)

1. `rcs` basis: shape, first column == x, linear beyond boundary knots, monotone knots, knot 5 present,
   degenerate all-zero `d` and `<5` distinct values fall back instead of raising.
2. Bin assignment at every boundary, empty bin handled, thin bin flagged and not interpreted.
3. Cluster SE: repeated players → clustered SE differs from HC1/iid in the expected direction;
   `n_players` counts distinct ids.
4. Thin/`min_obs` guards: too few players → `thin=True`, null coef, no crash.
5. Recovery: `dose` slope within tolerance of −0.002 with p < 0.05 (all three scopes);
   `dose_null` not significant and |slope| below tolerance.
6. Kink: `slope_below_5` ≈ 0, `slope_above_5` ≈ −0.006 within tolerance.
7. Capital: `dose_capital` unadjusted slope significant, `pick_linear`-adjusted slope ≈ 0.
8. Determinism: two runs byte-identical `dose_fits.csv`.
9. CLI exit 0 on all four dose regimes; `provenance` first column; `sign_convention` non-empty;
   2 season scopes × 3 position scopes present.
10. Sample mode: labelled `PENDING FULL RUN` placeholder + checkpoint skip.
11. Figure written, > 15 KB, 300 dpi recorded.
12. All pre-existing test suites stay green.

## 13. Documentation to add

* `config.yaml`: new top-level `dose:` block (all thresholds/paths; no magic numbers in code).
* `src/stage_runner.py`: add `"10_dose_response"` to `STAGE_MODULES`.
* Notebook: `STAGE_ORDER` gains `"10_dose_response"` after `"07_security_models"`; `GAME_SIDE` and
  `SUMMARY_FILE` updated; new §12 cells appended at the **end** (cell indices of existing cells must not
  change — the contract test indexes them).
* `notes/decisions.md`: D50 — continuous exposure for this exploratory panel only; binary T3 result
  untouched; panel labelled exploratory/post hoc; **sign-convention discrepancy** documented
  (PI's stated intent: sliders realise less ⇒ negative slope; `TASK.md` H1 + config comment say the
  opposite) with the preregistered test left intact; capital-confound decision; figure is supplementary
  (not one of the 5 writeup figures).
* `notes/prereg-deviations.md`: DV-9 for the same, no frozen value changed.
* `notes/limitations.md`: append items for (i) exploratory/post hoc, (ii) dose–capital collinearity,
  (iii) small bins, (iv) the sign-wording discrepancy.
