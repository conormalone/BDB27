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

**Commit.** `PHASE05_SHA` on `main` (code/config/tests/notes/fixtures only — no data).
