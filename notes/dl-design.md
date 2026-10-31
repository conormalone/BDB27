# DL task — build contract (cycle 2026-10-30)

Binding spec: `TASK.md`. This file is the **implementation contract** for the new
DL chain. It does not override `TASK.md`; where they differ, `TASK.md` wins and
the difference is a bug in this file.

Python: `/mnt/project_data/bdb27_venv/bin/python` (system site packages +
duckdb 2.0.0, polars 2.1.0, pytest 9.2.0). Never use bare `python3` for stages
or tests. Baseline suite: **159 passed** (`test_` files that predate this cycle
must stay untouched and passing).

## 0. Hard constraints

* Do **not** edit, move or rename anything the LC chain uses: `src/00_thresholds.py`,
  `src/01_audit.py`, `src/02_combine_ceiling.py`, `src/03_route_depth.py`,
  `src/04_efforts.py`, `src/05_models.py`, `src/06_validation.py`,
  `src/07_phase4.py`, `src/lc_*.py`, `src/ceiling_core.py`, the `bdb27_lc:`
  config block, or any existing test.
* New code lives under `src/dl/`. New config under a new top-level key `bdb27_dl:`.
  Do not bump top-level `pipeline_version` (it would invalidate LC checkpoints).
* No magic numbers in code: every path, threshold, size and seed is read from
  `bdb27_dl`.
* Never load a whole CSV into pandas; Parquet + DuckDB with explicit column lists.
* All stage artefacts are written through the provenance writers; sample runs are
  stamped `PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT`.
* No `data/` or `outputs/` files are committed. `fixtures/**` are committed.

## 1. Data facts verified by the PM on 2026-10-30 (re-verify in the audit, do not assume)

These are observations, not gospel. The audit must reproduce them and log each
in `notes/schema.md`.

| # | fact | evidence |
|---|---|---|
| F1 | `player_play.csv` has 314,197 rows / 114,954 plays / 1,001 games; mean 2.73 rows/play; **only 497 nfl_ids appear** — it is the 510-prospect cohort, not all 22 players. | row counts; `count(distinct nfl_id)` |
| F2 | `players.csv` has 510 rows; DL cohort is `nfl_position in (DE,DT,NT)` = **88** (DE 36, DT 47, NT 5). 87 of them appear in `player_play`. | value counts |
| F3 | `player_play` DL rows: 46,911. On dropback plays 17,481 rows / 14,917 plays. | join on `dropback_duration` |
| F4 | `dropback_duration`, `pressure_allowed`, `sack_allowed`, `time_to_pressure_allowed`, `pass_rushers_encountered`, `peak_pressure_probability_allowed` are **100% null on DL rows**; they are populated on **OL rows** (G/T/C). `dropback_duration` is constant within a play (nunique=1 for all 45,056 dropback plays) and must be joined at `(game_id, play_id)`. | null rates; nunique |
| F5 | `time_to_pressure` null on **91.45%** of DL dropback rows (1,494/17,481 non-null). Null = no pressure credited to that rusher (event absent), not missing data. | null rate |
| F6 | `quick_pressure` null on 91.54% of the same rows; it is non-null on 1,494 rows, of which 1,479 also have `time_to_pressure`; agreement with `ttp <= 2.50` is **94.4%** (83/1,479 disagree). It is **not** a pure function of `time_to_pressure`. | crosstab |
| F7 | `time_to_qb_hurry` non-null 3,879 rows overall, superset of the 3,804 `time_to_pressure` rows. Null = no hurry credited. | counts |
| F8 | `sack` and `unblocked_pressure` are **never null on dropback rows** (0/0.5/1 and bool); they are null on non-dropback plays. Null = not applicable (run/non-dropback), not zero. | value counts |
| F9 | `blitzing` is **not** a team blitz flag. On DL dropback rows it is TRUE on 98.3% of INTERIOR_LINE and 92.3% of EDGE rows where defined, FALSE on 322. `blitzing == TRUE` ⇔ the DL rushed the passer (15,633/17,159 TRUE rows have `player_get_off`; 2/322 FALSE rows do). For LB/DB it is TRUE on ~10%. Team blitz must be **derived** (count of rushers > 4) from tracking at full run. | crosstabs |
| F10 | `time_to_pressure` play-level count (2,116 dropback plays) is roughly **half** the play-level `pressure_allowed` count (4,674). ~2,700 pressures are by rushers outside the prospect cohort (no DL row). The two accounting systems are not interchangeable. | crosstab |
| F11 | DL pass-rush rate (blitzing TRUE rows) = **8.72%** (2,169/24,878): EDGE 11.5%, INTERIOR_LINE 7.0%. | groupby |
| F12 | `player_get_off` present on 95.3% (EDGE) / 89.0% (INTERIOR) of rush rows; null 10.6% of DL dropback rows. | groupby |
| F13 | Combine: 88 DL, 71 with `FORTY_YARD_DASH` tracking (65 with 2 attempts, 6 with 1). 28 have shuttle tracking, 35 cone, 85 `SKILL_DRILLS_DL`. `combine_results` coverage: ten_yd_split/forty 80.7%, three_cone 38.6%, short_shuttle 40.9%. | counts |
| F14 | Tracking sample: 1,048,575 rows = 2^20−1, 333 games (2023 + one 2024 playoff), 12,638 plays, 154 players, truncated mid-game. Cannot exercise all-22 or team blitz. | counts |
| F15 | No rusher-blocker assignment field exists in any provided file. Must be derived from tracking (nearest OL at snap) at full run. | header check |
| F16 | `player_get_off` exists on run plays too (46.8% of non-dropback DL rows). Never treat get-off as pressure-only. | crosstab |

## 2. Package layout

```
src/dl/__init__.py
src/dl/dl_common.py        # engine for bdb27_dl
src/dl/dl_ceiling_core.py  # pure Combine fitting / ICC / VIF / lateral mechanics
src/dl/dl_fixtures.py      # synthetic schema-compliant worlds (committed fixture + generator)
src/dl/stage_01_audit.py   # Phase 1 (a-k) + gate verdict
src/dl/stage_02_ceiling.py # Phase 2
src/dl/stage_00_thresholds.py
src/dl/stage_03_snaps.py   # Phase 3
src/dl/stage_04_models.py  # Phase 4
src/dl/stage_05_validate.py# Phase 5
src/dl/run.py              # dispatcher
tests/test_dl_*.py
fixtures/synthetic/dl/     # committed small fixture CSVs + config.yaml
```

Run: `cd src && /mnt/project_data/bdb27_venv/bin/python -m dl.run 01_audit --mode sample`
(also `python src/dl/run.py 01_audit --mode sample` must work from repo root).
Stage names are exactly `01_audit, 02_combine_ceiling, 00_thresholds, 03_game_snaps,
04_models, 05_validate`. `--config` is accepted by every stage.

## 3. `dl_common.py` API (implement exactly)

Same contract as `lc_common`, block `bdb27_dl`:

```python
REPO_ROOT, DEFAULT_CONFIG, SAMPLE_STAMP
load_config(path=None) -> dict          # raises KeyError if no bdb27_dl
dl(cfg) -> dict
parse_args(argv=None) -> Namespace(mode, force, config)   # --mode/--force/--config
resolve_paths(cfg, mode) -> dict[str, Path]
source_paths(cfg, key) -> list[Path]
tracking_sources(cfg, mode) -> list[Path]
duckdb_connect(cfg)
to_parquet(cfg, key, out_name, columns=None, mode="sample", force=False, source=None) -> Path
provenance(cfg, mode) -> str
provenance_stamp(cfg, mode) -> str
require_full_provenance(cfg, mode, stage) -> str
require_prereg(cfg, mode, stage) -> Path   # full only: file exists AND contains "FROZEN"
write_csv(path, rows, fieldnames, *, mode, cfg) -> Path   # leading provenance col
write_json(path, obj, *, mode, cfg) -> Path               # leading provenance key
write_markdown(path, text, *, mode, cfg) -> Path
timed_stage(cfg, stage, mode, note="")  # appends to configured run_log
seed_everything(seed), stable_hash(obj)
config_fingerprint(cfg, stage) -> str   # hashes cfg["seed"] + cfg["bdb27_dl"] + stage
checkpoint_path/is_done/mark_done(cfg, stage, mode, force=False)
assert_disk_space(cfg) -> dict
schema_check(obj, expected)             # re-export from lc_common
EXPECTED_SCHEMAS, SchemaError, SAMPLE_STAMP
```

Reuse pure helpers by importing from `lc_common` (`schema_check`, `SchemaError`,
`EXPECTED_SCHEMAS`, `seed_everything`, `stable_hash`, `SAMPLE_STAMP`). Do not
duplicate the schema declarations. Do not call `lc_common.lc()` anywhere.

`config_fingerprint` must include only `seed` + `bdb27_dl`; it must not read
`bdb27_lc`. Checkpoints: `data/interim/dl_checkpoints/<stage>__<mode>__<fp16>.json`.

`mode.sample.sample_row_cap = 1048575`; a full-mode run must refuse if the
tracking row count is ≤ cap (evidence gate, same as LC).

## 4. Config block `bdb27_dl` (values are a-priori design constants)

```yaml
bdb27_dl:
  paths: {raw: data/raw, interim: data/interim, parquet: data/interim/dl_parquet,
          checkpoints: data/interim/dl_checkpoints, tmp: data/interim/tmp,
          outputs: outputs/dl, figures: outputs/dl/figures,
          fixtures: fixtures/synthetic/dl, thresholds: outputs/dl/thresholds.json,
          run_log: outputs/run_log.csv, prereg: notes/prereg.md}
  files: {combine_tracking: combine_tracking.csv, combine_results: combine_results.csv,
          players: players.csv, games: games.csv, player_play: player_play.csv,
          game_tracking_sample: game_tracking_2023_sample.csv,
          game_tracking_full: [game_tracking_2023.csv, game_tracking_2024.csv, game_tracking_2025.csv]}
  seeds: {base: 20270106, fixture: 20270107, bootstrap: 20270108,
          permutation: 20270109, model: 20270110}
  mode: {default: sample,
         sample: {tracking_key: game_tracking_sample, provenance: PLACEHOLDER_SAMPLE,
                  sample_row_cap: 1048575, run_game_side: true},
         full: {tracking_key: game_tracking_full, provenance: FULL,
                sample_row_cap: 1048575, run_game_side: true}}
  runtime: {duckdb_threads: 1, duckdb_memory_limit: "1200MB",
            temp_directory: data/interim/tmp, csv_null_marker: "NA",
            empty_string_is_category: true, parquet_compression: zstd,
            parquet_row_group_size: 122880, chunk_rows: 250000,
            min_free_mb_root: 40, min_free_mb_interim: 1024,
            rss_sample_interval_s: 0.2}
  positions: {dl: [DE, DT, NT], edge: [EDGE], interior: [INTERIOR_LINE],
              ol: [G, T, C], qb: [QB]}
  thresholds:
    speed_cap_yds: 12.0
    combine_sampling_hz: 10
    combine_dt_tolerance_s: 0.02
    smoothing: {method: centered_rolling_median, window_frames: 5}
    peak_window: {frac_of_max: 0.9, pad_s: 0.2}
    gap: {interp_max_s: 0.5, interpolate_inside_peak_window: false}
    icc_min: 0.50
    vif_max: 5.0
    max_features: 3
    min_attempts_for_icc: 2
    min_frames: 20
    min_r2: 0.90
  ceiling:
    fit_drills: [FORTY_YARD_DASH]
    lateral_drills: [SHORT_SHUTTLE, THREE_CONE_DRILL]
    model: "vmax * (1 - exp(-t / tau))"
    a0_definition: vmax_over_tau
    bounds: {vmax_yds: [4.0, 13.0], tau_s: [0.3, 3.0]}
    initial: {vmax_frac_of_peak: 1.05, tau_s: 1.4}
    candidate_features: [a0, vmax, tau, speed_at_1_0, speed_at_2_0,
                         ten_yd_split, peak_accel]
    primary_feature: a0
    fallback_feature: speed_at_1_0        # only on Conor's decision, never automatic
    lateral: {entry_window_s: 0.3, exit_window_s: 0.3, braking_min_drop_yds: 1.0}
  audit:
    min_matched_dl: 40
    min_pass_rush_snaps_per_rusher: 100
    min_players_meeting_snaps: 25
    min_snap_coverage_share: 0.90         # Part 2 requirement
    design_effect: 1.3
    alpha: 0.05
    power: 0.80
    max_mde_hr: 1.15
    out_subdir: audit
  game:
    snap_out_subdir: game/snaps
    post_snap_window_s: [1.0, 2.0]
    los_tolerance_yds: 1.0
    blocker_radius_yds: 2.0
    blitz_rusher_threshold: 4             # team rushers > 4 => blitz
    min_snaps_per_player: 100
    garbage_time: {q4_margin: 21, any_quarter_margin: 28, q4: 4}
  load:
    absolute_speed_grid_yds: [1.5, 2.0, 2.5, 3.0, 3.5]
    relative_fraction_grid: [0.5, 0.6, 0.7, 0.8, 0.9]
    recency_half_life_s: 300.0            # identical for both definitions
    rest_column: seconds_since_last_snap
    posterior_draws: 200
  models:
    out_subdir: models
    primary_estimator: aft_frailty_lognormal
    frailty_quadrature_nodes: 20
    rungs: [aft_frailty_lognormal, cox_cluster, aft_cluster]
    cluster: player
    controls: [alignment, pre_snap_depth_yd, blitz, n_blockers, down, yards_to_go,
               score_margin, position_group, height_in, weight_lb, years_since_combine]
    random_effect: rusher
    crossed_fixed_effects: [opponent_team, primary_blocker_group]
    cv_folds: 5
    bootstrap_reps: 1000
    max_iter: 500
    mde_note: "HR per 1 SD a0; smallest effect fixed in prereg"
  validate:
    out_subdir: validation
    permutations: {reps: 1000, ceiling_scheme: within_position_group,
                   order_scheme: within_player_game}
    placebos: [alignment, pre_snap_depth_yd, height_in, weight_lb]
    equivalence: {margin_hr: 1.05, soi_hr: 1.10, alpha: 0.05,
                  margin_logscore: 0.002, soi_logscore: 0.005}
    garbage_time: {rule: quarter_and_margin, q4: 4}
    final_two_minutes_s: 120
  power:
    smallest_effect_hr: 1.10
    equivalence_margin_hr: 1.05
    smallest_effect_logscore: 0.005
    equivalence_margin_logscore: 0.002
    assumed_sd_a0: 1.0
    design_effect: 1.3
  fixture:
    seed: 20270107
    n_players: 60
    n_games: 8
    drives_per_game: 6
    snaps_per_drive: 6
    frames_per_snap: 40
    tracking_hz: 10
    planted_hr_a0: 1.35            # per 1 SD a0
    planted_decay_relative: 0.35   # log-hazard per unit relative load
    planted_decay_absolute: 0.0
    null_world: false
    rare_pressure_rate: 0.02
```

All values are design constants, **not tuned on sample output**. The audit may
report that a gate fails; it may not change a constant.

## 5. Stage contracts

### 5.1 `stage_01_audit.py` → `outputs/dl/audit/`
Writes `a_drills.csv`, `b_matched.csv`, `c_snap_coverage.csv`, `d_null_semantics.csv`,
`e_label_provenance.csv`, `f_alignment.csv`, `g_assignments.csv`, `h_units.csv`,
`i_icc.csv`, `j_load_collinearity.csv`, `k_power.csv`, `decision.json`, `SUMMARY.md`
and `provenance_findings.md` (the F-table above, confirmed/corrected).

* (a) drills present for DL, class years, attempts/player/drill.
* (b) matched DL, pass-rush snaps, games, drives, edge vs interior from
  `lined_up_position`; also participation share = player rows / team defensive plays.
* (c) snap coverage from tracking where present (sample), and the player_play
  participation share (always). Frame-level completeness is `PENDING FULL RUN`.
* (d) null semantics for `quick_pressure`, `unblocked_pressure`,
  `time_to_pressure`, `time_to_qb_hurry`, `sack`: rate by row set (all / dropback /
  rush), plus the hypothesis tests in F5–F9. Resolved by evidence, with the
  evidence printed.
* (e) pressure label provenance: quote the official docs if present in
  `notes/` or `data/raw/external`; otherwise state "not documented in the
  provided material" and report the two-label-system discrepancy (F10). Flag
  circularity plainly.
* (f) alignment/pre-snap depth availability; `blitzing` semantics (F9); team
  blitz derivable only from tracking → `PENDING FULL RUN`.
* (g) rusher-blocker assignments: field absent (F15), proxy defined, `PENDING FULL RUN`.
* (h) Combine distance/acceleration reliability: compare `s`,`a`,`x`,`y` derived
  vs provided; sampling rate; units; flag speeds > cap.
* (i) ICC of candidate features on DL (a0, tau, vmax, speed@1.0, speed@2.0,
  10-yd split, peak_accel, shuttle/cone mechanics). Report n players, attempts,
  ICC, CI if cheap. **Report as measured; never adjust `icc_min`.**
* (j) correlation between candidate relative and absolute load measures on
  synthetic/sample snaps (needs Phase 3; the audit calls it `PENDING` before 03
  and re-runs after 03 in a full run).
* (k) power: MDE HR for 1 SD a0 from actual event counts and design effect;
  MDE for the Part-2 log-score difference from n snaps/events. Compare to
  `power.smallest_effect_hr` / `smallest_effect_logscore`.

Gate (`decision.json`): `GO`, `GO_PENDING_FULL_RUN`, `CONDITIONAL`, `NO-GO`
plus a `reasons` list and per-item status (`PASS|FAIL|PENDING_FULL_RUN|NA`).
A FAIL on a binding item is `NO-GO`; only PENDING items that need full tracking
give `GO_PENDING_FULL_RUN`.

### 5.2 `stage_02_ceiling.py` → `outputs/dl/ceiling/`
`player_ceiling.csv` (long: nfl_id, position_group, feature, estimate, se,
n_attempts, icc, reliability, selected, vif), `attempt_fits.csv`, `summary.json`,
`SUMMARY.md`. Shrinkage per player; SE everywhere; vmax/a0 correlation reported;
ICC/VIF screen picks ≤ `max_features`; `primary_feature` must be in the selected
set to claim GO, otherwise the gate says so (no silent fallback).
`speed_at_1_0` / `speed_at_2_0` = `vmax*(1-exp(-t/tau))` at t=1.0/2.0.
`ten_yd_split` from `combine_results` (not the curve). `peak_accel` from the
provided `a` column's max, flagged exploratory if it duplicates a0.

### 5.3 `stage_00_thresholds.py` → `outputs/dl/thresholds.json`
Provenance `PLACEHOLDER_SAMPLE` in sample, `FULL` in full. Contains: load grids,
chosen thresholds (null until tuned in 04/03), min snaps, garbage-time rule,
prereg path + frozen flag. 03/04/05 assert `FULL` in full mode.

### 5.4 `stage_03_snaps.py` → `outputs/dl/game/snaps/snaps.csv`
One row per DL pass-rush snap: keys, `event` (pressure 0/1), `time_to_pressure`,
`censor_time` (dropback_duration), `quick_pressure`, `sack`, `player_get_off`,
alignment, pre_snap_depth_yd, blitz (derived; null in sample), n_blockers,
stunt (null where unlabeled), in-game `speed_at_1_0`/`speed_at_2_0`,
absolute_load, relative_load, snaps_in_drive, seconds_since_last_snap,
down/distance/quarter/clock/score_margin, opponent_team, primary_blocker_group.
Exclusions counted and written to `snaps_summary.json`.
Sample mode: derive what player_play allows; tracking features only where the
sample has frames; every missing item is `NA` + counted, never zero-filled.

### 5.5 `stage_04_models.py` → `outputs/dl/models/`
`part1_result.json`, `part2_result.json`, `cv_folds.csv`, `load_tuning.csv`,
`mediation.json`, `reliability.csv`, `model_result.json`, `SUMMARY.md`.
Refuses `provenance != FULL` for reportable numbers in full mode; sample mode
stamps everything.

### 5.6 `stage_05_validate.py` → `outputs/dl/validation/`
`validation_result.json`, `permutation_null.csv`, `placebo_checks.csv`,
`rotation.csv`, `context_sensitivity.csv`, `equivalence.json`, `SUMMARY.md`.
Every check that cannot run is reported with `status: not estimable` + reason.

## 6. Fixtures (must be committed, small)

`src/dl/dl_fixtures.py` writes a complete schema-compliant set into
`fixtures/synthetic/dl/`: `combine_tracking.csv`, `combine_results.csv`,
`players.csv`, `games.csv`, `player_play.csv`, `game_tracking.csv`, and
`config.yaml` (same `bdb27_dl` block with `paths.raw` pointing at the fixture
dir, seeds fixed). Worlds:
1. **planted** — a0 drives pressure (HR 1.35/SD), ceiling-relative decay planted;
2. **absolute** — decay depends on absolute load only;
3. **null** — no a0 effect, no load effect;
4. **censoring** — rare pressures (2%) with heavy censoring.
Each world's IDs are renamed so they cannot collide. Tests run stages 01→05 on
the fixture config and assert recovery/direction, not sample numbers.

## 7. Tests (mechanical validation)

Pre-existing 159 tests untouched and green. New tests must cover:
schema assertions (columns/dtypes/units) on real files and fixture; determinism
(same seed → identical bytes); every null-semantics claim in F5–F9; gate
VERDICT logic (GO/NO-GO/PENDING branches); ceiling recovery (known curve);
ICC edge cases (1 attempt, zero variance); load definitions (absolute vs
relative, rest, recency, same tuning budget); synthetic recovery in all four
worlds; models refusing sample provenance in full mode; edge cases (few snaps,
null labels, no dropback end, missing quarters, one-attempt players, missing
frames, no game data); scale (sample × k with renamed IDs, memory roughly
linear); prereg gate (full mode refuses without FROZEN).

## 8. Honesty rules

* Sample numbers are labelled and never interpreted in prose as findings.
* `notes/schema.md` gets a new "DL task" section with every mismatch.
* `notes/blockers.md` gets only missing data/fields (not missing documents).
* If a0 ICC < 0.50 the gate is not tuned: report it, propose speed@1.0 as the
  fallback, and leave the decision to Conor.
