# Spec — band-reliability figures (PM cycle D207)

**Written by the PM, 2026-11-16.** Binding on the Coder for this cycle. Source of the
figure requirements is `notes/viz-menu-reliability.md` (Storyteller, 2026-11-16); this
spec narrows it to what is built, and it wins where the two differ.

**Conor's instruction, verbatim:** *"Ok do them up"* — 2026-11-16 14:58 GMT, replying to a
message listing the six menu figures. That authorises **building the figures as artefacts**.
It does **not** authorise amending `TASK.md`, naming a figure in the draft/notebook, or
retiring an existing figure.

## 0. Non-negotiables

1. **DB only.** Every panel filters `setting == 'game'`, `position_group == 'DB'`,
   `30 <= abs_turn_angle < 60` (left-closed, right-open), `valid == True`. No other
   position is drawn, not even as grey context.
2. **No player identity on a canvas or in a figure CSV.** `nfl_id` may appear in the
   `turns_per_player.csv` artefact; it must never appear in a `fig_*.csv`, in a manifest
   `drawn` block, or in any drawn string. No display names anywhere.
3. **No per-player ranking.** Never sort players by a value, never draw a player-level
   dot series, never label a player. Figures 1/2/3/5 are aggregate-only by construction.
   Figure 4's two histograms are unordered distributions.
4. **One accent, grey context.** Grey `#B9BEC4`; accent `#1F4E79` at 0.85 alpha; panel
   `#EBEBEB`; border `#B3B3B3`. One meaning per panel.
5. **Counts and denominators on the marks**, not implied by bar length. Every panel that
   shows a statistic prints its `n_turns`, `n_players` and `k̄` beside it.
6. **No threshold lines.** Do not draw `label.thresholds` (`icc_stable 0.5`, `icc_weak 0.2`,
   `icc_void 0.2`, `icc_upper_void 0.4`) or the `VOID` label. The only rule allowed is the
   `R = 0.70` **target**, labelled "target R = 0.70" (figure 3 only).
7. **No arbitrary gates.** A thin or non-estimable row prints its counts and
   "not estimable" and still renders. The only legitimate refusals are in §6 below.
8. **No outcome language.** Words "separation", "pressure", "production", "predicts" are
   banned from every drawn string and manifest claim.
9. **No on-image provenance banner** (`figures.provenance.on_image_stamp: false`, D180).
   Provenance lives in the CSV `stamp` column, the `.provenance.json`, and the manifest.

## 1. Do not touch

`report-draft.md`, `TASK.md`, `notebooks/`, `notes/validation-section.md`, any existing
`fig_*` PNG/CSV/JSON, `outputs/figures/figures_manifest.json` **other than adding entries**
(never remove or rewrite an existing entry), `notes/decisions.md` (the PM writes D207),
and no re-run of stage 02, no new band edge, no new threshold.

## 2. What is built, in commit order

| # | figure key | script `--figure` | needs |
|---|---|---|---|
| 1 | `fig_third_of_one_percent` | `fig_third_of_one_percent` | §3.1 (artefact fields) |
| 2 | `fig_three_routes` | `fig_three_routes` | nothing new |
| 3 | `fig_reliability_vs_turns` | `fig_reliability_vs_turns` | §3.2 (`turns_per_player.csv`) |
| 4 | `fig_within_between_spread` | `fig_within_between_spread` | §3.2 |
| 5 | `fig_icc_intervals` | `fig_icc_intervals` | §3.3 (per-scenario bootstrap) |

Menu candidate 6 ("What it would take") is **parked** — see §7. Do not build it.

**Commit after each figure that renders, and push.** A committed figure 1 is worth more
than an uncommitted figure 3.

## 3. Data additions (the only three)

### 3.1 Adjusted variance components — `src/bdb27/band_reliability.py`

`adjustment.{all_band,entry_3_5}` gains `sigma2_a` and `sigma2_e`, taken from the dict
`icc1(...)` already returns in `adjusted_icc()` (fields `sigma2_a`, `sigma2_e`, which are
the unclamped `max(sigma2_a, 0)` and `msw`). No new estimator, no new moments, no change to
`icc`. Also add `sigma2_a_raw` for symmetry with the scenario rows if it is already returned
(it is). Regenerate `outputs/band_reliability/reliability.json`, `scenarios.csv` and
`manifest.json` with the existing script and commit them. **Verify before/after that every
existing value is unchanged** and the only diff is the new keys — say so in the commit.

Acceptance: `sigma2_a/(sigma2_a+sigma2_e)` reproduces `adjustment.*.icc` to 1e-12 for both
samples; `band_reliability.icc1 is fallback_validation.icc1` still holds.

### 3.2 Per-player exposure — `outputs/band_reliability/turns_per_player.csv`

New pure function `band_reliability.per_player_exposure(frame)` returning a frame with
columns `nfl_id, scenario, n_turns, mean_apex_retention, sd_apex_retention`, one row per
`(nfl_id, scenario)` over the four scenario names in `SCENARIOS` order. `sd` is the sample
standard deviation (`ddof=1`), and is `None`/blank where a player has one turn.

New script `scripts/band_turns_per_player.py` (same shape as `scripts/band_reliability.py`:
`--mode full`, `common.require_inputs`, sha256 of both inputs and the output, writes into
`outputs/band_reliability/turns_per_player.csv`). It must **not** be merged into
`figures_manifest.json` (it is not a figure).

Acceptance: for `scenario == 'all_band'` the 113 rows sum to 7,777 turns, and
`min/median/max` equal the manifest's `turns_per_player` (1 / 48 / 300). No gate on any
count: 1-turn players are written with an `n_turns` of 1 and a blank `sd`.

### 3.3 Per-scenario bootstrap — `outputs/band_reliability/reliability.json`

Each row of `scenarios[]` gains

```json
"ci_bootstrap": {"lo": ..., "hi": ..., "reps": 2000, "seed": 20261115,
                 "n_estimable_reps": ..., "method": "player-level percentile bootstrap (unclamped ratio)",
                 "level": 0.95, "stream": "icc1_<scenario name>"}
```

by calling the existing `fallback_validation.icc1_bootstrap_ci(y, groups, reps=2000,
stream=f"icc1_{name}")` — **imported, not reimplemented**, exactly as `icc1` is. The
whole-band row must reproduce D203's `[-0.0012, 0.008]` with stream `icc1` (D203 used
`stream="icc1"`; use the same stream for `all_band` so the number is identical, and distinct
streams for the other three). If `all_band` does not reproduce `[-0.0012, 0.008]` bit-for-bit,
stop and report — do not paper over it.

Acceptance: `all_band.ci_bootstrap.lo == -0.0012` and `.hi == 0.008` as printed by D203's
artefact; `n_estimable_reps == 2000` on all four; the analytic `ci_analytic` is untouched.

## 4. Module and script shape

One module `src/bdb27/reliability_figures.py` holding pure builders (no I/O except the
three loaders) and one script `scripts/plot_reliability_figures.py`:

```
.venv/bin/python scripts/plot_reliability_figures.py --figure all      # or one key
```

The script writes, per figure: `outputs/figures/<key>.png`, `.csv`, `.provenance.json`,
`.drawn_text.json`, and one merged entry in `outputs/figures/figures_manifest.json`.
Model every one of those on `scripts/plot_turn_angle_hist.py` for the script and
`src/bdb27/full_run_positions.py` (`render_figure`, ~lines 280-400) for the
drawn-text/provenance pair. Reuse `figure_text_scan.figure_texts` for the banned-token
scan and `full_run._visible_strings`' rule (visible strings only) for the dump.

Module constants: `GREY = "#B9BEC4"`, `ACCENT = "#1F4E79"`, `ACCENT_ALPHA = 0.85`,
`PANEL = "#EBEBEB"`, `BORDER = "#B3B3B3"`, `DPI = 150`.

Loaders raise `FileNotFoundError` naming the path and what to set; missing columns raise
`KeyError` naming the file and every missing column. Nothing else raises.

Manifest entry (per figure): `stage`, `artefact_version`, `figure`, `exploratory: true`,
`in_report: false`, `title`, `claim`, `png`, `csv`, `provenance: "FULL"`, `stamp`,
`sources`, `source_artefacts` (`path`, `sha256`, `role`), `caveats`, `rendered_text`, plus
the numbers block below. `in_report: false` is mandatory — the figure is built, and
`TASK.md`'s four-figure clause is **not** amended by this cycle.

## 5. Per-figure contract

### 5.1 `fig_third_of_one_percent` — which bar is the player

- **Data.** `outputs/band_reliability/reliability.json` → `scenarios[]` only.
- **Draw.** Four horizontal 100%-stacked bars, ordered `all_band`, `entry_3_5`,
  `entry_3_5_pass`, `pass_only`. Grey = within-player (`sigma2_e`), accent = between-player
  (`sigma2_a`) to scale. x 0-100%, no interior ticks, one x label "share of total variance".
- **Marks.** Right-hand tabular column per row:
  `0.34%  ·  7,777 T / 113 P  ·  k̄ 68.8` — share to 2 dp from
  `sigma2_a/(sigma2_a+sigma2_e)`, then turns, then players, then k̄ to 1 dp. Compute every
  number from the artefact; hardcode none.
- **Annotation,** top row only: *"the accent segment is the ICC, drawn to scale."*
- **Not drawn.** No adjusted rows, no zoom inset, no threshold line, no ratio between rows.
  The two adjusted ICCs (0.37%, 1.41%) go in the manifest `caveats`/`adjusted_not_drawn`
  block as text and in the CSV, **not** on the canvas.
- **Thin row.** A scenario with `sigma2_a` or `icc` null still draws its full-width grey bar
  with "not estimable" in the numeric column. Never raise.
- **CSV:** `scenario, rule_label, players, turns, kbar, icc, sigma2_a, sigma2_e,
  between_share, status`.

### 5.2 `fig_three_routes` — is the near-zero an estimator artefact

- **Data.** `outputs/fallback_validation/reliability.json` → `icc1`, `split_half`,
  `season_2024_2025`, `season_2024_2025_min10`.
- **Draw.** Three equal panels, one shared x = agreement, **−0.3 to 1.0**, shared ticks, a
  thin zero rule across all three. Each panel headed by *method*, not by result:
  "ICC(1)", "split-half (Spearman-Brown)", "season to season".
- **Values, all read from the artefact:**
  - ICC(1) `0.0034`, interval `[-0.0012, 0.0080]`, `7,777 turns / 113 players`.
  - Split-half S-B `-0.1055`, interval `[-0.2598, 0.1486]`, `112 players`.
  - Season 2024 vs 2025: `0.1959` (n = 66) **and** `0.0067` (n = 39, ≥10 turns/season) —
    two **bare dots**, no interval, because none exists.
- **Marks.** n under each dot. Under the middle panel: *"one player dropped (one-sided
  split), 112 of 113 used."* Under the season panel: *"no interval computed"*.
- **Refusal:** none (bare dots are the specified honest fallback).
- **CSV:** `method, value, lo, hi, n_players, n_turns, interval_computed`.

### 5.3 `fig_reliability_vs_turns` — what a player mean is worth

- **Data.** `reliability.json` → `derived.*`; `turns_per_player.csv` (scenario `all_band`).
- **Draw.** One panel. x = turns per player, **log**, 1 → 300, ticks at 1, 10, 30, 164, 300.
  y = reliability of that player's mean, 0 → 1.
  - accent curve: ρ = `derived.inputs.rho_entry_3_5_adjusted`;
  - grey curves: ρ = `rho_entry_3_5` and `rho_all_band`, each labelled with its own n and k̄;
  - dashed horizontal rule at 0.70 labelled *"target R = 0.70"* (a target, not a pass mark);
  - rug at y = 0, one tick per player from `turns_per_player.csv`.
- **Marks.** k = 30 → R = 0.300 (accent) and 0.220 (grey); harmonic mean k = 14.09;
  crossing k = 163.7 annotated *"164 turns/player ≈ 18,500 turns; 3,385 held"*;
  rug summary (113 players, median 48, min 1, max 300) printed.
- **Caption.** Must state: the curve is the formula applied to one point estimate, not a
  fitted model; the log axis compresses 30 → 164 (5.5×).
- **CSV:** `k, rho, R, note` for the plotted grid plus `marked_points` rows for the values
  above; and `rug_summary` in the manifest. No `nfl_id` column.
- **Thin rug.** If `turns_per_player.csv` has fewer rows than expected, print the row count
  and render. Missing file → the named FileNotFoundError.

### 5.4 `fig_within_between_spread` — the same turn, twice

- **Data.** `turns_per_player.csv` (`mean_apex_retention`) + the FULL turn parquet
  (`apex_retention`, filtered per §0.1) for the deviations.
- **Draw.** Two densities on one axis (percentage retention), shared bin width: grey = all
  7,777 turns as deviations from their own player's mean; accent = the 113 player means,
  drawn in front at 0.85 alpha. **Densities, not counts** — print "densities" on the axis.
- **Marks.** *"7,777 turns · 113 players; accent is the player means"*; the SD ratio
  (√ICC = 0.058) printed; x window bounds and the count of turns outside the window.
- **Provable arithmetic:** per-player deviations sum to zero; the 113 means' variance over
  the turns' variance reproduces the ICC to 1e-6 (test this).
- **CSV:** `bin_low, bin_high, density_turns, density_means, count_turns, count_means`.
  No ids.

### 5.5 `fig_icc_intervals` — no framing rescues it

- **Data.** `reliability.json` → `scenarios[].icc` and `scenarios[].ci_bootstrap`.
- **Draw.** Four horizontal dot-and-whisker rows, x = ICC **−0.01 to 0.03**, zero rule,
  grey whiskers, accent dots, `n_turns / n_players / k̄` right-aligned per row. Do not clip a
  negative lower bound at zero.
- **Refusal (this is the one the menu specifies):** if **any** of the four scenarios has no
  `ci_bootstrap`, raise `common.StageRefused` naming the scenarios that lack it and print the
  four point estimates first. Do not draw one interval beside three bare dots.
- **Marks.** `clamped: false` on all four stated in the caption.
- **CSV:** `scenario, icc, lo, hi, reps, seed, n_estimable_reps, turns, players, kbar`.

## 6. Tests — `tests/test_reliability_figures.py`

In the style of `tests/test_band_reliability.py` (synthetic frames, no network, no full
parquet where avoidable). Required pins:

1. **Shares are the ICC.** For each real scenario, `between_share` from the CSV equals
   `sigma2_a/(sigma2_a+sigma2_e)` and equals `icc` to 1e-9.
2. **§3.1 preserves everything.** Before/after the change, every key of `scenarios[]` and
   every `adjustment.*.icc` is unchanged; the new keys satisfy the identity above.
3. **§3.2 totals.** Synthetic + real: `all_band` rows sum to the turn count; median/min/max
   match; a 1-turn player has a blank sd and does not raise.
4. **§3.3 reproduction.** `all_band.ci_bootstrap` equals D203's `icc1.ci_bootstrap`
   (`lo`, `hi`, `reps`, `seed`).
5. **Figure 2 refusal-free fallback.** With `season_2024_2025` present but no interval, the
   builder returns `interval_computed == False` for both season dots and does not raise.
6. **Figure 3 arithmetic.** `R(k=30, rho_adjusted)` matches `derived` to 1e-9; the marked
   crossing equals `k_needed_for_R_0_70_at_adjusted_rho`; a rug of one player renders.
7. **Figure 4 arithmetic.** Per-player deviations sum to zero; means-variance over
   turns-variance reproduces `icc1` to 1e-6; clipping count is printed and consistent.
8. **Figure 5 refusal path.** With one scenario's `ci_bootstrap` deleted, the builder raises
   `common.StageRefused` and the message names that scenario; with all four present it
   returns four rows.
9. **No identity, no ranking, no gates.** For every figure: no drawn string matches any
   `nfl_id` in `turns_per_player.csv`; no `fig_*.csv` has an `nfl_id` column; no drawn string
   contains "VOID", "stable", "separation", "pressure", "production"; manifest
   `gates_drawn == False` and `ranked_players == False`.
10. **Determinism + artefacts.** Rendering one figure twice into `tmp_path` is byte-identical
    for CSV and `.drawn_text.json`; the `figures_manifest.json` merge **adds** a key and
    leaves the other 19 entries byte-identical.
11. **Missing input.** A missing `reliability.json` / `turn_context` / parquet raises
    `FileNotFoundError` naming the path; a missing column raises `KeyError` naming file and
    column.

## 7. Menu candidate 6 is parked, with the reason

`fig_what_it_would_take` is **not built**. The menu's own recommendation (its §6 and rank
table) is to fold "3,385 → ≈18,500" into figure 3 as one annotation — done, §5.3 — and to
keep the ≈590-cluster number in prose, because an outcome-power quantity drawn as a bar
gives a `TASK.md`-banned claim a picture. The D207 note records this decision and the
reason; it is not a silent omission.

## 8. Definition of done

- Five figures render; each has PNG, CSV, `.provenance.json`, `.drawn_text.json`, one new
  manifest entry with `in_report: false`.
- `notes/viz-menu-reliability.md` and this spec are committed (they are the provenance of
  the decisions).
- `pytest -q` green, with the new test file's count reported.
- Every commit pushed; `git status` clean; `HEAD == origin/main`.
- No change to `TASK.md`, `report-draft.md`, the notebook, or any pre-existing figure file.
