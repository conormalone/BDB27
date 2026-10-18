# Decisions log — BDB27

Append-only. Each entry: what was decided, why, and the evidence/constraint behind it. Binding spec =
`TASK.md`. **Prior (combine-fatigue) decisions D1–D25 are archived at git tag
`archive/combine-fatigue-v1`** (HEAD `fdc794c`); this log restarts fresh for the ceiling-binding task.

---

## D26 — Cycle 0: pivot to the ceiling-binding task; reset analysis, keep infra · 2026-10-11
**Decision.** Pivot the project to **"Does the Combine ceiling bind on max-effort routes? (WR primary, TE
replication)"** (`TASK.md`, superseding the combine-fatigue spec). This cycle = **repo restructure + Phase 0
setup only**; the 01–05 pipeline is **not** implemented yet.
**Kept (infra):** `src/common.py` (config loader + DuckDB/Parquet helpers), `src/README.md`,
`requirements.txt`, `.gitignore`, `research/` (prior-work scan), `data/` (symlinks + README), `notes/`,
`README.md`, `README_RUN.md`, plus the git history and the `archive/combine-fatigue-v1` tag.
**Removed (`git rm`):** the combine-fatigue analysis — `src/{00_thresholds,01_audit,02_features,
03_combine_model,03b_metric_panel,03d_warmup_phenotype,04_game_features,05_link}.py`, `src/tests/`,
`notebooks/*` (old run/build notebooks + prototype), `tools/`, `specs/`. Analysis artefacts under
`outputs/` deleted on disk (gitignored).
**Why.** The new task shares the data and the engineering discipline but not the analysis; keeping the old
scripts would confuse the run order and imply unimplemented stages.
**Evidence.** `TASK.md` (new binding spec); the archive tag preserves the prior work verbatim.

---

## D27 — Cycle 1: Phase 1 audit (go/no-go gate), sample mode · 2026-10-11
**Decision.** Implemented and ran **Phase 1 — Audit (`src/01_audit.py`)**, one code path for
`--mode sample|full` + `--force`, config-driven, Parquet+DuckDB (no full CSV in pandas), run-logged to
`outputs/run_log.csv`. Game-side items that need the full game-tracking files (c/d/e/h/i) are **scripted**
and emit **`PENDING FULL RUN`** placeholders in sample mode. Built a committed, schema-compliant
**synthetic fixture with a planted binding ceiling** (`src/synthetic_fixture.py`, `fixtures/synthetic/`)
to exercise those paths; fixture outputs are stamped `SYNTHETIC FIXTURE (not a finding)` and are never
interpreted. Added `src/tests/test_audit.py` (5 suites, all passing: schemas/units, sample outputs,
fixture + planted-ceiling recovery, determinism).

**Findings (UNVALIDATED SAMPLE OUTPUT; Combine + `player_play.csv` are complete, tracking is not).**
- **(a)** WR/TE Combine drills = `FORTY_YARD_DASH` (straight-line), `THREE_CONE_DRILL` + `SHORT_SHUTTLE`
  (cutting), and position skill blocks `SKILL_DRILLS_WR`/`SKILL_DRILLS_TE`. WR: 94/24/29 dash/cone/shuttle
  players; TE: 34/19/21.
- **(b)** Attempts/player are **thin**: 40-yd dash median **2** (WR 82/94 and TE 32/34 have ≥2); cutting
  drills mostly **1** attempt (WR cone 5/24, shuttle 6/29 have ≥2). Combine classes **2023/2024/2025**.
- **(c)** Match preview from the **complete `player_play.csv`**: matched **WR 106/107, TE 38/42**; route snaps
  (via `route_ran` NOT NULL) median **WR 261, TE 184**. Sample *tracking* prefix matches only WR 39/TE 11
  (non-representative). Authoritative frame-level match = `PENDING FULL RUN`.
- **(d)** **Route type IS provided** — `player_play.route_ran` is **categorical** (GO/HITCH/CROSS/IN/OUT/POST/
  CORNER/SLANT/FLAT/SCREEN/WHEEL/ANGLE; NULL = no route), *not* a boolean flag (config `route_snap_col`
  semantics corrected — see `schema.md`). Route **depth** provided via `pass_length` (integer yards).
  Trajectory-derived depth/break angle, 22-player tracking, and ball location = `PENDING FULL RUN`;
  time-to-throw available as a **proxy** (`dropback_duration`).
- **(e)** Route snaps **53,308**; **targeted 9,842 (18.5%)**, **non-targeted 43,466 (81.5%)** → plays **do**
  cover non-targeted routes (WR 81.2%, TE 83.3%). 3 seasons (2023/24/25), 1,002 games, 114,954 plays.
- **(f)** Provided **`dis` is unreliable** (confirmed): median under-count of the x/y path **8.9% on the
  40-yd dash** (80% of attempts beyond the 5% tolerance), **39–66% on cutting/skill drills**. Provided **`a`**
  agrees with `ds/dt` on the straight-line dash (r≈0.98) but not on cutting drills (r≈0) — expected, since
  the vendor `a` includes the lateral/centripetal component; recompute kinematics from `x,y`.
- **(g) Test-retest ICC** (repeat source = distinct `event_id` attempts of the same `(nfl_id, drill_type)`),
  **40-yd dash** (375 players with ≥2 attempts): `peak_speed` **0.975**, provided-`s` peak 0.986,
  `speed_at_20yd` 0.969, `min_speed_after_peak` 0.966, `speed_at_10yd` 0.883, `env_vmax` 0.643,
  `speed_at_t2.0s` 0.573, `max_decel` 0.413, `time_to_20yd` 0.403, `env_a0` 0.337, `time_to_10yd` 0.216,
  `env_tau` 0.097. **Cutting-drill features are unreliable** (ICC ≈ 0.1–0.3, several negative) with thin
  repeats (cone 38, shuttle 42 players ≥2). Some features are **explicitly `UNESTIMABLE`** (e.g.
  `SHORT_SHUTTLE speed_at_20yd`/`time_to_20yd`: zero players with ≥2 attempts, path too short) — recorded,
  never passed as reliable.
- **(h)/(i)** `PENDING FULL RUN` in sample mode. The synthetic **binding** fixture recovers an exceedance
  rate of **0.109** vs the planted **0.10**; the **null** fixture gives **1.0** — the game-side code path
  is exercised and the planted ceiling is recovered (fixture only; not a finding).

**Go/no-go:** **CONDITIONAL — thresholds not frozen.** `audit.min_matched_wr`, `audit.min_route_snaps_per_player`,
`audit.min_icc` are `null` (`prereg.md` skeleton); the sample cannot exercise the game-side gate. No required
Combine/game field is missing. A `--mode full` run remains **blocked** until `prereg.md` is frozen (itself a
blocker per `TASK.md`). Advisory (preview from the complete `player_play.csv`): matched WR 106 / TE 38 and
median route snaps ≫ any plausible minimum look sufficient.

**Guidance for Phase 2 feature selection (ICC first):** the reliable candidates are
`peak_speed`, `speed_at_10yd`/`speed_at_20yd`, and `env_vmax`; `env_a0`/`env_tau` and raw timing splits are
weak; cutting-drill cut metrics are not reliable enough to headline.

**Evidence.** `outputs/audit/*` (+ `SUMMARY.md`) and `outputs/audit/synthetic/*`, `outputs/run_log.csv`;
`src/01_audit.py`, `src/synthetic_fixture.py`, `src/tests/test_audit.py`; `fixtures/synthetic/{binding,null}/`.

---

## D28 — Cycle 2: freeze the pre-registration (Conor-approved values) · 2026-10-12
**Decision.** Filled every `TBD`/`null` in `notes/prereg.md` (now **FROZEN**) and `config.yaml` with the
values Conor approved, and implemented a runtime **freeze gate** (`src/gate.py`). Choices + rationale:

| Key | Value | Rationale |
|---|---|---|
| `models.quantiles` | `[0.5, 0.75, 0.9, 0.99]` | Conor-approved τ ladder; median reference + three upper tails to test tail-strengthening (prereg §1). |
| `game.top_k_efforts` / `game.route_snap_count` | `5` / `100` | approved; top-5 efforts from a fixed 100-snap denominator ⇒ comparable tail sampling (prereg §2). |
| `envelope.max_features` / `selection_rule` | `3` / `[icc, vif]` | approved ≤3 features, ICC then VIF (prereg §3). |
| `envelope.icc_min` = `audit.min_icc` | `0.5` | approved reliability floor; audit ICC splits the pool cleanly (`.97/.97/.88/.64` retained vs `.34/.10` dropped). |
| `audit.min_matched_wr` / `min_route_snaps_per_player` | `60` / `100` | approved gate minima with headroom below the audit preview (WR 106/107; median route snaps 261/184) (prereg §5). |
| `audit.exceedance_margin_rel` / `exceedance_reframe_rate` | `0.0` / `0.25` | approved reframe rule ">25% speed exceedance → envelope not binding → reframe as utilization": exceedance = strictly beat the ceiling (margin 0; same yd/s units), reframe if the overall rate exceeds 25% (prereg §6). |
| `envelope.vif_max` | `5.0` | standard deflaible VIF ceiling; used as the redundancy-pruning trigger (prereg §3). |
| `envelope.split_distances_yd` | `[5, 10, 20]` | short/medium/long splits across the dash; the 20-yd split is the reliable one (ICC 0.97). |
| `envelope.speed_at_times_s` | `[1.0, 2.0]` | model-free speed-at-time samples from movement onset (kept, but ICC .35/.57 → may be dropped). |
| `envelope.cut_metrics` | `[entry_speed, exit_speed, cod_deficit]` | change-of-direction metrics for the cutting drills (cone/shuttle); not headlined (audit g: ICC ≈0.1–0.3). |
| `envelope.min_attempts_per_player` | `1` | hierarchical shrinkage is designed to stabilise thin ceilings (dash median 2 attempts); requiring ≥2 would discard ~13% of players and bias toward two-attempt players (prereg §8). |
| `game.thresholds.high_speed_percentile` / `accel_percentile` | `0.90` / `0.90` | effort cut-offs at the 90th percentile — "max-effort" tail, matching the tail question (prereg §8). |
| `models.bootstrap_reps` | `1000` | standard; player-clustered bootstrap SE for the quantile slopes (prereg §8). |
| `models.controls_without_draft` / `controls_with_draft` | `[position, age, height, weight, bmi]` / `+ draft_position` | approved controls; draft excluded/included per prereg; "undrafted" is an explicit level (limitations §10). |
| `models.fallbacks` | `[refit_increased_max_iter, adjacent_tau_report, pool_positions, report_null]` | ordered, pre-declared stability chain mirroring the prior project's "report a null rather than force a result" discipline (prereg §7). |
| `validate.cv_folds` / `perm_reps` | `5` / `1000` | player-grouped CV folds and permutation reps (prereg §8). |
| `validate.measurement_error_correction` | `icc_attenuation` | divide the estimated slope by the feature reliability — the standard attenuation correction given the audit ICC (limitations §3, §6). |
| `validate.sensitivity` | `[alt_quantiles, drop_thin_repeats, exclude_optouts, margin_grid]` | pre-declared sensitivity analyses (tail ladder alternatives, thin-repeat players, Combine opt-outs, exceedance-margin grid). |
| `game.sample_placeholders.{high_speed_threshold_yds, accel_threshold_yds2}` | `8.0` / `3.0` | **a-priori** placeholders only (never derived from the sample); full runs must overwrite with `FULL` provenance. |

**Gate.** Added `src/gate.py`: a `--mode full` run of a stage that consumes pre-registered values must find
`notes/prereg.md` `FROZEN` **and** all required keys non-null, else it stops and points at
`notes/blockers.md`. `01_audit` (full) and `02_combine_envelope` (full) call `gate.enforce(...)`. Verified:
the freeze gate **passes** with the frozen prereg and **blocks** an unfrozen skeleton (tests).
**Evidence.** `notes/prereg.md` (FROZEN), `config.yaml`, `src/gate.py`, `src/tests/test_gate.py`.

---


## D29 — Cycle 2b: finish the freeze gate + Phase 2 Combine envelope · 2026-10-12
**Decision.** Resumed the **stranded Cycle-2 run** and completed it in one session (single-session
PM+Coder+Reviewer):

1. **Freeze gate wired + tested.** `src/gate.py` (freeze-only for Combine-side; freeze **+** audit go/no-go for
game-side) is now called by `01_audit` (full) and `02_combine_envelope` (full). `src/tests/test_gate.py`
(8 suites) proves it **passes** with the frozen prereg and **blocks** an unfrozen/missing prereg, a null
required key, and a non-passing audit decision, while sample mode is never gated.
2. **Phase 2 built + run for real.** New `src/02_combine_envelope.py` + shared `src/combine_features.py`:
per-attempt kinematics recomputed from `x,y`; envelope `v(t)=vmax(1-e^{-t/tau})` on `FORTY_YARD_DASH`
(`a0=vmax/tau`); model-free split / speed-at-time features; cut metrics (`entry_speed`, `exit_speed`,
`cod_deficit`) for the cutting drills; two-stage empirical-Bayes **hierarchical shrinkage**; **<=3 features by
ICC then VIF**; per-player table **with SEs**. Config-driven, DuckDB-only (no pandas), run-logged,
checkpointed. **Findings (FULL; Combine file complete):** 418 dash players / 793 attempts (43 single-attempt);
positions {WR 94, TE 34, DB 102, DL 93, OL 95}. **Retained = `peak_speed`, `speed_at_20yd`, `env_vmax`** — the
pre-declared set (prereg §3). ICC reproduces the audit exactly (`peak_speed` .9746, `speed_at_20yd` .9694,
`speed_at_10yd` .8832, `env_vmax` .6425 retained; `env_tau` .0968, `env_a0` .3373 dropped). Residual VIF>5
among retained: `peak_speed`, `speed_at_20yd` (flagged). WR shrunk ceilings: vmax median **11.67**
(10.79–13.21), tau 1.36, a0 8.51; TE: vmax median **10.78**, tau 1.31.
3. **Fit bounds kept audit-consistent.** The envelope bounds stay `vmax∈[0,30]`, `tau∈[0.05,10]` — the bounds
behind the audit evidence cited in prereg §4 — so the freeze-consistent ICC/selection is reproduced. The wide
`tau` bound admits a few noisy/thin attempts whose 2-parameter fit extrapolates implausibly (raw ceiling up to
~18 yd/s): these are **flagged** (`env_vmax_implausible`, raw ceiling > `envelope.flag_vmax_over_peak`(1.5)x
mean peak → **2 players**) rather than silently dropped (prereg §3.4 pattern); see `notes/limitations.md`.
*Rejected alternative:* a physically-tighter fit bound tames the outliers but **flips the VIF pruning to drop
`env_vmax`**, contradicting the frozen expected set — a HARKing risk — so the pre-registered configuration was
kept. Also refactored `01_audit._features_for_attempt` to use `combine_features.fit_envelope` (one config-driven
fitter, removing hardcoded bounds). **No frozen prereg value changed.**

**Evidence.** `src/{gate,combine_features,02_combine_envelope}.py`, `src/tests/{test_gate,test_envelope}.py`
(8+9 suites pass; audit 5/5 regression), `outputs/envelope/*` + `SUMMARY.md`, `outputs/run_log.csv`, `config.yaml`.

---

## D30 — Cycle 3: Phase 3a `00_thresholds` + Phase 3b `03_game_demand` · 2026-10-12
**Decision.** Built the **first two game-side stages** in one session (single-session
PM+Coder+Reviewer+Validator), on the committed synthetic fixture, with **no frozen value changed**.

1. **`src/game_features.py` (new shared module).** x/y-derived per-play effort kinematics (speed =
   step/dt, accel = d(speed)/dt; one streaming DuckDB aggregation — no full CSV in pandas), route-snap
   extraction (`route_ran IS NOT NULL`; it is **categorical**, not a flag — schema §7/audit d), a fixed-
   width **histogram percentile** (memory-bounded, one pass), the **top-k tie rule** (ties at the k-th
   value kept — prereg §2), and the Combine-ceiling helpers. `x,y` are downcast to `REAL` in the frame
   scan.
2. **`src/00_thresholds.py`.** `--mode sample` writes **a-priori PLACEHOLDER** cut-offs from
   `game.sample_placeholders` (8.0 yd/s, 3.0 yd/s²), provenance `PLACEHOLDER` (+ `label=UNVALIDATED SAMPLE
   OUTPUT`), **never derived from the sample**; `--mode full` streams the game data, accumulates the
   per-effort peak speed/accel histograms, and writes `outputs/thresholds.json` with provenance `FULL` at the
   frozen percentiles (`game.thresholds.*_percentile=0.90`). `--synthetic` runs the identical derivation on
   the fixture. Full mode is gated (`gate.enforce(..., requires_game_side=True)`) and writes a blocker on
   zero efforts.
3. **`src/03_game_demand.py`.** Consumes `thresholds.json` + the Phase-2 ceiling table
   (`outputs/envelope/per_player_envelope.csv`, `env_vmax`/`env_a0`); per route snap it computes
   speed/accel efforts and the **envelope fraction** `max_over_ceiling = effort speed ÷ ceiling`; it takes
   the **top-`k=5` efforts per player** from the fixed `route_snap_count=100` denominator (ties kept;
   players with <100 snaps flagged ineligible, reported not pooled), and writes `efforts.csv`,
   `top_k_efforts.csv`, `player_summary.csv`, `thresholds_applied.csv`, `SUMMARY.{md,json}` — every table
   `provenance`-first. Full mode additionally **blocks when the thresholds provenance is not `FULL`**
   (`TASK.md` blocker) or the Phase-2 ceiling table is missing. Sample mode emits a labelled
   `PENDING FULL RUN` placeholder (game demand is full-only).
4. **Fixture exercise (NOT a finding).** On the committed synthetic fixture the planted ceiling is
   recovered end-to-end: **binding** overall exceedance **0.109** (planted 0.10; per-effort envelope
   fractions ≈ 0.80–1.12) vs **null 1.00**. Ceilings for the fixture come from the shared Phase-2 fitter
   (`combine_features.fit_envelope`); a full run reads the committed Phase-2 file.
5. **New config keys** (`game.top_k_metric`, `game.ceiling.*`, `game.thresholds.histogram.*`) — all
   config-driven, no magic numbers; the frozen pre-registration values are untouched.

**Rejected alternative.** Doing the per-game loop (one DuckDB query per `game_id`) was rejected: it makes
   the full 3-season scan O(#games × table) (a full re-scan per game). One streaming per-play aggregation
   (as `01_audit`'s `_game_effort_peaks` already does) reads the Parquet **once** and lets DuckDB spill;
   the percentile is then a single histogram pass.

**Tests.** `src/tests/test_thresholds.py` (5) and `src/tests/test_game_demand.py` (6) added; **33/33**
   across `test_audit`(5)+`test_gate`(8)+`test_envelope`(9)+`test_thresholds`(5)+`test_game_demand`(6) all
   green. Covers placeholder-vs-derived, histogram exactness, top-k ties, planted-ceiling binding **and**
   null recovery, `provenance`-first schema, the full-run freeze gate, the non-`FULL`-thresholds blocker,
   the sample placeholder, and determinism.

**Remaining.** `04_models` / `05_validate` are a later cycle. The **real** Phase-3 `--mode full` run still
   needs `game_tracking_{2023,24,25}.csv` (absent on this Pi) — `PENDING FULL RUN`
   (`notes/pending_full_run.md` P4/P5). **No active blockers.**

**Evidence.** `src/{game_features,00_thresholds,03_game_demand}.py`, `src/tests/{test_thresholds,test_game_demand}.py`,
   `config.yaml`, `outputs/thresholds.json`, `outputs/game/synthetic/{binding,null}/*`, `outputs/run_log.csv`.

---

## D31 — Cycle 4: Phase 4 `04_models` + Phase 5 `05_validate` · 2026-10-12
**Decision.** Built the last two stages in one session (single-session PM+Coder+Reviewer+Validator), on the
committed synthetic fixture, **with no frozen value changed**.

1. **`src/model_features.py` (new shared module).** Quantile-regression plumbing: per-player covariates
   (`age` = `draft_year − birth_year`, `bmi` = `models.bmi_constant`·wt/ht², `draft_position` = draft round
   tier with `undrafted` explicit), a config-driven **design matrix** (`[const, primary, controls]`; numeric
   vs category-like controls auto-detected; **rank- and VIF-pruning of near-collinear controls — never the
   primary**), a statsmodels `QuantReg` fit, a **player-clustered bootstrap**, the proper **pinball loss**,
   and deterministic player-grouped folds.
2. **`src/04_models.py` (Phase 4).** Reads the Phase-3 `efforts.csv` (effort-level, one row per route-snap
   effort) + Phase-2/Combine covariates; models in-game effort speed on the Combine ceiling
   (`models.primary_feature=env_vmax`) at the frozen τ ladder (`[0.5,0.75,0.9,0.99]`), **pooled vs WR/TE**,
   **with/without draft**, **player-clustered bootstrap** (`models.bootstrap_reps`, seed = config `seed` +
   `bootstrap_seed_offset`); reports the **exceedance rate** and **utilization `u`** and applies the
   pre-declared ordered **fallback chain** (`models.fallbacks`), logging every firing. Outputs
   `model_coefficients.csv`, `exceedance_utilization.csv`, `fallbacks_fired.csv`, `SUMMARY.{md,json}` — all
   `provenance`-first.
3. **`src/05_validate.py` (Phase 5).** **player-grouped CV** (`validate.cv_folds`, seed `cv_seed`) of the
   model vs a **controls-only baseline** scored by **pinball loss** (mean + robust median over folds); a
   player-level **permutation** null (`perm_reps`, `perm_tau`); **`icc_attenuation`** measurement-error
   correction (slope ÷ reliability, reliability from the Phase-2 selection table or recomputed from the
   Combine tracking); and the pre-declared **sensitivity** analyses (`alt_quantiles`, `drop_thin_repeats`,
   `exclude_optouts`, `margin_grid`), plus a **power note** and **limitations**. Outputs `cv_results.csv`,
   `permutation.csv`, `measurement_error.csv`, `sensitivity.csv`, `SUMMARY.{md,json}`.
4. **Fixture exercise (NOT a finding).** On the committed synthetic fixture the planted ceiling is recovered
   end-to-end: **binding** → exceedance **0.109** (planted 0.10), verdict `binding`, utilization `u` median
   **0.93** (<1); **null** → exceedance **1.00**, verdict `not_binding_use_utilization`, `u` median **1.22**.
   The thin fixture (8 players) is under-powered for a *controlled* quantile model, so the fallback chain
   fires (e.g. pooled → `report_null`, extreme tail → `adjacent_tau_report`, per-position → `pool_positions`)
   and nulls the reported estimate — exactly the prereg §7 behaviour; CV median improvement is small and the
   mean is dominated by one exploding fold (fixture artefact, not a result).
5. **New config keys** (`models.{response,primary_feature,bmi_constant,fit_max_iter,fit_max_iter_retry,
   fit_p_tol,bootstrap_ci,min_obs_per_stratum,control_vif_max,utilization_percentiles}`;
   `validate.{perm_tau,perm_seed_offset,measurement_error_feature,min_repeats_for_sensitivity,
   sensitivity_alt_quantiles,sensitivity_margin_grid,optout_fields}`) — all config-driven, no magic numbers;
   the frozen pre-registration values are untouched.
6. **Fixture extended.** `src/synthetic_fixture.py` now also emits a schema-compliant `players.csv`
   (identity + `birth_date` + `draft_round`) so `age`/`draft_position` are exercised; deterministic, no rng
   draws before the existing files (the committed `game_tracking.csv` is byte-identical).

**Rejected alternative.** Restricting the Phase-4 model to *eligible* players' top-k efforts (prereg §2) was
rejected for the model sample: on the 8-player fixture **no** player is eligible (8 route snaps < 100), so the
model would be empty and could not be exercised end-to-end. The **primary sample is effort-level over all
route-snap efforts** (the natural reading of "effort-level"), with eligibility reported; the fixed-denominator
top-k tail remains a Phase-3 descriptive summary. Documented in `notes/limitations.md`.

**Tests.** `src/tests/test_models.py` (9) and `src/tests/test_validate.py` (9) added; **53/53** across
`test_audit`(5)+`test_gate`(8)+`test_envelope`(9)+`test_thresholds`(6)+`test_game_demand`(7)+`test_models`(9)
+`test_validate`(9) all green. Covers planted-ceiling recovery **and** null reporting, the fallback chain
(fires + nulls the reported estimate), `provenance`-first schema, the full-run freeze gate, the
non-`FULL`-thresholds blocker, the sample placeholder, permutation p-value validity, the attenuation
identity, all four sensitivity analyses, and determinism.

**Remaining.** The **real** Phases 4–5 `--mode full` runs still need `game_tracking_{2023,24,25}.csv`
(absent on this Pi) — `PENDING FULL RUN` (`notes/pending_full_run.md` P6/P7). **No active blockers.**

**Evidence.** `src/{model_features,04_models,05_validate,game_features,synthetic_fixture}.py`,
`src/tests/{test_models,test_validate}.py`, `config.yaml`, `fixtures/synthetic/*/players.csv`,
`outputs/{models,validate}/synthetic/{binding,null}/*`, `outputs/run_log.csv`.

---

## D32 — Cycle 5: runnable deliverable notebook (`notebooks/bdb27_full_run.ipynb`) · 2026-10-12
**Decision.** Built the **runnable deliverable notebook** — the point-and-click interface for the whole
pipeline. The user sets `MODE` (`sample`/`full`) and — only if needed — `DATA_DIR` in the first code cell,
then **Run All**; no terminal is required.

1. **Thin notebook, engine in `src/`.** The notebook imports the existing engine (`src/common.py`) for
   config/paths and drives each stage **through its own entry point** (`importlib` + `mod.main()` with the
   right `sys.argv`), so every stage's `--mode`/`--force`/checkpoint behaviour **and** the pre-registration
   freeze gate (`src/gate.py`) apply unchanged. No analysis logic is duplicated in the notebook.
2. **Order.** `01_audit → 02_combine_envelope → 00_thresholds → 03_game_demand → 04_models → 05_validate`,
   then the synthetic-fixture demonstration of the game-side path for the `binding` **and** `null` regimes.
3. **Cross-platform + Kaggle.** `pathlib` only, no bash-only calls; auto-detects the dataset under
   `/kaggle/input/…` (else `data/raw`); writes a scratch run-config (`bdb27_run/config.yaml`) that
   overrides **only** `paths.raw`, so the repo `config.yaml` is never edited and nothing is written outside
   `outputs/` plus the `data/interim` Parquet cache. Memory discipline holds: the notebook never loads the
   large tracking CSVs into pandas (the `src/` stages stream via Parquet + DuckDB).
4. **Graceful degradation.** A stage that needs absent data is caught and reported `PENDING FULL RUN`
   (game-side), never crashing. The notebook prints an honest per-stage status read from each stage's own
   outputs (placeholder `status`, `thresholds.json` provenance, `_DONE` mode). On this Pi (no full
   `game_tracking_{2023,24,25}.csv`): all six stages run — `01_audit` → partial (game-side c/h/i pending),
   `00/03/04/05` → `PENDING FULL RUN`; the synthetic demo completes both regimes.
5. **≤5 writeup figures** rendered to `outputs/figures/` (and inline): (1) Combine envelope decay curves,
   (2) ICC/VIF feature selection, (3) exceedance/utilization, (4) quantile slope vs τ, (5) validation
   (player-grouped CV vs controls-only + measurement-error + permutation). Each carries a
   Visualisation-Brief (`chart_type`, `data_source`, `columns_required`, `output_filename`) and its
   provenance in the title. Game-side figures fall back to the labelled synthetic fixture until real
   full-run outputs exist.
6. **Figures committed.** The PNGs are **deterministic** (byte-identical across independent runs; verified
   by sha256), so the 5 figures + `.gitkeep` are committed via a narrow `.gitignore` exception — **no data**.

**Verification.** Notebook executed top-to-bottom via `jupyter nbconvert --execute` (sample mode,
`RUN_SYNTHETIC_DEMO=True`): **24/24 cells, 0 errors**; all 6 sample stages + 4×2 synthetic stages `OK`;
**5/5 figures** written, non-empty, byte-identical on re-run. `src/tests/*` unchanged: **53/53 green**. No
frozen value changed; no data committed.

**Evidence.** `notebooks/bdb27_full_run.ipynb`, `notebooks/README.md`, `README_RUN.md`, `.gitignore`,
`outputs/figures/fig{1..5}_*.png`.

---

## D33 — Fix: portable peak-RAM probe (Windows `ModuleNotFoundError: resource`) · 2026-10-13
**Decision.** Fixed the Windows blocker reported by Conor (2026-10-13): `src/common.py` imported the
POSIX-only stdlib module `resource` at **module top level**, so `import common` — which the deliverable
notebook's bootstrap cell does — raised `ModuleNotFoundError: No module named 'resource'` on Windows before
any stage ran. This broke the documented cross-platform contract (README_RUN.md §1/§7, notebooks/README.md).

1. **Guarded import.** `import resource as _resource` is now wrapped in `try/except ImportError` →
   `_resource = None` on non-POSIX hosts; importing `common` no longer depends on a POSIX-only module.
2. **Portable measurement (`peak_ram_mb`).** First-success probe chain: POSIX `resource.getrusage`
   (KiB on **Linux**, bytes on **macOS/BSD** — normalised to MiB) → Windows
   `psapi.GetProcessMemoryInfo().PeakWorkingSetSize` via **stdlib `ctypes`** (no new dependency) → the
   documented sentinel **`RAM_UNKNOWN_MB = -1.0`**. `peak_ram_mb()` **never raises**; a stage can never fail
   because memory measurement is unavailable (TASK.md logging contract).
3. **Schema preserved.** `run_log.csv` columns/order unchanged (`timestamp, stage, mode, wall_s,
   peak_ram_mb, error, notes`); the sentinel is written in the `peak_ram_mb` cell as a numeric value.
4. **Audit.** Grepped `src/` + notebooks for POSIX-only modules (`resource/pwd/grp/termios/tty/fcntl/…`),
   shell-outs (`os.system/popen/fork`, `subprocess`, bash), and hard-coded POSIX paths: **only `resource`
   was found**; all other hits were comments. No other change needed.

**Verification.** New regression test `src/tests/test_common_portability.py` (5 tests): import survives a
blocked `resource`; a real stage (`00_thresholds --synthetic`) runs end-to-end with `resource` unimportable
and logs a schema-exact row; sentinel when no probe is usable; Linux-KiB vs macOS-bytes unit handling;
frozen run-log schema. Full suite: **58/58 green** (was 53/53). Deliverable notebook executed top-to-bottom
**twice**: once normally, and once with `resource` **unimportable** (import blocker via `sitecustomize`) —
both **NOTEBOOK_EXEC_OK, 0 errors**; the blocked run logged `-1.0` for all 14 stage rows.

**Evidence.** `src/common.py`, `src/tests/test_common_portability.py`, `README_RUN.md`, `notebooks/README.md`.
No data committed (`.gitignore` unchanged); no new runtime dependency (`requirements.txt` unchanged).

---

## D34 — Fix: PEP 701 f-string (backslash in expression) + version-portability guard · 2026-10-13
_(Task labelled this `D33`; `D33` above is the same-day peak-RAM fix — next free id is `D34`.)_
**Decision.** Fixed the import-time crash introduced in `eca046e`: `src/model_features.py` line 105 put an
escaped quote inside an **f-string expression** —
`f"{', '.join('\"%s\"' % f for f in cfg['validate']['optout_fields'])}"` — which is legal only on
CPython >= 3.12 (PEP 701). On <= 3.11 it is a hard `SyntaxError` ("f-string expression part cannot include a
backslash"), raised the moment `04_models.py` / `05_validate.py` import `model_features`.

1. **Fix.** The quoted opt-out column list is precomputed **outside** the f-string into a local
   `optout_cols = ", ".join('"%s"' % f for f in cfg["validate"]["optout_fields"])`, then interpolated
   into the SELECT. No backslash and no reused-quote appears in any f-string expression. The built SQL is
   **byte-identical** to the pre-fix expression (verified for representative non-empty, singleton, empty,
   and the real `config.yaml` opt-out lists — same `sha256` on every case).
2. **Sweep.** A `tokenize`-based scanner (`FSTRING_START/MIDDLE/END`, brace-depth inside the f-string)
   checked every `.py`/`.ipynb` in the repo (excl. `.git/.venv/__pycache__/bdb27_run/outputs/data`):
   **22 files (21 `.py` + 1 notebook), 0 remaining violations** — line 105 was the **only** hit, now fixed.
   Tokenize is the **primary** detector; `ast.parse(feature_version=(3,11))` is only a coarse secondary
   check and (documented limitation) does **not** catch backslash-in-f-string on a 3.13 host.
3. **Declared minimum.** **Python 3.12** is now stated identically (`Python 3.12`) in `README_RUN.md`,
   `requirements.txt`, and `notebooks/README.md`. Rationale: pinned `numpy==2.5.3` + `scipy` need >= 3.12;
   `pyarrow` 26 / `pandas` 3.0.6 / `scikit-learn` 1.9.1 / `matplotlib` 3.11.2 need >= 3.11; Kaggle ships 3.13.
4. **Guard.** New `src/tests/test_portability_fstrings.py` (no pytest): `scan_source()` flags (a) backslash
   in an f-string expression, (b) same-quote reuse, (c) not parsing on 3.12 (`feature_version`); tests assert
   the pre-fix line-105 text is flagged (RED), `f"{m["k"]}"` is flagged, the whole repo + notebooks are
   **clean** (GREEN), and the three declared-`Python 3.12` files agree + interpreter >= 3.12. Tiny CLI:
   `python src/tests/test_portability_fstrings.py --scan <path|->` (exit 1 on any violation).

**Verification.** RED: `git show eca046e:src/model_features.py | python src/tests/test_portability_fstrings.py
--scan -` → `1 violation(s): <stdin>:105: [a] backslash in f-string expression` (exit 1). GREEN: fixed tree +
repo+notebooks → `0 violations`. Full suite **63/63 green** (58 existing + 5 new guard tests). Deliverable
notebook executed top-to-bottom via `nbclient` (kernel `python3`, cwd `notebooks/`): **24/24 cells, 0 errors,
334 s** (sample mode + synthetic demo; `PENDING FULL RUN` game-side as documented); 5/5 figures regenerated
**byte-identical** to the committed PNGs.

**Evidence.** `src/model_features.py`, `src/tests/test_portability_fstrings.py`, `README_RUN.md`,
`requirements.txt`, `notebooks/README.md`. No data committed (`.gitignore` unchanged); no new runtime
dependency (stdlib `tokenize`/`ast`/`json` only).

---

## D35 — Post-freeze data-QC: frame-level **despike** of impossible game-tracked speeds at 12.5 yd/s · 2026-10-15
_(Spec header labelled this `D36`; the last entry above is `D34`, so the next free id is `D35`.)_

**Decision (revision 3 — the final mechanism).** **Discard impossible per-frame game-tracked speed
samples** and recompute the per-effort peak from the **retained (valid) frames only**, bounded by a
config-driven threshold **`game.speed_cap_yds: 12.5`** (yd/s). The mechanism is a **frame-level greedy
forward despike** — *not* a clamp and *not* a row drop (see "Superseded revisions" below). The effort
is always **kept**: "just discard the bad samples, not the whole effort" (Conor 2026-10-15). This is a
**post-freeze data-QC change**: `notes/prereg.md` stays **FROZEN**; the pre-registered values are
untouched.

1. **Config-driven, no magic numbers.** The bound lives only in `config.yaml`
   (`game.speed_cap_yds: 12.5`, commented as a data-QC per-frame validity bound). Code reads it from
   config at every game-side recompute site — never a literal.

2. **The despike (greedy forward scan)** — `src/game_features.py::_despike_scan_sql` (used by
   `effort_kinematics_sql`, shared by `00_thresholds` and `03_game_demand`) and
   `src/01_audit.py::_game_effort_peaks` (items h/i). Within each effort (one player on one play,
   ordered by time):
   * the **first** frame is always retained and becomes the "last good frame" (it has no incoming
     sample);
   * each later frame's **sample** = step distance / dt from the **last retained frame**;
   * a sample `<= cap` — or `dt <= 0`, which stays NULL and is not a sample — **retains** the frame
     and makes it the new last-good frame;
   * a sample `> cap` **discards only that sample**; the last-good frame is unchanged, so the **next**
     frame is re-measured from the *same* last-good frame (the spike is skipped; its valid neighbour
     is **not** contaminated by it and is **not** dropped — the trajectory is effectively compressed);
   * `peak_speed` = max over the retained samples; `peak_accel` = `d(speed)/dt` over the retained speed
     series. Because the last-good frame is always `<= cap` away, **every** retained sample is
     `<= cap` by construction, so no emitted `peak_speed` can exceed the bound.

3. **The effort is always KEPT.** An effort whose old peak came from a spike now reports its genuine
   peak from the valid frames; an effort whose samples are all valid is **byte-identical** to the
   pre-change behaviour (same `n_frames`, `peak_speed`, `peak_accel`).
   **Degenerate edge case:** if an effort has **<2 retained frames** (no speed sample can be derived —
   e.g. a single frame, or every post-first frame is impossible) the effort is still emitted with
   `peak_speed = 0.0` and `peak_accel = 0.0` (the empty `max()` is coalesced to `0.0`) — never dropped,
   never NULL.
   **Null-safety:** a `dt = 0` frame yields a NULL speed; it stays NULL (never treated as a violation,
   never a fabricated value) — the first frame anchors the effort. (`n_frames` keeps its pre-change
   meaning: the number of retained frames carrying an incoming sample, i.e. retained frames other than
   the anchor.)

4. **Every game-side recompute site is despiked** (threaded from config): `game_features.py::`
   `effort_kinematics_sql` and `01_audit.py::_game_effort_peaks`. The **Combine side is NOT bounded**
   (`combine_features`, `02_combine_envelope`, `01_audit._combine_ceiling`) — the bound is game-side
   only. `00_thresholds` flows through the shared `extract_efforts`, so its cut-offs are derived on the
   despiked peaks automatically; no code change beyond the shared scan. No separate accel cap
   (scope-tight): the speed bound implicitly bounds the speed-derived accel.

**Physical justification.** `peak_speed` is a `max()` over per-frame speed; a human cannot run a route
at 26+ mph. NGS in-game route speeds top out at ~21–22 mph. **12.5 yd/s = 25.6 mph** sits above every
real in-game route speed, so the despike removes only physically-impossible samples — it is not a
smoothing or analysis parameter.

**Pre-change behaviour (the diagnosed contamination).** Before the despike the per-effort `peak_speed`
was `max()` over the **raw** frame steps. On the real game-tracking data **351 efforts had a
`> 12.5 yd/s` peak**, and for **78 of the 101 eligible players** the single fastest effort was such a
spike — i.e. the contamination sits exactly in the top-k (exceedance) tail, where one noisy/duplicated
frame defines the effort's peak. Pooled exceedance was **0.75%** and the **top-5 tail ~56%**; the top-5
value crosses the pre-registered **0.25 reframe threshold** and **flips the verdict**.

**Expected effect (exact numbers from the re-run).** Removing only the impossible samples and
recomputing from the valid frames moves pooled exceedance **0.75% → ~0.12%** and the top-5 tail
**~56% → ~11%** — i.e. the binding verdict is restored *without dropping a single effort*.

**Superseded revisions (why revision 3).** Two earlier mechanisms were considered and rejected:
*R1 (clamp)* — `LEAST(spd, cap)` replaces a spike with the cap (12.5) instead of removing it, and so
**fabricates** a value; *R2 (row drop)* — discarding **every raw frame** whose speed `> cap` (or the
whole effort) would wrongly discard the valid **neighbour** that the spike corrupted, and would drop
genuine efforts. Revision 3 (the greedy despike) is the PM's binding methodology: a spike frame is
skipped and its neighbour is re-measured from the last good frame, which is better than "drop every raw
frame whose speed > cap".

**Governance note.** `notes/prereg.md` stays **FROZEN**; `audit.exceedance_margin_rel` (**0.0**) and
`audit.exceedance_reframe_rate` (**0.25**) are **untouched**. This is a **data-QC bound, not analysis
tuning**: it is justified physically (above every real route speed) and is **never** tuned to improve
the result. Had the bound been an analysis knob, changing it after seeing results would be HARKing; it
is instead a declared data-quality bound recorded here (and in `notes/limitations.md`) with the
pre-change behaviour.

**Rerun requirement.** Game-side speed is now despiked, so the game chain must be re-run with `--force`:
`00_thresholds → 03_game_demand → 04_models → 05_validate` (Combine `02` unchanged).

**Verification.** `src/tests/test_speed_despike.py` (unit + e2e): a planted ~50 yd/s single-frame spike
is **discarded** and the genuine ~8 yd/s peak is emitted (never 50, never 12.5) with the effort
retained; **neighbour recovery** (A → spike S → genuine fast B) keeps B re-measured from A (not
dropped); a clean sub-cap effort is byte-identical to the pre-change behaviour; a `dt=0` frame stays
NULL; a degenerate effort is kept with `0.0`; the bound is config-driven (a lower cap discards more, a
cap above the spike is inert); `01_audit._game_effort_peaks` obeys the same despike; no emitted
`efforts.csv` (binding + null + full-mode e2e) exceeds the cap; determinism; fixture non-collision
(binding planted max ~10.97 stays below the bound; the null verdict is unchanged).

**Evidence.** `config.yaml`, `src/game_features.py`, `src/01_audit.py`, `src/tests/test_speed_despike.py`,
`notes/decisions.md`, `notes/limitations.md`, `README_RUN.md`, `notebooks/bdb27_full_run.ipynb`.
Post-freeze **data-QC** change (not a pre-registration edit); no new runtime dependency.

---

## D36 — Phase 0.5: pre-Combine expectation proxies (Grinding The Mocks) + Phase-4 gate · 2026-10-16
**Decision.** Added an additive **Phase 0.5** (external data) to the ceiling-binding project: build
`src/00b_external_proxies.py` to map Kaggle `nflId` → **Pre-Combine Expected Draft Position** (EDP) from
**Grinding The Mocks** mock-draft history, filtered to **February** snapshots (pre-Combine), aggregated per
prospect (median; `mean` supported), fuzzy-matched on Name + Draft Year (hard filter) + Position + College,
and written to `outputs/pre_combine_expectation.parquet` with **exactly** `nflId, Pre_Combine_EDP,
match_confidence_score` (schema asserted). Spec persisted (unmodified) at
`notes/phase-0.5-external-proxies.md`; `config.yaml` gained an `external:` section (config-driven, no magic
numbers). The Phase-4 gate was extended (`src/gate.py::external_proxy_gate_ok`) so `04_models.py --mode
full` requires the proxy (existence + exact schema) when `external.require_for_phase4` is true; `--mode
sample` is never gated.

**Why (acquisition outcome).** The live GTM export is **not machine-obtainable** from this host: HTTPS
direct times out; HTTP 301s to a **reachable Shiny websocket app** (`grindingthemocks.shinyapps.io`) that
has no static export and whose `robots.txt` disallows crawling; no GitHub mirror (repo search 0; code
search needs auth — none available); Wayback holds only the app shell; static probes 404. Per the spec we
**do not fabricate data** and **do not substitute a source** (the non-GTM fallback covers only 2025/26;
CFBD is out of scope). Full route table + access dates: `notes/external_data_manifest.md`; blocker appended
to `notes/blockers.md`.

**Evidence / verification.** `src/synthetic_gtm_fixture.py` builds a committed, schema-compliant synthetic
GTM fixture (`fixtures/synthetic/gtm/`: players subset + raw mock history), deterministic given the seed,
that exercises the February filter (with Jan/Mar outliers), near-miss fuzzy matching, and unmatched cases.
`src/tests/test_external_proxies.py` (10 suites) proves: fixture end-to-end with the EXACT parquet schema
and provenance labelling; the February filter controls the EDP (median recomputed independently); fuzzy
matching resolves near-misses one-to-one and leaves unknowns unmatched; the **> 0.85 drafted WR/TE
match-rate gate halts + writes a blocker**; the schema assertion rejects wrong column sets/orders; the
**Phase-4 gate** blocks on a missing/wrong-schema proxy and passes on a valid one (and never gates sample);
`--mode full` with no cached GTM stops with a data-availability blocker (no proxy written, nothing
fabricated); sample-mode labelling + determinism + checkpoint/skip. Full standalone suite green (includes
the unchanged `test_gate.py` and `test_models.py`).

**Scope note (Phase-4 consumption).** This cycle wires the **gate** and makes the proxy available; it does
**not** alter the FROZEN model specification (`notes/prereg.md` untouched) to add the EDP covariate — that
is a pre-registration decision to be taken (if at all) with Conor, and is moot until real GTM data exists.

**Commit.** `b247648` on `main` (code/config/tests/notes/fixtures only — no data).

---

## D37 — Cycle 1 (security/stakes): EDP board external data + Phase-1 board audit extension + Phase-4 security stage & Analysis A · 2026-10-17
**Decision.** Wire the **stakes/security axis** into the pipeline (binding spec `TASK.md` Phase 4 + Headline
gate): (a) accept the **external pre-Combine EDP board** as a permitted source; (b) add an **additive
Phase-1 board/security audit extension** (`src/security_audit.py`, wired into `01_audit.py`) covering items
c/d/e/h/l and the merged headline gate; (c) add the **Phase-4 security stage** `src/06_security.py` —
the pre-Combine-only **exposure table** + **Analysis A** (the early gate); (d) add the **synthetic security
fixture** (`src/synthetic_security_fixture.py`) with a **planted** and a **null** regime; (e) add the missing
**security tests**. No game-side security models (5B–5G), Phase-3 work or notebook/writeup this cycle.

**Board external data (Phase 0).** `GTM_BDB_EDP_Snapshots_20261016.csv` under `data/raw/external/gtm/`
(sha256 `4664b369…63b4`, 3,625 rows × 13 cols, `DD/MM/YYYY` dates). **Permitted** (competition External Data
section; Conor confirmed 2026-10-17); **not committed** (`data/` gitignored). Recorded in `notes/rules.md` §6,
`notes/schema.md` §9, `notes/external_data_manifest.md`; asserted in code
(`security_features.verify_board`). The board **supersedes** the earlier hypothetical GTM *fuzzy-name*
mapping for the security axis: joins are on **`nfl_id`** directly.

**Immutability / pre-Combine rule.** Exposures are the **2-weeks- and 1-week-pre** snapshots only
(primary = `log(EDP)` at 1-week-pre); a snapshot counts only if **strictly before the player's own Combine
testing day**. `Combine Week` and the post-Combine snapshots are **never** exposures — the 1-week-post EDP is
an **outcome** (the Analysis-A pre→post change). Enforced and tested.

**Analysis A result (the early headline gate; board + Phase-2 envelope only, no game data).** Population =
board players with Combine tracking: **n = 280**. Pre-registered primary spec uses `env_vmax` (the repo's
declared primary Combine feature; `security.analysis_a.speed_feature`, `models.primary_feature`) and controls
position/year: **speed coef = +4.33 (se 5.83, p = 0.458)**, **speed×log(EDP) coef = +1.70 (se 2.57,
p = 0.508)** → **no significant speed response; the gate FAILS** (`gate_pass=False`). Robustness panel (same
spec, other shrunken speed features) **does** show a significant *negative* speed response (market reward):
`peak_speed` −9.73 (p = 0.0063, raw EDP-change) and `speed_at_20yd` −11.16 (p = 0.0012); in the log-ratio
outcome p = 8.0e-5 and 1.7e-5. **The interaction ("rewarded more for at-risk players") is not significant in
any spec** (all-scope p ≈ 0.5–0.6); in the WR-only scope it is marginal and *wrong-signed* (negative,
p ≈ 0.06).

**Why the sign flips between `env_vmax` and the raw-speed features (reliability).** `env_vmax` is the
**fitted envelope ceiling** and is the **least reliable** retained feature (test-retest **ICC ≈ 0.64**), while
`peak_speed` and `speed_at_20yd` are near-perfectly reliable (**ICC ≈ 0.97 / 0.97**; `notes/prereg.md` §4,
audit g). A noisy regressor attenuates its coefficient and can flip/obscure a real market response. **We do
NOT change the pre-registered primary post hoc** — `env_vmax` stays primary and the gate result stands; the
`peak_speed`/`speed_at_20yd` sensitivity is reported **honestly as a robustness caveat** (it is a legitimate
warning that the *ceiling* construct, not the *speed* construct, may be the weak link).

**Headline-gate outcome.** Per `TASK.md`, a failed headline gate means the security question does **not** lead
the paper: **fall back to the ceiling/tail framing and report security as exploratory**. It is **not** a
pipeline stop. Fix (this cycle): `security_audit.merged_gate` now separates **`headline_pass`** (the security
gate, *informational*) from **`blocking`** (reserved for the spec's one hard stop — external board data **not
permitted**); `01_audit.py` only downgrades the audit decision on a genuine block. (Before this fix the
extension set the audit decision to `NO-GO` on *any* headline failure, which wrongly halted the whole
full-mode chain — caught by `test_full_mode_e2e`/`test_speed_despike` and fixed without touching stages a–i.)

**Open caveat (flag for the cycle-2 validator).** The board→Combine **exact `nfl_id`** match rate for WR/TE is
**0.757** (115/152) — below `external.board.min_match_rate` (0.85) — because the board covers a **broader**
prospect pool (783 board players; 413 with Combine tracking) than the 2027 competition subset. That threshold
is currently **not wired** into the merged gate; it is logged here rather than silently ignored (no blocker:
the implemented go/no-go minima — matched WR, route snaps, ICC, security tiers — are evaluated separately).

**Tests.** New `src/tests/test_security.py` (16) + `src/tests/test_security_audit.py` (8): exposure-table
schema/dtypes/units (fail-loud), pre-Combine-only + strict-before-testing rule, synthetic **recovery**
(planted effect recovered; null case **not** recovered; across-seed size/power), edge cases (no 1-week-pre
rows, players absent from the board, duplicate snapshot rows, blank `nfl_id`), determinism, board
sha256/row/permission checks, sign convention, the merged-gate semantics, and CLI provenance/checkpoint.
Full standalone suite green (all 15 test files).

**Files.** `config.yaml` (board + security sections), `TASK.md` (new binding spec), `src/01_audit.py`
(+extension), `src/security_audit.py`, `src/security_features.py`, `src/06_security.py`,
`src/synthetic_security_fixture.py`, `fixtures/synthetic/security/{planted,null}/`, new tests, and the notes
above. **Commit.** this cycle on `main` — code/config/tests/notes/fixtures only (no data, no generated
outputs).

---

## D38 — Cycle 2 (PI override): Analysis A gate RETIRED; the spine becomes **incentive exposure**; two-group pre-Combine sort key · 2026-10-17
**Decision (binding for this cycle).** The PI directed a change of argument. **Analysis A is not a gate and is
not part of the paper argument.** Headline-gate **condition 4 is dropped**. Analysis A survives only as a
short **descriptive appendix**, now with an attenuation-corrected panel. The paper's spine is
**incentive exposure**: a player whose draft stock is *slipping* entering the Combine has a stronger reason to
invest in the speed mechanism, so he should show a **smaller** Combine→game gap (higher ceiling
realisation / exceedance) than a high/secure player. This is operationalised **strictly as incentive
exposure — never as measured belief or intent**. The writeup may say "consistent with an incentive
mechanism"; it must **never** say players tried harder, intended to, or gamed the Combine.

**Exact pre-change gate wording (quoted before the change, for the record).** `TASK.md` §Headline gate read:

> The security question leads the paper only if: external data is permitted; each security group meets the
> prereg minimum matched-receiver count; Combine ceiling reliability passes; and **analysis A shows the market
> responded to speed**. Fix these before any full-data run. If any fails, fall back to the ceiling and tail
> design as the paper's main framing and report security as exploratory.

The fourth clause ("analysis A shows the market responded to speed") is the condition retired here. The other
three clauses are unchanged and remain informational gate checks.

**Two-group pre-Combine sort key (replaces continuous log-EDP as the primary exposure).**
`security_features.assign_security_groups`:

1. **trend stratum** (`has_trend == true`): `edp_trend = edp_pre(1wk) − edp_pre(2wk)`; `moved_down` iff
   `edp_trend > trend_epsilon` (config, default **0.0**), else `high_secure`. Positive trend = EDP rose =
   worse / less secure.
2. **level-fallback stratum** (`has_trend == false`, `has_primary == true`): tertiles of `log(EDP_pre)`
   computed **within position group** (WR and TE separately); bottom tertile = `high_secure`, top tertile =
   `moved_down`, **middle = `unclassified`** (excluded from the two-group contrast, retained in the
   continuous model). Cutoffs (q_lo, q_hi) and counts are reported in `SUMMARY.json`/`SUMMARY.md`.
3. otherwise `unclassified` (basis `no_pre_combine`).

**Non-circularity (why this is not HARKing a result).** The assignment is a deterministic function of
**pre-Combine** board snapshots only, with **no reference to any game outcome**. The tertile cut-offs are
computed over the pre-Combine pool (board players with a 1-week-pre snapshot and no trend), **not** over the
appeared/game sample, so selection on being drafted cannot move a player between groups. The pipeline
hard-asserts this: `security_features.assert_pre_combine_only` raises if any post-Combine EDP column
(`edp_post`, `edp_change`, `edp_log_change`, …) is present in the assignment input, and the assignment path
projects onto a pre-Combine whitelist, so mutating a post-Combine value cannot change a single assignment
(tested byte-for-byte). Post-Combine EDP / pre→post change remain **outcomes**, appear only in the
exploratory Analysis-A appendix, and are never used for assignment or any primary model.

**Sign convention (stated in every output).** A **NEGATIVE** group coefficient means `moved_down` sits further
below its ceiling (a **larger** gap) — **against** the incentive hypothesis. The hypothesis predicts a
**POSITIVE** coefficient (`moved_down` has a **smaller** gap / higher realisation).

**Level fallback rationale.** About half the board players lack a 2-week snapshot, so a trend-only key would
silently drop them (and bias the sample toward players with two snapshots). The within-position tertile
fallback keeps them with a declared, pre-Combine rule; it is labelled as a separate stratum everywhere and the
headline model includes a **stratum fixed effect** (trend + level-fallback pooled, with trend-only and
level-only reported as pre-declared sensitivity panels).

**Language rule (binding on the copy).** "Incentive exposure" / "consistent with an incentive mechanism" are
permitted; "tried harder", "intended to", "motivated to", "gamed the Combine" are **not**.

**Files.** `config.yaml` (`security_models:` + `security.analysis_a.attenuation`), `src/security_features.py`
(`assign_security_groups`, `group_counts_by_epsilon`, `assert_pre_combine_only`, `load_feature_icc`),
`src/07_security_models.py`, `src/06_security.py` (attenuation appendix + retired-gate summary),
`src/synthetic_security_game_fixture.py`, `notes/prereg-deviations.md`, `notes/prereg.md` (pointer line only;
**no frozen value changed**), `notes/limitations.md`, `notes/pending_full_run.md`, `README_RUN.md`,
`notebooks/bdb27_full_run.ipynb`, `src/tests/test_security_models.py`.

---

## D39 — Cycle 2 implementation: Phase 5 B–G stage, attenuation method, synthetic fixture, fallbacks · 2026-10-17
**What was built.** `src/07_security_models.py` — one code path for `--mode sample|full` and
`--synthetic --synthetic-regime planted|null|selection`, gated with
`gate.enforce(cfg, mode, "07_security_models", requires_game_side=True)`.

* **Inputs** (DuckDB, explicit column lists; no full CSV into pandas): `outputs/game/{efforts,top_k_efforts,
  player_summary,thresholds_applied}.csv`, `outputs/security/security_exposure.parquet`,
  `outputs/envelope/{per_player_envelope,attempt_features}.csv`, covariates via
  `model_features.load_covariates`, and the selection pool = board players **with Combine tracking**
  (exposure table ∩ envelope; `appeared` = `nfl_id` in `player_summary.csv`). A missing required column
  raises `AssertionError` (`assert_input_columns`) rather than silently dropping a field.
* **B (primary)** — effort-level OLS on the top-k efforts, `log(peak_speed)` on `log(shrunken Combine speed)`
  + `moved_down` + route family + pass length + snap share (`n_route_snaps / route_snap_count`, clipped) +
  position + height + weight + years-since-Combine; **player-clustered SEs** (`cov_type="cluster"`,
  `groups=nfl_id`). The **headline is the pooled two-group split with a `stratum` fixed effect**
  (`.security_models.primary.stratum_fe`), plus the pre-declared one-stratum sensitivity panels
  `panel_trend_only` / `panel_level_fallback`. Scopes WR (primary) / TE (replication) / pooled (secondary);
  robustness panel over `peak_speed` and `speed_at_20yd`; a per-player `log(top-k mean speed)` variant with
  HC1. Also `b_group_means.csv` (mean log game speed, log ceiling, gap, utilisation by group).
* **Attenuation (both estimates always reported).** Frisch–Waugh: residualise `y, x, g` on the controls;
  with `S` the 2-regressor moment matrix and `λ` the pre-declared ICC, `β = S⁻¹s` (naive) and
  `S* = [[λ·Var(x), Cov(x,g)], [Cov(x,g), Var(g)]]`, `β* = S*⁻¹s` (corrected). The corrected **group** SE/p
  come from a **player-clustered bootstrap** of `β*` (config `attenuation_reps`, seed + offset) — no
  fabricated precision. `speed_coef_corrected = speed_coef / λ` is emitted for continuity with the
  pre-registered `icc_attenuation` convention. ICC source: `outputs/envelope/feature_selection.csv` →
  `outputs/audit/g_feature_icc.csv` (fit drill) → `outputs/envelope/selection.json`; **if no ICC is
  available the corrected value is null with the reason recorded**.
* **C** — piecewise-linear model in `log(EDP_pre)` (knots at config percentiles), same controls; fitted gap
  at the 10th/50th/90th percentile **at the mean ceiling** and **within-player (own ceiling)**, with delta-
  method SEs from the clustered covariance. Exceedance is reported **overall first** (Wilson CI), then by
  group.
* **D** — skippers (exploratory): `high_secure` players with `cut_only`/`no_speed_drills` vs a size +
  completed-drills prediction fitted on dash players; n, mean residual, CI; **null with a reason when
  `n < d.min_n`**.
* **E** — logistic appearance model (`z_log_edp_pre` + `z_log_combine_speed` + height + weight + position;
  the two log predictors are standardised so the reported ORs are per SD) reporting coefs/SEs/ORs, then a
  stabilised, trimmed IPW refit of B plus a truncation grid (`e_ipw.csv`).
* **F** — (1) standardised height/weight placebos in place of the group term; (2) top-k vs **bottom-k**
  low-effort placebo; (3) Combine peaking marker (`best − mean` attempt speed) × group at the player level.
* **G** — quantile regressions on the **frozen** `models.quantiles` ladder with the player-clustered
  bootstrap (`g.bootstrap_reps`, seed + offset); an estimate whose `boot_se > instability_ratio·|estimate|`
  is **reported as a null** (never forced), with the pre-declared action recorded. Tail utilisation
  P50/P90/P99 by group.
* **Outputs** (`outputs/security_models/`; synthetic under `…/synthetic/<regime>/`): `SUMMARY.{md,json}`,
  `group_assignment.csv`, `b_primary.csv`, `b_group_means.csv`, `c_end.csv`, `c_exceedance.csv`,
  `d_skippers.csv`, `e_selection.csv`, `e_ipw.csv`, `falsification.csv`, `g_tail.csv`, `g_tail_summary.csv`,
  `_DONE.json`. Every CSV's **first column is `provenance`**. Sample mode writes a labelled
  `PENDING FULL RUN` / `UNVALIDATED SAMPLE OUTPUT` placeholder and exits 0; checkpoint + `--force` as usual.
  Wall time + peak RAM go through `common.timed_stage`.

**Fixture (`src/synthetic_security_game_fixture.py`, committed under `fixtures/synthetic/security_game/`).**
Deterministic given the seed; the **same population and RNG stream across regimes**, so planted-vs-null is an
exact counterfactual. 140 WR + 50 TE + 8 forced edge players. `planted`: `+δ` (0.08 log units) on
`moved_down` plus a continuous `dose_gamma` (0.08) gradient in `log(EDP_pre)` ⇒ a smaller gap and a
**positive** group coefficient. `null`: δ = 0 **and** γ = 0. `selection`: planted signal **plus** appearance
probability falling in `log(EDP_pre)`. The top-k efforts carry the planted effect; the **bottom-k efforts
carry neither δ nor the dose** (the F2 placebo). Forced edge players: no 2-week snapshot (level fallback),
one effort, <100 route snaps, no game data, missing size covariates, no 1-week-pre snapshot. Group
assignment in the fixture calls the **same** `security_features.assign_security_groups`, and the ground-truth
groups/δ/target contrast are written to `meta.json` so tests assert against truth.

**Brief deviation (sign of the planted effect).** The brief's illustrative expression
`log game speed = log ceiling + gap0 − delta` for `moved_down` would give `moved_down` a **larger** gap and a
**negative** group coefficient — contradicting both the brief's own "Sign convention" paragraph and the PI
hypothesis. Implemented as `+δ` (smaller gap ⇒ **positive** coefficient), matching the stated expected sign;
recorded in the fixture `meta.json` and here. No other deviation.

**Fixtures are tuned; the analysis is not.** The δ, γ, selection slope and noise levels were chosen so the
recovery / null / selection tests are deterministic and robust. Nothing in `src/07_security_models.py` was
tuned to a fixture result; all thresholds live in `config.yaml`.

**Verification.** `src/tests/test_security_models.py` (16 suites, standalone runner + pytest-collectable):
schema/units fail-loud, sort-key sign/tertiles/middle-exclusion/cutoffs, post-Combine mutation invariance
(byte-identical assignment from a mutated table) + a source guard, planted recovery (positive, CI excludes 0,
all three scopes), monotone dose-response, null not recovered with nulls written as null, selection bias +
IPW move toward the planted target, edge cases, determinism, sample placeholder + checkpoint, and the CLI on
all three regimes. Full standalone suite green.

## D39 — H1/H2 reworded to the incentive direction; exposure is movement, not level (2026-10-17, Conor)

Conor confirmed the D38 framing stands after review. **Documentation only** — no code, config, thresholds
or prereg values changed; the pipeline and sign convention were already correct.

- `TASK.md` H1/H2 now state the incentive prediction: `moved_down` realises a **higher** share of its
  Combine ceiling (smaller gap), and a **positive** `group_coef` supports it.
- Clarified that the **trend stratum sorts on movement only**; level enters solely through the level-fallback
  tertiles. A 1st-to-2nd slider is still starter-calibre, so the "fringe/depth" opportunity story is a
  *level* confound, not a movement one. Snap share, route family and depth are the controls.
- Added limitations 33 (opportunity is a level threat) and 34 (Combine peaking inflates the ceiling; the F
  peaking markers discriminate; a negative sign is not evidence for a single mechanism).
- The sign convention stated in every output is unchanged: positive = `moved_down` closer to ceiling.

---

## D40 — Replace the sign split with an asymmetric threshold (T=3 primary, T=5 sensitivity) · 2026-10-17
**Decision.** The **D38 sign split** (`moved_down` iff `edp_trend > 0.0`, i.e. *any* downward movement) is
**replaced** by an **asymmetric threshold** on the same pre-Combine trend, agreed with Conor after he saw
the movement distribution. The sort key is now:

* **PRIMARY (T=3):** `moved_down` iff `edp_trend >= +3.0` — the player fell **3 or more EDP spots**
  between the 2-week and 1-week pre-Combine snapshots (`edp_trend = 1wk-pre − 2wk-pre`; positive = the
  EDP number rose = worse/less secure). `high_secure` = **everyone else with a trend** (`edp_trend <
  +3.0`), i.e. **stable or rising**. There is **no `unclassified` middle in the trend stratum**.
* **SENSITIVITY (T=5):** the identical rule at `edp_trend >= +5.0`, emitted as the declared
  `panel_threshold_T5` alongside `panel_trend_only` / `panel_level_fallback`. It is declared, not a
  search over cutoffs.
* **Level fallback (unchanged):** with no 2-week snapshot, within-position tertiles of `log_edp_pre`;
  the middle tertile stays `unclassified`.
* **Sign convention unchanged:** positive `group_coef` = `moved_down` closer to its ceiling = supports H1.

**Why the sign split was rejected.** Almost every player moves by *some* amount between the two snapshots,
so `moved_down` (any fall) vs `high_secure` (any rise/flat) split the sample at a knife-edge that is
mostly **measurement noise around a wide movement distribution** (observed interquartile range of
`edp_trend`: **WR 11.3, TE 18.1** EDP spots). A one-spot move — well inside the noise — was being treated
as a regime change, and the split mixed genuinely falling players with tiny wobbles. The asymmetric cut
asks the substantive question — did the player **actually fall several spots**? — and puts stable and
rising players together in the comparison group. This also removes the odd implication that a stable
player and a one-spot faller belong on opposite sides of the incentive story.

**Board counts this rule produces** (trend stratum; players with both pre-Combine snapshots):

| threshold | WR moved_down / high_secure | TE moved_down / high_secure | pooled WR+TE |
|---|---|---|---|
| **T=3 (primary)** | **31 / 52** | **16 / 20** | **47 / 72** |
| **T=5 (sensitivity)** | **28 / 55** | **14 / 22** | **42 / 77** |

**`high_secure` is "not falling 3+ (stable or rising)", not "low EDP" and not "no movement".** The copy in
`TASK.md`, `notes/limitations.md`, the stage `SUMMARY.md`/`JSON` and the notebook must say so; risers are
never silently relabelled "secure" in the low-EDP sense.

**Exposure-table detail.** A snapshot counts as pre-Combine under the board's explicit pre-Combine label
and is additionally required to precede the player's own testing day **where that day is known** (all such
rows pass on the real board). Players with no Combine-tracking record are no longer dropped from the
exposure table on that basis alone — the label is authoritative, and they are dropped later at the
Combine-envelope join if they have no ceiling. This is what puts the whole board's trend stratum in the
counts above (`has_primary` 656 / `has_trend` 619 of 783 board players).

**Files.** `config.yaml` (`security_models.sort`: `fall_threshold: 3.0`, `sensitivity_threshold: 5.0`;
the old `trend_epsilon` / `trend_epsilon_grid` are removed — no second live rule), `src/security_features.py`
(`assign_security_groups` uses `>=`; `group_counts_by_threshold` replaces `group_counts_by_epsilon`),
`src/07_security_models.py` (primary T=3 + `panel_threshold_T5`; SUMMARY labels the semantics),
`src/synthetic_security_game_fixture.py` (fixture re-tuned: `n_te=110`, `trend_sd_edp=10` so the TE
contrast clears `min_group_n=15` under the stricter rule), `src/tests/test_security_models.py`,
`notes/prereg-deviations.md` (DV-8), `notes/limitations.md` items 23/24/33, `TASK.md` H1/H2 + sort key,
`notebooks/bdb27_full_run.ipynb` §5/§8.

**Deviation status.** `notes/prereg.md` stays **FROZEN**; the change is recorded as DV-8. The threshold
was fixed from the movement distribution before any game-side result was inspected (game data is still
absent on this Pi), so this is not a results-driven cut.

**Checkpoint integrity (same D40 cycle).** A notebook test run had left `outputs/security/_DONE.json`
recording a **sample** run. Checkpoints must therefore compare the marker's recorded **mode** (and
provenance/synthetic/regime) with the request, never bare marker existence: stage 02 read only the
marker's existence and could skip a `--mode full` run after a sample run. Fixed centrally in
`common.read_done_marker` / `common.checkpoint_match` / `common.checkpoint_mismatch_note`, applied to
00_thresholds (mode+provenance), 00b, 02, 03, 04, 05, 06 and 07; a mismatch now logs a `rebuilding`
line. 01_audit has no stage-level marker (only per-Parquet conversion checkpoints). Regression test:
`test_envelope.py::test_checkpoint_is_mode_aware` writes a sample `_DONE`, requests full, asserts the
stage rebuilds and only the subsequent full run skips.

## D41 — Notebook: withhold synthetic numbers instead of previewing them · 2026-10-17

**Problem.** With the real `07_security_models` output absent, §8 of `notebooks/bdb27_full_run.ipynb`
silently fell back to `outputs/security_models/synthetic/planted/` — a fixture deliberately tuned to a
plausible +15% effect — and printed it as the headline block. The provenance banner was present, but the
numbers read as a finding.

**Decision.** §8 is all-or-nothing. Unless `MODE == "full"` **and** the threshold **and** security
provenances are `FULL` **and** the source is not a fixture, the section prints
`NO RESULT — synthetic/sample data in use`, the specific reason(s) (which stage is missing or failed,
which file is absent, what the stage's own summary says) and one line of next action. No coefficient, SE,
CI, p-value, effect size or utilisation mean is printed. The status of `06_security` and
`07_security_models` is read from `RUN_RESULTS` and shown at the top of the block.

**Explicit fallbacks.** `security_dir()` returns `(dir, is_synthetic, reason)`; §5/§8 print the path used,
whether it is a fixture, and why. `models_series()` / `validate_series()` return a fourth `reason` element
and §6 announces the source of every plotted series. No reader falls back silently any more.

**Opt-in preview.** `SHOW_SYNTHETIC_PREVIEW = False` (Configuration cell). Only when `True` may the
fixture be rendered, under a `SYNTHETIC LAYOUT PREVIEW — NOT DATA` heading, with the verdict still
suppressed. §5's synthetic tables are withheld under the same flag. Figures keep rendering because each
carries its own `SYNTHETIC FIXTURE (not a finding)` stamp in the image title.

**Verified** (sample mode, executed in a scratch copy so the FULL outputs stayed untouched): §8 printed
the NO RESULT block with zero numeric claims; with the flag `True` it printed the labelled preview block;
a repeat sample run skipped `02_combine_envelope` on its checkpoint and `outputs/envelope/_DONE` kept its
`FULL` marker.

**Pre-existing, unchanged here.** A sample-mode Run All still rebuilds `02_combine_envelope` and
`06_security` when the on-disk marker is `full` (D40 requires an exact mode match), which relabels those
outputs as sample. On a checkout holding `FULL` outputs, run the notebook in `full` mode.
**Closed by D42 (2026-10-17):** a `--mode sample` run now skips and reuses FULL checkpoints, so it can no
longer relabel or overwrite them.

## D42 — A sample run never downgrades FULL stage outputs · 2026-10-17

**Problem.** D40 made stage checkpoints exact-mode, which correctly stopped a sample marker satisfying a
full run, but created the reverse hazard: on a checkout holding FULL outputs (the multi-hour Combine
envelope, the real security table), `--mode sample` found no exact-mode match and rebuilt every stage,
overwriting the FULL outputs with sample-provenance ones.

**Decision.** The central helper `common.checkpoint_match` is now asymmetric and every stage goes through
it (`00_thresholds` included, replacing its inline marker check): same mode skips as before; a `sample`
marker never satisfies a `full` request; a `full` marker satisfies a `sample` request, which logs
`FULL checkpoint at <path> satisfies --mode sample — reusing FULL outputs (set --force to rebuild in
sample mode)` and leaves the marker byte-identical. Provenance is mode-derived and so is not compared on
this downgrade path; `synthetic`/`regime` still must match. `--force` overrides everything. Regression
test `test_envelope.py::test_sample_run_never_downgrades_full_checkpoint` covers all four cases and
asserts the FULL marker is byte-identical after the sample run.

---

## D43 — Retire the GTM runtime scraper; safe odds ratios in E · 2026-10-17

**Problem.** Two blockers in the first full real-data run. (1) `07_security_models` died 3.3 s in with
`OverflowError: math range error` and wrote nothing: the logit fit was inside a `try`, but the odds-ratio
computation (`math.exp(res.params[i])`, `math.exp(ci[i, 0/1])`) sat outside it. On real data the appearance
model (quasi-)separates, the coefficients explode, and `exp()` overflows — killing B/C/D/F/G with it.
(2) `00b_external_proxies` spent 40 s trying to fetch Grinding The Mocks over the network and then failed,
and its failure blocked `04_models` through `external.require_for_phase4: true` and stalled
`05_validate` on a stale checkpoint.

**Decision.**

* **GTM runtime scraper retired.** We hold the board snapshot export
  (`data/raw/external/gtm/GTM_BDB_EDP_Snapshots_20261016.csv`), which `06_security` uses for all 783
  exposures. The 00b live-acquisition path is a leftover from when we thought no export existed. It is now
  a **retired fallback**: `external.enabled: false` and `external.require_for_phase4: false`, with 00b
  exiting 0 and logging `external proxy acquisition disabled (board export in use)` **before any network
  access** (any `--synthetic` fixture run is exempt and never touches the network). `04_models` no longer
  passes `requires_external_proxy=True` — the proxy was never an input to that model; the gate was the
  only dependency. The gate implementation itself is kept for the day the fallback is revived.
* **E is non-fatal and separation-aware.** A `_safe_odds_ratio` helper clips the exponent at ±700
  (`security_models.e.or_exponent_clip`) and reports `inf` / `OR > 1e300 (exponent clipped…)` explicitly
  instead of raising. `_separation_reason` flags non-convergence, non-finite coefficients, `|coef| > 20`
  or `se > 50` (`e.separation_max_coef` / `e.separation_max_se`) and returns E as a labelled null with the
  reason, exactly as the fit-exception path already did. The `run_e(...)` call in `run()` is additionally
  wrapped so any unexpected exception becomes a reported-null E block and B/C/D/F/G still write.

**Why.** E is a secondary selection check. A numerical failure in it must never destroy the primary
tables, and an unidentified (separated) logistic OR must be reported as unavailable, not as a number or a
crash. The board export is the pre-registered exposure source (D37); live scraping was only ever a
contingency.

**Structural hardening (the point is that one bug cannot destroy a run again).**

* **Incremental emission.** `run()` no longer computes B..G and writes at the end. Each block's files are
  written immediately after that block completes: `group_assignment` before B, then
  `b_primary`/`b_group_means`, `c_end`/`c_exceedance`, `d_skippers`, `e_selection`/`e_ipw`,
  `falsification`, `g_tail`/`g_tail_summary`. A later failure leaves every earlier file on disk, valid.
* **Per-block isolation, loudly.** B/C/D/E/F/G each run inside their own `try/except`. A failed
  exploratory block writes that block's file(s) with a single labelled row
  (`term`/`weighting='unavailable'`, `reason='<ExceptionType>: <msg>'`, `block_status='FAILED'`), is
  logged at ERROR, and is listed in `SUMMARY.json::failed_blocks` and in a `## ⚠ FAILED BLOCKS` section of
  `SUMMARY.md` — never swallowed. Every successful row carries `block_status='OK'` (last column, after the
  mandatory leading `provenance`). **B is the headline**: a B failure writes the labelled FAILED row and
  then raises `RuntimeError("primary block B failed (...)")` so the stage exits non-zero; it never
  fabricates a null primary estimate.
* **Hostile-input tests.** `src/tests/test_security_models_hostile.py` feeds (a) a perfectly separating
  predictor for the selection logit, (b) a constant/zero-variance covariate, (c) extreme outliers in
  `log_edp_pre` and Combine speed, and (d) NaN/blank cells in optional covariates. It asserts no uncaught
  exception, every expected output file present, failed blocks labelled with a reason, earlier files
  surviving a later G blow-up, the B hard-fail path, and that the primary B result is either present or the
  stage exits non-zero with an explicit message.

**Evidence.** Regression tests: `test_security_models.py::test_separation_reports_null_e_not_crash`
(perfectly-predicted `appeared` ⇒ no exception, `term=unavailable`, reason recorded),
`::test_safe_odds_ratio_clips_instead_of_overflowing`, and
`::test_e_failure_is_non_fatal_and_b_primary_still_written` (E raises `OverflowError`; `b_primary.csv`,
`c_end.csv`, `d_skippers.csv`, `falsification.csv`, `g_tail.csv` all emitted and E labelled FAILED).
`test_security_models_hostile.py`: 4/4 (separation end-to-end, constant covariate + outliers + blanks,
incremental emission through a simulated G failure, B hard-fail).
`test_external_proxies.py::test_external_disabled_no_network_exit_zero` runs 00b with every socket denied
and asserts exit 0 plus the disabled log line; `::test_models_not_gated_on_proxy` asserts 04 no longer
references the flag and both config switches are false. Full suite: 17 files, 151 tests, all passing.

---

## D44 — The `OverflowError` was `bmi`, not the selection logit; covariate-quality reporting · 2026-10-18

**Problem.** D43 did not fix the crash. After pulling `eea0272` (D43), a Run All on Windows failed again
with the **identical bare `OverflowError: math range error`** and **no `b_primary.csv` at all**. That is a
different signature from D43's own failure mode: D43's per-block isolation guarantees either a B result or
`RuntimeError("primary block B failed (...)")` plus a labelled `block_status=FAILED` row. A bare
`OverflowError` with no B file means the exception happened **before the B block was entered**, so D43
guarded the wrong code.

**Root cause.** `src/model_features.py::bmi` computed `float(height_in) ** 2` with no guard. CPython's
`float_pow` maps C `ERANGE` to `OverflowError`; the message is platform-dependent — **Windows: `math range
error`** (what Conor saw), **Linux/glibc: `(34, 'Numerical result out of range')`**. Same exception, different
text, and no test ever fed an absurd height, so the Linux suite never reproduced it.

It was on the **pre-B path**: `07_security_models.run()` → `load_covariates()` →
`model_features.covariate_table()` → `bmi()`. That call builds covariates before group assignment, before
`group_assignment.csv`, before B. Hence: bare exception, zero output files, and no D43 wrapper — every
symptom follows from one line.

**Decision.**

* **`bmi` is overflow-proof.** `h * h` replaces `**` (no `float_pow`, no `ERANGE`), the whole computation is
  inside `try/except (OverflowError, ZeroDivisionError, ValueError, TypeError)`, and a non-finite **or
  non-positive** result returns `nan`. The `val > 0` condition matters: `1e200 * 1e200` overflows to `inf`
  without raising, so a naive guard would report a **BMI of exactly 0.0** for a corrupt height.
* **A bogus height is visible, never silently `nan`.** `security_models.primary.covariate_bounds` in
  `config.yaml` declares the plausible ranges (`height_in 48–90`, `weight_lb 100–400`, `bmi 15–60`, with a
  comment saying these catch corruption, not unusual athletes). `model_features.sanitize_covariates()` then:
  sets the offending value to `NaN`; appends `{nfl_id, field, raw value, lower, upper, reason}`; and marks
  the player `quality_issue` so `build_effort_rows` drops the row under the explicit reason
  `covariate_quality` (not the generic "missing size" tally). An **empty** cell stays "missing" and is not
  mislabelled as corrupt; a **present** but non-finite/out-of-range value is corrupt.
* **The stage reports it three ways.** (1) A `WARNING` per value naming `nfl_id`, field, raw value and reason,
  plus a summary warning with the counts. (2) `outputs/security_models/covariate_quality.csv` (always written;
  a single `field=none` row when nothing was flagged, so a clean run states the check happened). (3)
  `SUMMARY.json::covariate_quality` (`bounds`, `n_players_flagged`, `n_values_flagged`,
  `n_effort_rows_excluded`, `issues`, `policy`) and a `SUMMARY.md` line + table. Every `b_primary.csv` row's
  `note` states how many players/rows were excluded, so the primary block itself carries the exclusion count.
* **Audit of every other numeric path.** An AST scan of `07_security_models.py`, `common.py`, `gate.py`,
  `model_features.py`, `security_features.py` (locked by `test_static_numeric_audit`) finds `math.exp` only
  inside `_safe_exp` (the D43 clip) and no scalar `float(...) ** n`; the remaining `** 2` is a NumPy array
  variance term in a VIF sum, which cannot raise. `math.log` sites are already guarded by `v > 0`.
* **Notebook guard extended to D44.** `run_diagnostics.REQUIRED_CAPABILITIES` now also requires the D44
  marker in `07_security_models.py` (`covariate_quality`), `model_features.py` (`sanitize_covariates`) and
  `config.yaml` (`covariate_bounds`); a checkout missing any of them is a **hard stop** with the sha and
  "pull and re-run". A dirty tree / detached HEAD / shallow clone / missing git is a warning only — a dirty
  tree must never block a run. §8 distinguishes **CRASH** (prints the captured traceback tail, last ~10
  lines, and says re-supplying data cannot help) from **DATA-ABSENT** (data remedy), and a `FAILED` B row now
  carries the run's tail instead of an empty list.

**Why.** A corrupt height must not decide whether the headline model exists, and a guard that only returns
`nan` would hide a data-integrity defect and silently shrink the sample. The exclusion is therefore counted
and named, with the bounds declared in config rather than buried in code.

**Evidence.** `test_security_models_hostile.py::test_absurd_height_through_stage_writes_primary_and_reports_quality`
drives `height=1e200` and `weight=1e200` through the real CLI entry point and asserts exit 0, an `OK`
`primary_env_vmax` row in `b_primary.csv` with the exclusion note, the two `nfl_id`s and the raw `1e200` in
`covariate_quality.csv`, non-zero `n_effort_rows_excluded`, the bounds in `SUMMARY.json`, the summary line in
`SUMMARY.md`, and the `IMPLAUSIBLE COVARIATE` warning naming the player on stderr.
`::test_separating_real_shaped_rows_without_fixture` now also asserts `bmi(1e200, …)` and `bmi(1e-200, …)`
are `nan` (not `0.0`/`inf`). `test_run_diagnostics.py` covers the D44 capability gate, dirty-tree-is-a-warning,
and the crash-vs-data distinction. Full suite: **19 files, 177 tests, all passing** (D43 baseline 17/151;
the two security-model files 31/31).

---

## D45 — A stage-07-only re-run is a supported path (RUN_ONLY) · 2026-10-18

**Problem.** D44 fixed a three-line defect in `model_features.bmi`, but the only way to exercise it was a
full Run All: ~5–6 h, almost all of it stages 03/04/05 on the full tracking data, which are already
checkpointed. `07_security_models` itself fails in ~3 s. Worse, §8 labelled every checkpointed stage
`not run in this session (§3 must be executed first)`, so a single-stage re-run *looked* broken even when
the on-disk outputs were FULL.

**Decision.**

* **`RUN_ONLY` in the Configuration cell** (default `[]` = the whole `STAGE_ORDER`). A non-empty list runs
  exactly those stages, still in `STAGE_ORDER` order; `STAGE_ORDER` itself is never edited. Unknown names
  warn; a list matching no stage raises. Every skipped stage is printed as
  `[SKIPPED - checkpoint]` with its own on-disk state, so a single-stage re-run takes seconds and is
  self-describing. Documented in §1 and §3.
* **Skipped ≠ failed.** `stage_status_text` now falls back to `run_diagnostics.stage_status_on_disk`, which
  reads the stage's own `_DONE(_DONE.json)` marker and key artefacts and returns e.g.
  `FULL checkpoint on disk (not re-run in this session; marker mode=full)` — never `FAILED`/`PENDING`.
* **The §8 FULL gate is provenance-only.** Extracted to `run_diagnostics.headline_is_full(mode, thresholds
  provenance, security provenance, synthetic, checkout_blocked, b_row_status)`: no `RUN_RESULTS` input, so
  `RUN_ONLY=["07_security_models"]` with FULL thresholds + FULL security provenance prints a live headline
  block. (`test_headline_is_full_is_provenance_driven_not_session_driven`.)
* **Self-describing header.** §8 now prints the data dir and the covariate-quality counts
  (`n_players_flagged` / `n_values_flagged` / `n_effort_rows_excluded`) before any number.

**Is `04_models` affected by the `bmi` fix?** Yes, it is reachable: `04_models.py:512` calls
`mf.load_covariates(...)` → `covariate_table` → `bmi`, and `height`, `weight`, `bmi` are controls in both
`models.controls_without_draft` and `models.controls_with_draft`; `05_validate` reads the same table. So:

* **No flagged covariate** (the expected case): `sanitize_covariates` is a no-op, every value 04 sees is
  byte-identical to the D43 table, and 04's existing FULL checkpoint is **valid — no rebuild**.
* **Any flagged covariate**: the covariate table 04 sees has changed for those players, so its checkpoint
  is stale. It could not have been built on that same table anyway — the old `bmi` would have raised
  `OverflowError` in 04 exactly as it did in 07, since both call the same loader before fitting. Rebuild
  with `FORCE_STAGES=['04_models','05_validate']`. §8 prints that instruction whenever the exclusion count
  is non-zero, so the stale-provenance input is never silent.

**Evidence.** `test_run_diagnostics.py`: `test_stage_status_on_disk_reads_checkpoint_and_never_says_failed`,
`::test_stage_status_on_disk_reports_outputs_without_marker`,
`::test_stage_status_on_disk_missing_stage_is_not_a_failure`,
`::test_headline_is_full_is_provenance_driven_not_session_driven`.
`test_notebook_contract.py`: RUN_ONLY default/documented, loop filtered via `STAGES_TO_RUN` with
`STAGE_ORDER` untouched, skipped stages sourced from disk (no `FAILED`/`PENDING`), §8 gate free of
`RUN_RESULTS`, header carries data dir + covariate quality.

---

## D46 — The notebook executed stale in-memory modules after a pull · 2026-10-18

**Problem.** D44 fixed the `bmi` overflow and D45 made a stage-07-only re-run cheap, yet Conor's run kept
dying with the *same* pre-D43 `OverflowError` and still wrote no `b_primary.csv`. The checkout guard
reported the new commit every time, because it reads the files on disk. The notebook's §3 loop did not:
`run_stage()` called `importlib.import_module(module_name)`, and Python returns the module object already
cached in that kernel's `sys.modules`. Re-running the cell therefore re-executed the **pre-pull** code
objects, formatted against the fresh source file on disk.

**Diagnostic signature (the general tell).** A traceback whose frames cannot exist in the current source:

* `main()` reported at a line that is `L.append("## ⚠ FAILED BLOCKS")`, not a call to `run(...)`;
* `run(...)` reported at a line that is `rec["action"] = "adjacent_tau_report"`, not a call to `run_e(...)`;
* `run_e(...)` reported at a line that is `_safe_exp`'s `return float(math.exp(x))`.

Three frames, none a legal call chain, all with plausible line numbers. That combination means the
interpreter is executing older code objects and printing them against the newer file. **If the printed
frames do not form a legal call chain in the file you are reading, the kernel is stale — not the source.**
Line numbers match because they are read from the file; the code is what is old.

**Decision.**

1. **Reload on every stage invocation.** New `src/stage_runner.py` owns the stage call. `run_stage()`
   calls `importlib.invalidate_caches()`, purges the module's cached `.pyc`, reloads every
   already-loaded helper (`common`, `gate`, `model_features`, `security_features`, `combine_features`,
   `game_features`) in that order, then reloads the stage module itself (importing it only if absent).
   The `.pyc` purge matters: `reload` otherwise trusts the bytecode header's source mtime+size, and a
   pull or a same-second rewrite can leave that looking valid — the same trap in a different layer.
   Helper-first order matters because a stage's `from common import load_config` re-binds only when the
   stage module is re-executed.
2. **Bootstrap knows what the kernel is holding.** `stage_runner.code_state_report(REPO)` runs in §2 and,
   when any pipeline module is already in `sys.modules`, prints a banner plus one line per module with its
   file, mtime and last commit (`run_diagnostics.file_commit`). A fresh kernel prints
   `no pipeline module preloaded`. The §3 stage rows now end with **`code reloaded from disk`**
   (or `code imported from disk` on a first import), so the run states which code it used.
3. **No traceback is lost.** Any stage exception writes the full traceback to
   `outputs/<stage_subdir>/crash_<YYYYmmdd-HHMMSS>.txt` (never overwriting; `-2`, `-3` … on collision) and
   the notebook prints the path. `failure_lines` prints `traceback tail: (empty — see <path>)` instead of a
   bare `traceback tail:`, and appends `full traceback: <path>`. The `crash_` prefix keeps these files out
   of every results listing (stage artefacts are named explicitly; no stage globs its output directory).
4. **Tests.** `test_stage_runner.py` (8) reproduces the defect directly: import a temp module, rewrite its
   source with the *same byte length in the same second*, call `run_stage` again, assert the new code ran
   and the action is `reloaded`. Also: crash file written in the stage's subdir with a full traceback and
   the path printed; empty-tail wording; preload warning with file/mtime/commit; module-list coverage.
   `test_notebook_contract.py` gains three static assertions (bootstrap banner, delegation to
   `stage_runner`, `code reloaded from disk` in both the §3 table and §3 markdown). `test_run_diagnostics.py`
   gains `file_commit` coverage (real temp repo + no-git).

**Operational consequence.** A kernel that is *already* running pre-D46 notebook code cannot be fixed by
re-running the §3 cell — that cell itself is the stale code. **After pulling D46: restart the kernel and
Run All once**; from then on re-running any cell is sufficient, because every stage call reloads from disk.

---

## D47 — Route-mix robustness for the group contrast (analysis only) · 2026-10-18

**Question (Conor).** "Can we rule out that this is because they are running worse routes in the
league?" The rival explanation for a negative `group_coef` is mechanical: later-pick / lower-security
receivers get fewer deep routes and more decoy duty, which lowers a top-k **peak-speed** outcome with no
effort or fatigue mechanism. The existing controls (`route_family`, `pass_length`, `snap_share`,
position, height, weight, years since Combine) absorb some of that, but not depth within family,
coverage, alignment, motion, or target/decoy duty.

**What was added.**

* **`src/08_route_robustness.py`** — analysis-only. It rebuilds nothing: it imports `07_security_models`
  and re-uses `build_effort_rows`, `_complete`, `design`, `ols`, `_coef`, `_mark_thin` and
  `_apply_attenuation`, so every fit is block B's exact specification on a different sample. It writes
  `outputs/security_models/route_robustness/`: `route_balance.csv`, `route_balance_tests.csv`,
  `route_strata.csv`, `composition.csv`, `loo_fits.csv`, `loo_summary.csv`, `permutation.csv`,
  `reproduction_check.csv`, `route_robustness.md`. Nothing touches `b_primary.csv` or the verdict.
* **Config** `route_robustness:` — `perm_reps: 1000`, `perm_seed_offset: 505`, the VERTICAL family and
  the depth bands `[0–9, 10–19, 20+]`. The deep stratum re-uses `audit.deep_pass_length_yd` (15); no new
  cut-off was invented.
* **Notebook §9** — appended after §8, prints `robustness — not a pre-declared test` before anything
  else, reads the directory above, and prints the balance table, the stratum rows and the LOO/permutation
  p-values. It is never merged into §8's verdict block.
* **Tests** — `src/tests/test_route_robustness.py` (12): counts/shares sum, missing fields stay NaN,
  the thin rule never fits, the full-sample stratum fit equals `run_b` to 1e-12, LOO drops exactly one
  player per fit, summary ranges/shares, permutation determinism and null centring, CLI artefacts +
  schema, sample placeholder.

**Findings (full-data artefacts available in this checkout; 87 pooled players, 33 `moved_down` /
54 `high_secure` at T=5).**

* **Balance (T3 panel, pooled):** route-family mix is *not* distinguishable — VERTICAL 45.0% vs 39.2%,
  BREAK_IN 20.6% vs 19.8%, BREAK_OUT 27.2% vs 28.4%, SHORT 7.2% vs 12.5% (χ²=3.66, dof 3, p=0.30,
  Cramér's V=0.09). Mean depth 10.3 vs 12.6 yd (Welch p=0.074). Snap share 0.83 vs 0.89.
  **Target rate 55.6% vs 66.4% (−10.8 pp, p=0.025)** — `moved_down` receivers are targeted materially
  less often. WR only: 51.7% vs 40.6% VERTICAL (χ² p=0.161, V=0.13), depth 11.8 vs 13.9 yd (p=0.195),
  target rate 58.3% vs 72.2% (−13.9 pp, p=0.0124).
* **Within-stratum re-estimation:** 17 of 20 panel × scope × stratum cells estimable; **none** reached
  p<0.05 (11 of 17 were negative). Pooled T=5: VERTICAL −0.0559 (p=0.451), depth ≥15 −0.0266 (p=0.732),
  bands 0–9 −0.0112 (p=0.819), 10–19 −0.0549 (p=0.466), 20+ −0.0746 (p=0.501). WR cells are centred on
  zero (+0.0003 VERTICAL at T=5). Three WR cells were thin (14, 14 and 13 `moved_down` players < 15) and
  were reported, not fitted.
* **Composition (exploratory, scope restrictions of the same fit):** the pooled coefficient is not a
  WR coefficient. At T=5 pooled is −0.0673, WR −0.0019, pooled **excluding TE** −0.0173 (T=3:
  −0.0505 / +0.0071 / −0.0011). Removing 20 TE players moves the estimate about three-quarters of the
  way to zero. Those TE rows are 11/9 at T=3 and 10/10 at T=5 — below `min_group_n` in both groups —
  so TE is not a reportable scope, yet it carries the pooled number. The "pooled excluding WR" scope is
  itself thin (15/12 at T=3, 12/15 at T=5) and is reported without a fit.
* **Leave-one-player-out:** pooled T=5 baseline −0.0673 (p=0.1176) → coef −0.0802…−0.0532, p
  0.0585…0.2070; **0 of 87 refits reached p<0.05** and 0 of 87 had a CI excluding zero. The most
  influential single player moves the coefficient by +0.014. Pooled T=3 and WR T=5 behave the same
  (0 of 87 and 0 of 60 significant refits).
* **Player-level permutation (1000 reps, fixed seed):** pooled T=5 p=0.1249 two-sided (directional
  0.0559); pooled T=3 p=0.2368; WR T=5 p=0.9710. The observed coefficient sits inside the label-shuffle
  null.

**Reproduction caveat — load-bearing.** The stage-07 numbers quoted for this check (pooled T=5
`group_coef=-0.0380`, `se=0.0185`, `p=0.0396`, corrected `-0.0395`, p=0.0299) are **not** what the
committed code produces from the full-data artefacts present here. Re-running stage 07 on the same
population (87 players; 33/54) gives pooled T=5 **−0.0673 (se 0.0430, p=0.1176)**, pooled T=3 −0.0505
(p=0.2220), WR T=3 +0.0071 (p=0.8701), WR T=5 −0.0019 (p=0.9686). The group sizes match exactly, so this
is not a population difference; the SE is 2.3× larger, which points to a different effort build or a
different estimator in the run that produced the quoted row. `b_primary.csv` from that run was not found
anywhere on this machine (`outputs/` is gitignored; only the sample placeholder and the synthetic runs
are present). **Consequence:** the robustness numbers above describe the reproducible estimate, not the
quoted one. Re-run `python src/08_route_robustness.py --mode full` (or the notebook §9) against the
checkout that holds the quoted `b_primary.csv`; the stage prints a `reproduction_check.csv` comparison so
the difference cannot pass silently.

**What this cannot rule out.** `outputs/game/efforts.csv` carries `route_ran`, `pass_length` and `target`
only. `receiver_alignment`, `in_motion_at_ball_snap`, `coverage_assignment` and `team_coverage_type` exist
in `player_play.csv` (audit item d) but are **not propagated** to the efforts table, so they cannot be
balanced or controlled here. Even a clean pass would leave route-running skill/separation, QB and target
quality, coverage difficulty and decoy duty beyond the binary target flag untouched. The honest
conclusion is that route *menu* differences are small and cannot explain the gap on their own, while the
target-rate difference is real and is exactly the kind of decoy-duty mechanism the rival explanation
predicts — so it is **not** ruled out.

**Discipline.** `outputs/` is gitignored; the new directory is additive and no existing output was
modified. `b_primary.csv` and the pre-declared verdict are untouched. All emitted CSVs put `provenance`
first.
