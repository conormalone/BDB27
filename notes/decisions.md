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

## D48 — Draft outcomes and targeted routes are exploratory probes (analysis only) · 2026-10-19

**Question (Conor).** Two follow-ups on the pre-Combine group contrast: (A) did the two sort
populations actually end up with different **draft capital**, and does the `moved_down` coefficient
survive controlling for / within realised draft slot? (B) does the contrast hold on plays where the
receiver was **actually targeted**, and what does the non-target (decoy) subset look like?

**A — draft outcome is POST-Combine.** `players.csv` carries `draft_round`,
`draft_pick_within_round` and `draft_overall_pick`; the stage joins them to 07's group assignment on
`nfl_id`. It reports per group × panel × scope: n assigned, n matched, join rate, unmatched ids,
median pick with IQR, R1 / R2 / R3-4 / R5-7 shares, R1-2 share, a Mann-Whitney U with Cliff's delta,
a chi-square on the four round bands and a Fisher exact on R1-2 vs R3-7. Spearman correlations of
`edp_pre` and `log_edp_pre` with actual overall pick are the validity check on the sort key.

**This is not a clean adjustment.** Realised draft slot is post-Combine. Conditioning on it can open
a collider (Combine performance → draft slot, and Combine performance → game speed). A significant
group difference in draft slot is **both** evidence that the pre-Combine sort key tracks a real market
outcome **and** a live confound (worse draft capital → worse QB/team/opportunity). The conditional
fits (`log(draft_overall_pick)` as a covariate; within-band refits) are therefore labelled a
mechanism/diagnostic probe, not causal estimates, and are kept out of the pre-registered §8 panel.

**B — targeted routes.** `build_effort_rows` in `07_security_models.py` now carries `target` and
`route_ran` in the model-row dict, and `load_topk`/`load_efforts` read `target` (the field already
exists in `top_k_efforts.csv` and `efforts.csv`). Existing artefacts do **not** need a stage-07
rerun: `09_draft_and_target.py` derives the flag by ONE documented key join on
(`nfl_id`, `game_id`, `play_id`) from `efforts.csv`, and asserts that any in-row value agrees; a
disagreement is a hard error, not a silent choice. The primary panel and the T3/T5 sensitivity panels
are refitted on `target == True`, `target == False` and all rows, pooled and WR.

**Selection, not randomisation.** Being targeted is a post-treatment, role-dependent variable
(QB/scheme/depth chart). `target == True` is therefore **not** a clean estimate and is not written as
one. The value of the split is decomposition: the non-target (decoy) subset is where the mechanical
"peak speed is an opportunity-limited maximum" suppression should live. A null there is not evidence
of no effort effect, and a surviving target-only coefficient is not a causal group effect.

**Engineering.** New analysis-only `src/09_draft_and_target.py` imports 07's `build_effort_rows` /
`design` / `ols` / `_mark_thin` / attenuation helpers and 08's panel builder; no statistics are
forked. New config block `draft_target:` (band definitions, target subsets, min_obs/min_group_n
reuse, seed offset); no existing config key changed. Outputs are additive under
`outputs/security_models/draft_and_target/` (`draft_by_group.csv`, `draft_tests.csv`,
`draft_conditional_fits.csv`, `draft_spearman.csv`, `target_subset_fits.csv`, `target_balance.csv`,
`draft_target.md`). `outputs/` stays gitignored; no existing result file is modified. Notebook §10 runs
the stage **in-notebook** via `stage_runner` and, when full-data inputs are absent, prints exactly
which files are missing instead of substituting sample/synthetic numbers. `src/tests/test_draft_and_target.py`
adds 12 tests: planted-effect recovery in target and non-target subsets, a null fixture flat in every
subset, a thin-group guard, target join agreement/conflict, missing-key reporting, draft rank/band
direction, CLI artefact/provenance contract, missing-input exit, sample placeholder, and the §10
notebook contract. Full suite: **22 files / 226 tests, all passing**.

**Pi caveat (repeat).** This Pi has no full-data `b_primary.csv` (`outputs/` is gitignored; the
full-run staging directory is absent). The code path is proven on the synthetic fixture, but the
real numbers must come from Conor's laptop run. The Pi outputs are not quoted as if they were his
data; they are labelled `SYNTHETIC FIXTURE (not a finding)`.

**Discipline.** Neither analysis enters §8 or changes `b_primary.csv`/the verdict. All emitted CSVs
put `provenance` first.

## D49 — Season scope is now REG+POST (preseason excluded); season labels come from games.csv · 2026-10-19

**Why this is a specification change, not a robustness tweak.** Verified against the raw files
(`/mnt/project_data/conor_downloads/bdb27`):

* `player_play.csv` has 314,197 rows / 497 players / 1,001 games (2023-08-03 → 2026-02-08).
  Each true season is 272 REG + 49 PRE + 13 POST games.
* Route snaps by `season_type`: **REG 43,181 (81.0%), PRE 7,962 (14.9%), POST 2,165 (4.1%)**.
  145 of 156 route-running players have at least one preseason route snap, and **50 players have
  >25% of their route snaps in preseason**.
* **Defect 1 — season mislabel.** `_season()` in `07_security_models.py` parsed the leading four
  digits of `game_id`. NFL game ids are date-based (`YYYYMMDDNN`), so every January/February game
  received the FOLLOWING calendar year as its season: 87 games, **4,775 route snaps (9.0%)**, had
  `years_since_combine` one year too high. `games.csv` carries the true `season`/`season_type`.
* **Defect 2 — preseason confound.** Preseason was included by default (no `season_type` filter
  anywhere in `src/`). Snap counts, `snap_share`, the `min_snaps` eligibility gate and the outcome
  rows therefore contained 14.9% of reps played largely against backups. **Direction of the bias:**
  deeper-depth-chart players plausibly receive proportionally more preseason work — the same axis
  the `moved_down` / `high_secure` contrast differs on — so this is a plausible group-difference
  channel, not just noise.

**Decision.** The primary specification is **REG + POST**. The all-games version
(**REG + POST + PRE**) is **retained as the declared sensitivity, not deleted**. Nothing is
dropped at build time: `03_game_demand` carries the true `season` and `season_type` on every
effort and emits top-k once per configured scope. The scope is applied at the model stage from
`config.yaml::game.scope`, so Conor's ~6-hour game build is paid **once**, not twice.

**Implementation.**

* One season helper in `common.py` (`load_game_index` / `season_and_type` / `attach_seasons`),
  used by 03, 07 and 08/09. games.csv is authoritative; when games.csv **is present**, a game_id
  missing from it is a hard error listing the count and ids. The date fallback (`YYYYMMDD`,
  Jan/Feb → year − 1) is used only when games.csv is absent (fixtures / sample mode).
* `03_game_demand` joins games.csv; `efforts.csv` and `top_k_efforts.csv` gain `season` /
  `season_type`; `top_k_efforts.csv` gains a `season_scope` label (primary / sensitivity) and
  `route_snap_counts.csv` splits counts by true season/type. `player_summary.csv` reports the
  primary-scope denominator (plus all / sensitivity columns).
* `07_security_models` filters rows **and** the denominator by the active scope; `min_snaps` is
  applied to the same scoped count. `season_scope.csv` reports, per scope: route snaps, players,
  players passing `min_snaps`, mean/median route snaps and mean snap-share. Every output CSV
  carries a `season_scope` column and the SUMMARY records both scope definitions.
* `08` / `09` inherit the filter through `build_effort_rows`; 09 asserts that no inherited row
  leaks a season type outside the active scope.

**Checkpoint fingerprints (the dangerous one).** `common.checkpoint_match` previously compared
only mode/provenance/synthetic/regime, so a config change silently reused stale artefacts. Every
stage `_DONE` marker now records `pipeline_version` + `config_fingerprint` (stable SHA-256 of the
stage's relevant config subtree, sorted keys). A **missing** fingerprint is a mismatch (legacy
markers rebuild), and so is a changed one. `pipeline_version` is bumped to **1** in this commit.
Consequence: Conor's existing markers are invalid, so the first full run after this commit is
**one full rebuild** — this is correct, not a resume. The notebook §3 prints the per-stage
fingerprint rejection reason and an explicit `artefacts stale — ONE FULL REBUILD REQUIRED`
banner; §1 documents it.

**What is NOT claimed here.** No new result is reported. The Pi has no full artefacts. The code
path is proven on the synthetic fixture (games spanning PRE/REG/POST, a Jan game whose calendar
year differs from its season, a player with majority-preseason route snaps) and on sample mode.
The real REG+POST numbers require Conor's one rebuild, then notebook §11 (primary vs all games).

**Read on how much the headline could move.** Two offsetting channels are now explicit: (i) the
season relabel removes a one-year error in `years_since_combine` for 9.0% of route snaps; (ii)
excluding preseason removes reps that are disproportionately available to deeper-chart players.
If the group contrast is driven by opportunity/role, the primary coefficient should move relative
to the all-games sensitivity; if it is stable, the confound is small. The honest statement is that
the direction of (ii) is predictable (deeper charts get more preseason work) but its magnitude on
this contrast is unknown until the full REG+POST run. `outputs/` stays gitignored.

## D50 — The exposure is modelled continuously (exploratory panel only) · 2026-10-20

**Why.** The pre-registered exposure is a **binary** cut (`moved_down` iff `edp_trend >= +3.0`, D40) with a
declared +5.0 sensitivity. Both cuts are arbitrary, and the T3→T5 change relabels only a handful of players
while moving *p* a lot — the threshold is not doing real work. A continuous model asks the same question
without a cut: **does one more EDP spot of pre-Combine decline predict a smaller share of the Combine
ceiling realised in games?**

**The binary result is untouched.** `b_primary.csv`, `TASK.md` H1 and every frozen value in
`notes/prereg.md` stay exactly as they are. The new panel is written only under
`outputs/security_models/dose/` by `src/10_dose_response.py`, is labelled **exploratory / post hoc** in
every file, and cannot change the verdict. Nothing here is a new pre-registered test.

**Estimand and estimator (reused, not reimplemented).** The outcome is **exactly block B's**: row-level
`log(peak_speed)` on the top-k efforts, `log_env_vmax` as the ceiling regressor, `statsmodels` OLS with
`cov_type="cluster"`, `groups = nfl_id`, and the same controls **minus `stratum`**. `stratum` is *constant*
inside this population by construction — every row has a finite `edp_trend`, which **is** the trend
stratum — so a stratum fixed effect would be an intercept with no identifying variation. That is stated in
`SUMMARY.md` and on every fitted row's note. `dose_fits.csv` also reports, beside the naive slope, the
**ICC-attenuation-corrected** linear slope (Frisch–Waugh + the 2-regressor moment matrix with Var(ceiling)
scaled by λ, player-clustered bootstrap SE), reusing `security_models.primary.attenuation_reps/seed_offset`.

**Sign convention — a real discrepancy, recorded not smoothed.** The PI's stated expectation is that a
slipping player **realises less** of his ceiling — i.e. a **NEGATIVE** slope of log(game speed) on
`edp_trend`. `TASK.md` H1 and the `config.yaml::security_models` comment say the opposite (slipping ⇒ a
*smaller gap* ⇒ *higher* realisation ⇒ **POSITIVE**). This is the same contradiction D39/DV-7 recorded for
the fixture's planted sign, and it cannot be resolved by data. The decision here:

* the **pre-registered binary test keeps its own (H1) sign convention and wording** — untouched;
* the **dose panel states its own convention explicitly and identically everywhere** (a constant
  `sign_convention` column on every row, a plain-words block at the top of `SUMMARY.md`, and an annotation
  on the figure): `coef` = change in log(game peak speed) per **+1 EDP spot fallen**, holding the ceiling
  fixed; **NEGATIVE = the slipping player realises LESS** (the PI's stated expectation); **POSITIVE** is
  what the H1 wording predicts;
* no reader should have to infer the sign from a table.

**Capital confound (mandatory, not optional).** Draft capital is the obvious rival explanation: later picks
are worse players, and `edp_trend` is correlated with the level of EDP. Every specification is therefore
fitted under **seven** treatments — `unadjusted`, `pick_linear` (`overall_pick`, undrafted excluded because
no pick exists, and the note says so), `round_band` (`draft_position` as a categorical with `undrafted` kept
as its own level and `R1` as the reference), `pick_plus_level` (pick **and** the pre-Combine level, which is
a separate confound from capital), and three within-band fits (`within_R1`, `within_R2_R3`,
`within_R4plus`, the last labelled `R4plus_or_undrafted`). `overall_pick` comes from `players.parquet`
(`draft_overall_pick`) in a real run and from the fixture's `covariates.csv` column `overall_pick` in
synthetic mode; an undrafted player's pick is **never invented**. **If the slope dies once capital is
controlled, `SUMMARY.md` says so in one sentence — "the dose slope is draft position in disguise" — and
that is the finding.** The sentence is generated from the fitted numbers, never typed.

**Ambiguities in the spec, and how they were resolved (all recorded here, none silently simplified).**

1. **The segmented basis is discontinuous.** §3.5 mandates `f(d) = b1*d + b2*d*I(d >= 5)` and
   `slope_above_5 = b1 + b2`. That basis is *linear in `d`* on each side and therefore carries a **jump of
   `+5*b2` at the knot** (downward here, because `b2 < 0`); it does not nest a continuous "slope change at
   5". The alternative
   (`b1*d + b2*(d-5)*I(d>=5)`) is a continuous kink but is **not** the mandated design, so it was not used.
   The estimator is implemented exactly as specified, every kink row's note spells out the jump, and the
   `dose_kink` fixture plants the form the estimator actually nests (`-0.006*d*I(d>=5)`), so the recovery
   test is a genuine test of the machinery rather than an attenuated one. **This is the one place where the
   fixture table ("0 below 5, −0.006 per spot above 5") and the estimator could be read as inconsistent;
   the estimator wins, because the contract pins the columns and the SE formula.**
2. **Fixed bins are closed on the upper edge.** `<=0 / 1-2 / 3-4 / 5-9 / 10+` leaves gaps for a fractional
   `edp_trend` (`edp` is an expected position, so a difference can be fractional). The bins are implemented
   as a **total** partition `(-inf,0] (0,2] (2,4] (4,9] (9,inf)` — identical to the labelled bins on integer
   trends, and no row can fall through a gap. Stated in `SUMMARY.md`.
3. **`season_scope` in the emitted tables is the PANEL axis** (`all_seasons` / `rookie_only`, both fitted in
   one invocation), not the season-type scope: that stays on the existing `--scope` CLI knob (D49), and a
   non-default scope writes to a suffixed directory, exactly as stage 07 does.
4. **Thin discipline is applied to the treatment-specific sample.** `pick_linear` and `pick_plus_level`
   exclude undrafted rows, and the within-band fits use one band, so `n_obs`/`n_players` are reported *per
   treatment*; a cell is fitted only when both clear `dose.min_obs` (40) and `dose.min_players` (15).
5. **Fixture nuisance noise.** The four dose regimes use a smaller player-level gap SD and ceiling
   measurement noise than the legacy fixture (`dose.fixture.dose_gap_player_sd` /
   `dose_measurement_noise_sd`). The planted dose is small (−0.002 log units per spot), and in a single
   fixture replication a large nuisance draw that happens to correlate with the deterministic dose grid
   would swamp it. The effort-level noise is untouched, so the clustered-SE machinery is still exercised.
   The legacy `planted|null|selection` fixtures are **byte-identical** (verified): every new RNG draw lives
   in a `DOSE_REGIMES` branch and `overall_pick` is written **only** for the dose regimes.
6. **`common.STAGE_CONFIG_SUBTREES` was not extended.** The default for an unlisted stage already hashes
   every top-level config key except `paths`/`logging`/`mode`, so a `dose:` change invalidates the stage's
   checkpoint. Adding an explicit entry would only *narrow* the fingerprint.

**The figure is supplementary.** `fig_dose_response.png` (300 dpi, single panel, zero-based y-axis, no
break, no dual axis) is written into the stage's own output directory and is **not** one of the 5 writeup
figures; `deliverables.figures_max: 5` and the `FIGURES` list in the notebook are unchanged. Notebook §12
saves it to `outputs/figures/fig_dose_response.png` **only** when the source is FULL, otherwise to
`outputs/figures/synthetic/` with `_SYNTHETIC_PREVIEW` in the name; with no FULL output it prints a
`NO RESULT` block (D41) instead of numbers.

**What is NOT claimed here.** No result. The Pi has no full game artefacts, so the panel is proven on the
four committed dose fixtures (recovery / null / kink / capital) and in sample mode only. The
`dose_capital` fixture demonstrates the failure mode the panel is built to expose: unadjusted
−0.0039 vs pick-adjusted −0.00007. The real numbers require the full run.

---

## D51 — 2026-10-25 — Task pivot: in-game decay of DB effort relative to the Combine ceiling

**Decision.** The active task is now **"In-game decay of DB performance relative to the Combine ceiling"**
(`TASK.md`, rewritten 2026-10-25). The previous WR/TE draft-stock ("security") task is archived verbatim at
`notes/archive-tasks/TASK-wr-security-2026-10-20.md` and remains in git history. All security-task outputs,
config blocks and stage code stay in the repo as legacy; they are **not** part of the new paper.

**Why.** Conor set the new task and deadline. Only one Kaggle entry will be submitted; the DB-decay question
uses the Combine tracking asset directly and does not depend on the external draft-board data (which was a
data blocker, `notes/blockers.md` Phase 0.5).

**Consequences.**
1. New run order: `01_audit -> 02_combine_ceiling -> 00_thresholds -> 03_game_efforts -> 04_models -> 05_validate`.
2. New config block `db_decay:`; existing keys are preserved (merge, never clobber).
3. `notes/prereg.md` must be rewritten for the DB-decay estimands before any full-data run. The frozen
   security prereg is superseded; keep it as `notes/archive-tasks/prereg-wr-security.md`.
4. Prior combine-fatigue finding (no within-session signal) is **not** evidence about in-game decay and must
   not be cited as such.

---

## D52 — 2026-10-26 — The DB audit is a new stage; the legacy WR/TE audit and its tests stay untouched

**Decision.** The DB-decay audit is implemented as a **separate stage**, `src/01_db_audit.py`, writing to
`outputs/audit/db_decay/`. The legacy WR/TE audit (`src/01_audit.py`), its outputs and its **259 tests** are
left untouched. The DB stage adds **21** tests of its own; the suite collects **280** today
(`pytest --collect-only -q`, 2026-10-26: 280 total, 21 in `src/tests/test_db_audit.py`; 280 − 21 = 259
legacy). The ICC helper is **cross-checked numerically** between the two implementations so they cannot
drift apart. _(Corrected 2026-10-26: this entry first said 12 tests / 271 collected, the count before the
four review regressions in D58 were added; the legacy 259 was right.)_

**Why.** Zero regression risk on a green suite (the legacy stage has audit outputs the archived security
evidence still cites — `notes/archive-tasks/`, D37/D38), and a clean separation between **legacy** and
**active** evidence: a reader of `outputs/audit/db_decay/` must never wonder whether a number came from the
WR/TE population. Duplicating the ICC code is only safe if the duplicate is checked, hence the numerical
cross-check rather than a copied formula and a hope.

**Consequences.**
1. Run order for the active task is `01_db_audit -> 02_combine_ceiling -> 00_thresholds -> 03_game_efforts ->
   04_models -> 05_validate`; `01_audit` is not in it.
2. Two audit trees coexist: `outputs/audit/` (legacy, cited by the archived security work) and
   `outputs/audit/db_decay/` (active). Neither overwrites the other.
3. If the two ICC implementations ever disagree, the DB stage **fails** rather than picking a winner. There
   is no "trust the newer one" rule.
4. The legacy stage stays in the repo for as long as the archive cites it. It is documentation of prior
   evidence, not dead code to be tidied away.

---

## D53 — 2026-10-26 — `a0` exits by the ICC screen; the ceiling becomes single-feature

**Decision.** `a0` is **excluded** from the ceiling feature set by the pre-registered ICC screen, and the
ceiling is therefore **single-feature (`vmax`)**. It is **not** a stop condition. Recorded evidence
(`outputs/audit/db_decay/g_feature_icc.csv`, 40-yard dash, n = 77 players with ≥2 attempts, 154 attempts,
15 attempts excluded by the sanity rule):

| Feature | ICC(1,1) | 95% CI | Status |
|---|---:|---|---|
| `vmax` | **0.686** | [0.548, 0.788] | pass (≥ 0.60) |
| `a0` | **0.288** | [0.071, 0.480] | **fail → dropped** |
| `max_decel` | 0.313 | [0.098, 0.500] | fail at the 0.60 primary threshold; retained only as secondary/descriptive |

**Why.** `TASK.md` fixes the selection rule as **ICC then VIF, ≤3 features**. Under that rule a candidate that
fails ICC **leaves the feature set**; it does not kill the project. My earlier gate configuration made **every
candidate mandatory** (a per-feature `min_icc` check that turns one failure into a project-level NO_GO), which
is a different and stricter rule than the spec — and the audit duly returned `NO_GO` on `icc_primary_a0`
(`outputs/audit/db_decay/SUMMARY_db_decay.md`). The gate condition is the thing that was wrong, not the data:
`a0` really is unreliable (CI upper bound 0.480 is below the 0.60 threshold, and the point estimate is 0.288),
so dropping it is the correct application of the spec.

**Consequences.**
1. **Single-feature ceiling.** The load × ceiling interaction is tested on **`vmax` only**; the
   acceleration-moderated version of the hypothesis is not testable and must not be half-tested.
2. `a0`, `tau` and the cut/COD metrics are **reported descriptively** (they are already in the audit) and are
   **not** model inputs. The writeup may show the `a0` ICC as justification for the exclusion; it may not use
   `a0` as a moderator.
3. `vmax`'s CI **lower bound (0.548) sits below the 0.60 threshold**, so the ceiling is retained but known to
   be marginally reliable. The ceiling's **SE must propagate into every downstream estimate**, and a
   **measurement-error-corrected** estimate is **mandatory alongside** the naive one — a result that survives
   only the naive fit is not a result.
4. The audit's go/no-go gate must be re-expressed as **"at least one primary feature passes"** before a full
   run; as configured today it cannot return GO for a reason the spec does not require. (Action for the
   coder/PM; recorded here because it changes the gate's meaning, not just its threshold.)

---

## D54 — 2026-10-26 — The 40-yard sanity exclusion is pre-registered data hygiene, not post-hoc tuning

**Decision.** Attempts whose fitted `vmax > 12.5 yd/s` **or** `vmax > 1.5 ×` the player's observed peak speed
are excluded from the ceiling fit, and the exclusion is counted and reported. Both bounds **already existed in
`config.yaml` before the audit ran**: `envelope.flag_vmax_over_peak: 1.5` (from the D29 Combine-envelope work,
2026-10-12) and `db_decay.ceiling.sanity.vmax_yds_max: 12.5` / `.vmax_over_peak_max: 1.5` (written in the
DB-decay block; the config file on disk is dated 01:10, the audit outputs 02:16–02:20 on 2026-10-26). The
rule is therefore a **declared precondition**, not a response to the ICC result.

**Why.** The 2-parameter envelope is unidentifiable on a small number of noisy or thin attempts — a flat start
plus tracking noise lets `tau` run away and the fitted ceiling extrapolate far above human speed. Those fits
are not "fast players"; they are **failed fits**, and they enter the between-player variance as pure noise.

**Consequences.**
1. The effect is large and must be stated with both numbers: **raw ICC 0.118 vs filtered ICC 0.686** for
   `vmax` (`g_feature_icc.csv`, `icc_raw` vs `icc`). **15** failed fits dominate the between-player variance;
   without the rule the ceiling would be junk.
2. Because a filter that moves an ICC from 0.118 to 0.686 is exactly the kind of step that invites suspicion,
   the **four-variant sensitivity table** (filter on/off × each bound) and the **selection-bias table**
   (who gets excluded: class, position, attempt count) are **mandatory evidence**, not optional appendix.
3. **Any variant below threshold is reported, never suppressed.** If the unfiltered or single-bound variant
   fails, the writeup says so and explains why the pre-registered rule still stands.
4. The rule is never re-tuned after full-data results. If it needs to change, it changes as a dated decision
   with the pre-change value (anti-HARKing).

---

## D55 — 2026-10-26 — Provided game acceleration and Combine `dis` are unusable; kinematics come from `x`/`y`

**Decision.** **(a)** The provided game `a` is **never** used as acceleration; acceleration is derived from the
smoothed speed everywhere. **(b)** The provided Combine `dis` is **never** used for path length or speed;
distance and speed come from `x`/`y` in both pipelines. The provided `s`/`dis` survive **only as cross-checks**,
and their disagreement is reported.

**Evidence.** `outputs/audit/db_decay/f_reliability_game.csv` (n = 225,679 game frames): game `a` vs `ds/dt` of
the tracked speed has **r ≈ 0.0027**, **bias +1.733 yd/s²**, and breaches a 5% relative tolerance on **88.0%**
of frames — i.e. it is not the derivative of the speed in the same file, by any reading.
`outputs/audit/db_decay/f_reliability_combine.csv` (40-yd dash, 9,254 frames): Combine `dis` vs per-frame
|Δxy| has r = 0.980 but a **−0.048 yd** bias and breaches the 5% tolerance on **75.6%** of frames (it
under-counts low-speed displacement); Combine `s` and `a` on the straight-line dash are close to the
derivatives (r = 0.999 / 0.982), and on cutting drills `a` is not (r ≈ 0 to −0.10).

**Why.** A field named `a` that is not `ds/dt` silently breaks every downstream quantity that assumes it:
acceleration-based effort thresholds, deceleration metrics, and the exceedance test. The failure is worse
than noise (88% of frames, a one-directional +1.73 yd/s² bias) — it is a **different quantity**, most likely
including a lateral/centripetal component or a vendor smoothing that is not differenced the same way.

**Consequences.**
1. Stage 03's **cumulative load is `x`/`y` path length** (`prior_distance_yd_per100`), not a sum of `dis`.
2. The acceleration branch of the episode definition uses **derived** acceleration, so the audit's accel
   exceedance number (`h_exceedance_summary.csv`, accel rate ≈ 0.14% on the sample) is **provisional by
   construction** — audit item h says so in its own note — and must not be quoted as a ceiling result.
3. The Combine ceiling fit uses speed from `x`/`y`, smoothed, so the ceiling and the game effort are produced
   by the **same** kinematic chain. That is what makes the ratio comparable at all.
4. `notes/schema.md` §4 and `notes/blockers.md` carry the same finding; the blocker stays
   **OPEN-WORKAROUND**, not closed, because the underlying field is still wrong in the supplied data.

---

## D56 — 2026-10-26 — The DB analysis cannot rely on co-located players: no matchup-demand covariate

**Decision.** No nearest-receiver, ball-distance or matchup-demand covariate is built. Matchup demand is
declared a **confound** and handled by **within-game contrasts**, not by conditioning. Full-data tracking
coverage is `PENDING FULL RUN`.

**Evidence.** `player_play.csv` contains only the 497 combine-class players (mean **2.73 rows/play**,
`d_players_per_play.csv`), and the 2023 tracking **sample** has **1.16 players/play** (max 4, **0 plays with
all 22 tracked**, `audit_items.json` item d). The sample carries **no ball entity**, so ball location and
lateral reference are unavailable (`d_sample_blocked_paths.csv`). Within `player_play`, `dropback_duration`
is **0% non-null on DB rows** and 38.4% non-null on opponent rows (`d_field_availability.csv`) — the offense
row exists per play, but the *tracked* offense players largely do not.

**Why.** The intended covariate ("nearest receiver's speed / route depth") needs the receiver's frame-level
trajectory on the same play. With ~1 tracked player per play in the sample, that player is usually the DB
himself; a "nearest receiver" computed from those rows would be a fabrication. Even on full data the question
of whether tracking is **all-22 or combine-class-only** is open (T5/T8), so the covariate cannot be
pre-registered as if it will exist.

**Consequences.**
1. The max-effort subset's "within 5 yd of the ball carrier" condition **cannot** be computed from tracking in
   the sample; it falls back to the `first_contact` / `tackle` / `assist` flags in `player_play`
   (`notes/prereg.md` §4, T5).
2. Matchup demand is a **stated confound**: a DB covering a WR1 on a vertical route is not doing the same job
   as one covering a check-down. The design answer is the **within-player, within-game contrast** (early vs
   late, matched on play type/alignment/margin), which differences out the player's role but **not** a
   change in role across the game (garbage time, injuries elsewhere).
3. If the full run turns out to track all 22, the covariate can be added **only as a declared sensitivity**,
   not as a change to the pre-registered primary model.

---

## D57 — 2026-10-26 — Sample-vs-full discipline: the sample can never produce a reportable number

**Decision.** The 2023 tracking sample is **code-path exercise only**. It is a **2²⁰−1-row cap over
2023-class players only**, with **scattered** play coverage, and it can never yield a reportable number.
Every sample output is stamped `UNVALIDATED SAMPLE OUTPUT`, and the gate **cannot return GO from sample
mode**.

**Evidence.** 1,048,575 data rows (2²⁰−1) but **333 games interleaved** in file order, so it is not a prefix
(`d_sample_draw.csv`, hypothesis A **REFUTED**); every player has `draft_year = 2023` (hypothesis C,
"CONFIRMED (stronger)"); 197 units have < 40 frames and one unit has an internal > 150 ms gap (truncation
mid-play); `d_sample_blocked_paths.csv` lists six blocked paths including load accumulation ("plays are a
scattered subset (~11% of player_play plays) ... prior-effort load must come from the full run, not the
sample").

**Why.** Two independent failure modes: (i) **coverage** — a scattered ~11% subset of plays per game cannot
support a within-game cumulative-load variable, which is the exposure; (ii) **truncation** — cutting a play
mid-trajectory biases peaks **downward**, which is precisely the tail the whole design uses. A sample that
biases the outcome in a known direction is worse than a small sample.

**Consequences.**
1. `01_db_audit` in sample mode is allowed to be **NO_GO** (it is now, on the `a0` gate — D53) without
   blocking anything except its own sample run; the DB audit's Combine-side numbers are complete-data and
   usable as *evidence about the Combine file*, not as results about decay.
2. No threshold, no power figure, no effect size may be tuned against sample output; `00_thresholds` must
   stamp `FULL` in full mode and stage 03 asserts it.
3. `notes/prereg.md` contains no sample numbers by construction; the TBD register names the stage that
   produces each missing value.
4. Any writeup draft that quotes a sample number is wrong even if the number looks plausible.

---

## D58 — 2026-10-26 — Review of the DB audit: four defects fixed, and the legacy full-run gate is now red by design

**Decision.** The Phase-0/1 DB-decay audit was reviewed line by line and re-run from scratch. Four defects were
proven and fixed, each with a red→green regression test; the gate's *semantics* were left as specified (D53).

**(1) Item (e) divided by nothing.** `e_snap_distribution_by_quarter.csv` grouped `player_play` by
(position, quarter) only, so every DB-game collapsed into one row: `n_db_games = 1` and `snaps_median =
9,592` for CB Q1 — a population *total* presented as a per-game median (this was open as P27). The query now
groups by (player, game, quarter); CB Q1 reads 889 DB-games, median 11 snaps, IQR 7–15. Fixing it does not
change `e_attrition.csv` (75.8% all four quarters; 21.0% Q1-not-Q4), which was already per DB-game.

**(2) The power gate compared two different scales.** `_power_mc`/`_power_closed_form` return an MDE **per 1 SD
of load**, while the pre-registered smallest effect of interest is **0.01 effort fraction per 10 prior
high-intensity episodes** (prereg §11). `decide()` compared them directly, so at
`assumed_load_sd_per10 = 2.0` an MDE of 0.015 per SD (0.0075 per 10 episodes — adequate) was failed. The gate
now converts first (`mde_per_sd_load / assumed_load_sd_per10`) and reports both numbers. The verdict on this
cycle's sample run is unchanged (`0.0013 ≤ 0.01`, still `pass`), but the *rule* now matches the prereg.

**(3) Config validation had silent defaults in the gate's own inputs.** `ceiling_config()` promised "a missing
key is a loud `GateError`, never a silent default", but `smooth_window_frames`, `heading_window_frames`,
`cut_window_s` and `cut_min_speed_yds` were read with `.get(..., default)`, as were `audit.icc_ci_alpha`,
`audit.provenance_thresholds` and three `power.assumed_*` inputs. Any of those could change which attempts
enter the ICC screen (or whether a full run is allowed) without a word in the log. All of them are now
required by `validate_db_decay_config()` (presence + range, dotted key named in the error) and read by direct
index.

**(4) The DB stage overwrote the legacy audit decision.** `write_decision` wrote
`outputs/audit/audit_decision.json` — the exact file `src/gate.py` enforces for the legacy WR/TE game-side
stages — after a one-time backup. That contradicted D52 ("neither overwrites the other") and meant a legacy
full run would have been gated by the DB decision (`GO_PENDING_FULL_RUN` passes `gate.py`, so it would have
passed silently). The authoritative file is now `outputs/audit/db_decay/audit_decision.json`; the legacy path
is mirrored only when it is absent or already holds this stage's decision. The clobbered legacy file was
restored from `audit_decision.wr_security.json` (byte-identical).

**Also corrected (no behaviour change):** the sensitivity-table column `variant_is_preregistered` actually
meant "is the gate variant" (all four variants are pre-registered, D54) — renamed `is_gate_variant`. Item (f)
and item (h) emitted **sample-derived numbers** (75.6%, r≈0.003, +1.73 yd/s²) as a hardcoded note that would
have been printed verbatim in a full run; the notes now point at `f_reliability_*.csv` for the mode in hand.
The summary no longer claims `00_thresholds` "has not frozen" thresholds when provenance is `FULL`.

**The suite is not globally green, and that is not a defect in this stage.** 12 legacy full-mode tests
(`test_audit_full_mode`, `test_full_mode_e2e`, `test_speed_despike`, two in `test_envelope`, one in
`test_gate`) now fail with `FULL-RUN GATE BLOCKED … notes/prereg.md does not declare '**Status: FROZEN**'`.
Cause: the pivot replaced the frozen WR/TE `notes/prereg.md` (archived, still frozen, at
`notes/archive-tasks/prereg-wr-security.md`) with the DB-decay prereg, which is deliberately **DRAFT — NOT
FROZEN** until Conor approves it. `gate.py` is therefore doing exactly what `TASK.md` requires — refusing full
runs — and those tests assert the retired pipeline's precondition. The D52 claim "zero regression risk on a
green suite" is **wrong as stated** for those 12. Two remedies, both Conor's call, neither taken here:
(a) retire or explicitly skip the legacy full-mode tests (they cover a pipeline that can no longer run), or
(b) point their `paths.notes` at a frozen copy of the archived prereg and re-assert the live-repo test as
"gate blocks while the active prereg is a draft". No DB-decay test and no other legacy test regressed:
`test_db_audit.py` 21/21, suite 268/280 passing, the 12 failures all being that one gate message.

**Consequences.**
1. `src/01_db_audit.py` now reads every gate input from validated config; adding a threshold means adding it
   to `config.yaml` and to the relevant key tuple, or the stage refuses to start.
2. Any consumer of the audit decision for the *active* task must read
   `outputs/audit/db_decay/audit_decision.json`; `src/gate.py` still points at the legacy path and will need a
   task-aware prereg/decision path before the DB game-side stages (03–05) can be gated.
3. P27 is closed. `notes/pending_full_run.md` keeps it as a closed item rather than deleting the history.

## D59 — 2026-10-26 — Leverage × ceiling task: foundations, parking, and provenance (coder pass 1)

**Context.** `TASK.md` was rewritten for a new question (does pre-snap leverage change how much of
the Combine-derived ceiling a receiver uses on max-effort routes?). The D51/D58 DB-decay work and the
older WR/TE security work are retired, not wrong. This decision records the structural choices made
while laying the new task's foundations.

**(1) The retired DB-decay code is parked, not deleted.** `git mv` (history preserved):
`src/01_db_audit.py`, `src/synthetic_db_decay_fixture.py` and `src/tests/test_db_audit.py` moved to
`src/legacy_db_decay/`. The DB-decay task answered a different question (defensive-back effort decay);
keeping it in the import path would let two audited pipelines share file names and confuse the gate.

**(2) The retired ceiling-v1 modules are parked.** Every other old module and its test moved to
`src/legacy_ceiling_v1/` (00b, 02–10, `common.py`, feature builders, `gate.py`, stage runner, all
synthetic fixture generators, `src/README.md`, and the 24 remaining `src/tests/test_*.py`). Nothing was
deleted. Legacy modules are outside the new pipeline's test scope by construction: `pytest.ini` sets
`testpaths = tests`, so only the new top-level suite is collected. Import failures in parked code are
expected and are not chased; they are provenance, not a supported product. `src/legacy_ceiling_v1/common.py`
still imports under the old name, which is fine because nothing in the new pipeline imports it.

**(3) One shared helper module: `src/lc_common.py`.** The new task gets a single helper surface for
config, paths, disk guard, DuckDB, parquet conversion, provenance writers, run log, checkpoints,
hashing and schema checks. Rationale: the previous cycle accumulated `common.py`, `stage_runner.py`,
`gate.py`, `run_diagnostics.py` with overlapping responsibilities, and sample-stamping was left to
callers. Here the stamp lives *inside* `write_csv`/`write_json`, keyed off `mode`; a caller cannot emit
an unstamped sample artefact even if it tries. Two deliberate consequences:
* `write_csv` always adds a leading `provenance` column; full-mode rows say `FULL`, sample-mode rows say
  `PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT`. The schema is stable across modes.
* `write_json` always adds a leading `provenance` key. Sample values contain both the level and the
  literal stamp, satisfying the two requirements at once (level for logic, phrase for humans).

**(4) New config is namespaced, never rewritten.** `config.yaml` keeps every existing block
(`paths`, `files`, `runtime`, `audit`, `envelope`, `game`, `models`, `validate`, `external`, `security`,
`route_robustness`, `draft_target`, `dose`, `deliverables`, `db_decay`) untouched. One new top-level
block, `bdb27_lc:`, holds paths, files, seeds, runtime, thresholds, audit, ceiling, leverage, efforts,
power, gate, prereg and fixture parameters. Reason: other agents' history and checkpoints depend on the
old keys; deleting them would silently change old provenance. Code for the new task reads only
`bdb27_lc` via `lc_common.lc(cfg)`. Duplicated constants that the brief requires in two places
(`icc_min`, `vif_max`, `max_features`, `min_attempts_per_player`, `min_receivers_both_extremes`) are
declared in both `thresholds` and `ceiling`/`gate`; tests assert they agree so drift is impossible.

**(5) `dis` is declared unusable, with numeric evidence.** It is not yards and is not monotone: across
793 40-yard attempts the per-attempt `dis` span is 0.699–1.436 (median 1.136) while `x` spans 40.433 yd;
in one traced attempt `dis` peaks at 0.835 then falls to 0.813 as `x` rises to 51.0 yd. The pipeline
derives distance from `x`,`y` and avoids `dis` entirely. `EXPECTED_SCHEMAS` marks the unit
`UNRESOLVED (NOT yards; do not use)`. See `notes/schema.md` §4.

**(6) There is no timeouts field, and we do not invent one.** Header checks over all seven files find
no `timeout` column; `play_description` only mentions the word in prose. We accept the gap because the
provided pre-snap win probability already embeds timeout state, and leverage is defined from pre-snap
WP only. Recorded as an accepted gap, not a blocker.

**(7) Thresholds are placeholders, and the code cannot pretend otherwise.** Every constant is a
design choice under `bdb27_lc`, never estimated from the sample or from outcomes. `00_thresholds.py`
writes `outputs/thresholds.json` with `PLACEHOLDER_SAMPLE` provenance in sample mode. In full mode it
refuses to write `FULL` unless the full tracking input exists and its parsed row count exceeds the
provided sample cap (1,048,575 = 2²⁰−1); otherwise it exits non-zero naming the missing files or the
actual row count. `require_full_provenance` is the assertion scripts 03–05 will call; it is tested now
in both directions.

**(8) No timeouts on stages.** Stages are checkpointed per `stage + mode + config fingerprint`
(`data/interim/checkpoints/`) and skip completed work unless `--force`. We deliberately do **not** add
wall-clock timeouts: this Pi is slow and heavily loaded (root FS 99 % full, swap nearly full), so a
timeout would convert a slow-but-correct full run into a false failure. The PM watchdog covers stalls,
not the code.

**(9) Brief corrections found while grounding the schema.** Three facts in the PM brief were wrong or
imprecise, and the measurements win:
* PLAYER rows never exceed `s = 11.854 yd/s` / `a = 10.718 yd/s²`; the 25.79 / 44.6 extremes are BALL
  rows. The >12 yd/s sanity rule therefore has zero triggers in the real PLAYER data (it still runs).
* FORTY attempt labels are not limited to {1,2}: labels 3 and 4 occur, even though each player has only
  one or two attempts. Attempt is a trial id, not a count.
* There are 56 distinct `drill_name` values, not 62.

**(10) Legacy run log migrated once, non-destructively.** The old `outputs/run_log.csv` used a
different header (`timestamp,stage,...,peak_ram_mb,error,notes`), so appending the new
`stage,mode,provenance,wall_s,peak_rss_mb,when,notes` rows would have corrupted the CSV. The legacy file
was moved to `outputs/run_log_legacy_2026-10-26.csv` (preserved, not deleted) and a fresh log started
with the new header. `outputs/` is gitignored; this is local run evidence only.

**(11) `pytest.ini` makes the new suite authoritative.** `testpaths = tests`, `addopts = -q`. The
parked suites are not collected by default, so a green board means the new pipeline is green rather than
"green except the retired pipeline".

---

## D60 — 2026-10-27 — Phases 1–2 implemented; sample gate is GO_PENDING_FULL_RUN (coder pass 2)

**(1) Ceiling core is shared, not duplicated.** `src/ceiling_core.py` owns the per-attempt
`vmax*(1-exp(-t/tau))` fit, the empirical-Bayes shrinkage, the ICC(1) calculation and the ICC/VIF feature
screen. `01_audit` calls it for item (h) and `02_combine_ceiling` calls it for the published feature table,
so the audit cannot disagree with the stage. Estimates are reported on the natural scale with a delta-method
SE; the pooled quantity is the log parameter (positivity preserved). Shrinkage uses
`theta_hat = mu + B*(ybar - mu)` with `B = tau2/(tau2 + se_ybar^2)`; `tau2` is the method-of-moments
between-minus-within variance. `se_log_a0` treats `vmax` and `tau` errors as independent (a documented
conservative approximation, not a claim of zero covariance).

**(2) Real Combine result (complete file, so this is not sample-limited).** 127 WR/TE players have usable
40-yard-dash fits (238 attempts; 4 attempts dropped for `r2 < 0.90`, 1 for sinuosity > 1.10). Test-retest
ICC: **vmax 0.725, tau 0.263, a0 0.193**. Only `vmax` passes `icc_min = 0.50`; the VIF screen then keeps
`vmax` alone (tau and a0 are mechanically collinear with it). This is weaker than the old
`peak_speed` ICC of 0.975 because fitting a curve and pooling attempts adds parameter noise; it still
clears the gate. `02` writes `outputs/ceiling/lc/player_ceiling.csv` (one row per player-feature, SE
included) and `summary.json`.

**(3) The audit has its own output path and its own gate.** `01_audit` writes only
`outputs/audit/lc/{a..j}.csv`, `decision.json` and `SUMMARY.md`; it never touches
`outputs/audit/db_decay/` or the retired `outputs/audit/*` files. The gate verdict is
`GO_PENDING_FULL_RUN`, not GO: items (c frame-level), (g), the load half of (i), the in-game half of (h)
and the empirical half of (j) need full tracking and are marked `PENDING FULL RUN` with no imputed value.
Items (a), (b), (d), (e), (f), the ICC half of (h) and the play-level diagnostic in (i) are evaluated.

**(4) Item (c) is stronger than the old audit.** `player_play.csv` is complete, so matched players, games,
plays and route snaps are evaluated there: **WR 105/106, TE 38/42** matched; WR median 254 route snaps,
TE 212. Only the frame-level match needs tracking. On sample tracking the frame-level match is 38 WR / 12
TE and is explicitly non-representative.

**(5) Leverage is definable without external data.** Provided pre-snap home WP covers 100% of plays
(314,194 / 314,194 rows; 3 nulls are in rows with no play-level loss), is constant within every play, and
`4*p*(1-p)` is used. No external WP/EP model and no post-snap `*_added` column enters the definition.
Play-level leverage-time correlation is **-0.331**; the gate criterion is residual collinearity after load,
which stays pending because load is effort-level.

**(6) Power warning, not a hidden pass.** At the configured assumption (within-player SD 0.10 log speed, 40
efforts) the analytic MDE is **0.0443**, above the 0.03 target; **88 efforts/player** are needed for MDE
0.03. Matched route snaps suggest this is plausible (median 254 / 212), but the empirical MDE needs item
(g) counts. This is recorded in `j_power.csv` and must be checked before a full run. It is not converted
into a GO.

**(7) Effort extraction uses a non-post-treatment depth control.** `lc_efforts` records
`route_displacement_yd`, but the model does **not** control for it: over a fixed route duration,
displacement is mechanically a function of speed and would absorb the outcome. `lc_models` controls route
family, time-to-throw, score margin, down, distance and prior efforts. The full-data stage must derive
designed route depth at the break before adding a depth control; this is logged as a design gap, not
papered over.

**(8) The confounded synthetic world now has a real confound.** The old generator made leverage correlate
with time but gave time no effect on speed, so a naive model could not be fooled. `synthetic_lc_fixture`
now plants a within-player speed decline with elapsed time (`beta_time = -0.06`) in the confounded world
while keeping `beta_leverage = 0`; leverage-time correlation is ~0.92. The naive within-player slope is
about **-0.061** (p < 0.001); the time/load-orthogonalised slope is about **-0.006** (p = 0.59). Planting
world recovers **+0.053** against a planted 0.05; null world **+0.0001** (p = 0.86). The orthogonalisation
demeans and standardises time/load *within player* — a global slope plus player intercepts does not remove
player-specific time trends, which was the first version's bug.

**(9) Every artefact goes through a provenance writer.** `lc_common.write_markdown` was added so
`SUMMARY.md` is stamped by the shared writer like CSV/JSON; the caller cannot emit an unstamped sample
document. `write_csv`/`write_json` already stamp internally. Test coverage asserts the stamp on every
audit CSV, the decision, the ceiling CSV/summary and the Markdown header.

**(10) `01_audit` no longer raises in full mode for item (g).** Full-mode effort counting needs the
summary that `03_game_efforts` writes; the audit returns `PENDING FULL RUN` with that instruction instead
of crashing, and a full run re-audits after 03.

## D61 — 2026-10-27 — Route depth is *depth at the break*, realised from tracking

**Decision (Conor, 2026-10-27).** The model's route-depth control is **option 2 of four**: depth is
realised geometrically from the 10 Hz tracking as *downfield displacement at the receiver's break*, not
route family alone (option 1), a design-predicted depth by route label (option 3), or depth bins
(option 4). Alignment and cushion were also considered and are **not** depth measures. The definition is
frozen in `bdb27_lc.route_depth`. **There is no pre-registration in this project** (Conor, 2026-10-27):
the design record is `TASK.md` + `notes/decisions.md`, and `notes/prereg.md` is archived history that is
deliberately left untouched (D61 §9).

**(1) Why realised start-to-end displacement was rejected.** Over a fixed route duration, start-to-end
displacement is mechanically a function of speed: it is the same quantity that generates peak speed, so
controlling for it would absorb the outcome (a collider). On the sample's 3,055 receiver route tracks
(UNVALIDATED), `corr(max speed, start-to-end displacement) = 0.82`. It stays descriptive
(`route_displacement_yd`) and is not a control.

**(2) Why `pass_length` was rejected.** It is not a depth field. It is the literal string `"NA"` in
**129,458 of 314,197** `player_play` rows (41.2%), and where present it tracks the *thrown ball*, not the
route design: median `pass_length` on GO routes is **6.0 yd** (HITCH 6.0, CROSS 5.0, POST 7.0, SLANT 5.0).
Those are target distances on completed/incomplete throws, not stem depths. Not usable as a control.

**(3) The exact definition (frozen constants, all in `bdb27_lc.route_depth`).**
*Superseded on resolution only (D62): the break is 35 deg over 0.4 s, not 45 deg over 0.3 s. Every other
step below is unchanged and still current.*

1. Rotate each play into the play frame: `+x` = downfield. `play_direction == "right"` means field `+x`
   is downfield; `"left"` means `-x`. The rotation also flips lateral `y` so handedness is preserved. The
   origin is the receiver's **position at the snap frame** (tracking `ball_snap`; a play-level event time
   is the fallback for rows whose event is recorded on another player).
2. Smooth `x` and `y` with the **existing** centred rolling-median smoother
   (`thresholds.smoothing.window_frames = 5`, i.e. 0.5 s). There is no second smoothing convention; a test
   asserts the two config values agree.
3. Heading `theta(t)` = direction of the centred displacement over a **0.2 s** window, interpolated onto
   the actual frame times. Frames whose implied speed is below 2.0 yd/s get no heading (undefined, not
   guessed).
4. Stem = at least **0.8 s** of post-snap motion. No break is searched before that.
5. Break = the first frame at/after the stem where the cumulative `|dtheta|` over a sliding **0.3 s**
   window reaches **45 deg**, and the heading over the following **0.3 s** stays within **20 deg** of its
   own circular mean. The break timestamp is the **first frame of that sustained change** (the algorithm
   walks back over frames that already carry the post-break heading, at most one break-window).
6. Depth at break = downfield displacement at that frame, in yards, relative to the snap position.
7. A route with no detectable break gets `break_found = False` and a **missing** depth. Nothing is
   imputed; route family carries it. There is no fallback to displacement, `pass_length` or a design
   prediction.
8. A break after the ball is thrown is flagged `break_after_throw`. The throw frame is the tracking
   `pass_forward` event (play-level fallback), reused rather than re-derived. The flag never deletes a
   row: sensitivity reports the model both with and without post-throw breaks.
9. Model entry: **route family always + centred `break_depth_yd` + `break_found` indicator**. Depth is a
   control and is **reported, never interpreted**. In the design matrix the depth term is centred on the
   detected-break mean and coded 0 for no-break rows, with the indicator carrying those rows. That is
   reference coding for a missing value, **not** an imputed depth: the efforts table keeps NaN for every
   no-break route and a test asserts it.

**(4) Validation on the synthetic fixture (truth known).** The fixture now walks every route at its
planted speed along an exact two-segment path: a straight downfield stem, then a corner at a planted
depth. `GO`, `WHEEL` and `FLAT` plant no break. Results at the frozen constants:

* 204 planted breaks across seven route families; **204/204 detected**.
* Depth recovery: **median absolute error 0.18 yd, maximum 0.91 yd** (tolerance ±1 yd). The residual
  bias is the median smoother clipping a sharp reversal (HITCH has median −0.64 yd).
* 84 no-break route snaps: **0 false positives**, all `break_found = False`, **no depth fabricated**.
* 48 late breaks planted after the 2.0 s throw: **all 48 flagged** `break_after_throw`.
* Specification curve (±10 deg on 45 deg × ±0.1 s on 0.3 s): every **one-step** perturbation recovers
  ≥99.5% of planted breaks with max error ≤0.91 yd; the strictest double perturbation (55 deg, 0.2 s)
  recovers 84.3% because a 60 deg corner spends most of a 0.2 s window below 55 deg. Depths of detected
  breaks move by ≤0.3 yd (median) across the curve. The frozen point is not threshold-art; the short
  window is the fragile axis.
* Determinism: same seed → byte-identical tables (test).

**(5) Sample coverage at 45 deg/0.3 s (UNVALIDATED SAMPLE OUTPUT — not a finding, and not a tuning input).**
*Superseded by D62 §(3), which reruns the same stage at 35 deg/0.4 s.*
3,054 WR/TE
route snaps with tracking (WR 2,623; TE 431; 143 players). **89 breaks (2.9%)**: WR 74 (2.8%), TE 15
(3.5%). Of the 81 breaks with a known throw frame, **62 (76.5%) are after the throw**. Only **27 breaks
(0.9% of route snaps)** are before the throw. Coverage by family (count / median / IQR, detected breaks
only): CORNER 2 / 33.5 / 29.4–37.6; CROSS 5 / 12.8 / 11.3–17.9; FLAT 5 / 7.2 / 4.1–7.9; GO 17 / 19.2 /
15.9–27.3; HITCH 21 / 15.3 / 12.3–18.2; IN 11 / 9.8 / 5.8–16.1; OUT 18 / 12.9 / 8.4–15.7; POST 4 / 30.8 /
27.8–33.1; SCREEN 1 / 4.1; SLANT 5 / 7.1 / 7.0–12.0; WHEEL 0. The declared curve moves coverage between
0.5% (55 deg, 0.2 s) and 16.6% (35 deg, 0.4 s) at 45/0.3 s = 3.1%.

**(6) What the coverage numbers mean (open design question for Conor).** *Resolved by D62: Conor chose
option (b), the relaxed window. This paragraph is kept as the record of the question.* The frozen
constants are *literal* in the brief and were implemented exactly, but the sample tracking's routes turn
**gradually** (typical maximum cumulative turn ~20 deg per 0.3 s), not as sharp 45-deg cuts. As a
consequence the depth control exists for ~3% of routes, and for most of those the "break" is a
post-throw adjustment rather than the designed cut. If that generalises, the model's depth control is
effectively route family + a rare indicator, and the continuous depth coefficient is estimated off a few
dozen pre-throw breaks — not a usable control. This is **not** a reason to tune on the sample (TASK.md
forbids it): it is a design risk that must be decided **before** the full run. The options are
(a) keep the definition as specified and accept near-zero depth coverage; (b) relax the *window* (the fragile axis) —
35 deg/0.4 s reaches 16.6% on the sample; (c) interpret the criterion as *total* turn since the snap
(which would give near-100% coverage but is a different definition); (d) drop the continuous depth term
and declare route family + break indicator as the control, reporting depth descriptively. Conor's call.

**(7) Fixture design choices.** *Superseded by D62 §(5): the fixture now also plants 38 deg and 42 deg
corners inside the 35–45 deg band.* Planted post-break angles are 60–120 deg, deliberately clear of the
45 deg threshold, because a 45 deg geometric corner is exactly at the estimator's resolution limit
(smoothing consumes a few degrees). This is a fixture choice, not a change to the detector; the sample
run is what shows how real 45 deg routes behave. The fixture's speed profile is unchanged, so D60's
planted leverage recovery still holds (re-verified by the suite).

**(8) Power is settled, and stays a stated limitation.** The smallest effect of interest stays **0.03**
(`bdb27_lc.power.smallest_effect_of_interest`); it was not raised and no minimum-efforts requirement was
imposed. At the configured 40 efforts/player the achieved analytic MDE is **0.0443 log-speed** (D60),
above the smallest effect of interest. That is recorded as a **stated limitation** in `TASK.md` and
below, not converted into a gate: the design accepts that only effects at/above ~0.044 are reliably
distinguishable, and the full-run report must state the achieved MDE next to every interval. What did not
change: the estimand, the primary hypothesis, the leverage definition, the ceiling feature and the effort
definition. Depth enters only as a control.

**(9) No pre-registration; the LC "prereg" gate is retired (Conor, 2026-10-27).** This project has **no
prereg stage and no prereg deliverable**. `notes/prereg.md` is archived history and is left exactly as it
was (it still says DRAFT / not frozen); no stage reads it, and nothing in this cycle edited it. The
`bdb27_lc.prereg` block that declared `required_before: full` was a wrong gate condition: it was removed
from `config.yaml` rather than repointed at a new document. The gate that remains is evidence-based —
`00_thresholds` refuses to stamp `FULL` provenance on sample-sized input. A real blocker is **missing
required data or fields**, never a missing document; `notes/blockers.md` records only those.

## D62 — 2026-10-27 — Break resolution relaxed to 35 deg / 0.4 s (D61 option 2)

**Decision (Conor, 2026-10-27).** The route-depth control keeps D61's *depth at the break* definition, but
the break is now a cumulative **|dtheta| >= 35 deg within 0.4 s** — **option 2** of the four put to him in
D61 §6. Rejected: (1) freezing 45 deg/0.3 s and accepting 2.9% coverage / 0.9% pre-throw; (3)
reinterpreting the criterion as *total* turn since the snap (near-100% coverage but a different
definition); (4) dropping the continuous term for route family + alignment + cushion. Option 2 was chosen
because the specification curve already showed 35 deg/0.4 s at 16.6% on the sampled tracks, i.e. the
coverage problem was a resolution problem, not a definition problem. The purpose of this cycle was to
confirm that on the full sample stage and measure what it does to the pre-throw rate.

**(1) Exactly two constants changed.** In `bdb27_lc.route_depth`: `break_angle_deg` 45.0 -> **35.0** and
`break_window_s` 0.3 -> **0.4**. **Nothing else moved**: the play-frame rotation and snap origin, the
5-frame centred rolling-median smoother, the 0.2 s heading window, the 2.0 yd/s heading-speed floor, the
0.8 s stem, the 20 deg/0.3 s sustained-heading requirement, the `max_gap_s` rule, the "timestamp = first
frame of the sustained change" rule, the throw-frame flagging and the model entry (family + centred depth
+ indicator) are all as frozen in D61. `test_config_break_constants_are_the_chosen_relaxation` asserts
every one of those values, so a future edit cannot drift them silently. One variable at a time: the
constants were **not** tuned further after this run (TASK.md forbids tuning on sample output), and no
other threshold was touched.

**(2) Declared curve.** The robustness block (`[35, 45, 55] deg` x `[0.2, 0.3, 0.4] s`) is unchanged as a
*set*; the chosen point is now flagged `primary` and sorted first in `route_depth_sensitivity.csv` and the
`SUMMARY.md` table, so every table names the row the answer uses.

**(3) Sample-stage evidence (UNVALIDATED SAMPLE OUTPUT — not a finding, not a tuning input).** Rerun of
`src/03_route_depth.py --mode sample` on the same 3,054 WR/TE route snaps with tracking (WR 2,623; TE 431;
143 players; 967 games). Old numbers (45/0.3) alongside:

| quantity | 45 deg / 0.3 s | 35 deg / 0.4 s |
|---|---:|---:|
| breaks detected | 89 (2.9%) | **448 (14.7%)** |
| WR | 74 / 2,623 (2.8%) | **374 / 2,623 (14.3%)** |
| TE | 15 / 431 (3.5%) | **74 / 431 (17.2%)** |
| breaks with a known throw frame | 81 / 89 (91.0%) | 401 / 448 (89.5%) |
| ... of those, before the throw | 19 (23.5%) | **164 (40.9%)** |
| pre-throw share of route snaps (known throw only) | 0.6% | **5.4%** |
| post-throw share of known-throw breaks | 76.5% | **59.1%** |
| module `break_rate_before_throw` (includes breaks with no throw frame) | 27 (0.9%) | **211 (6.9%)** |
| median break depth (IQR) | — | 10.94 yd (5.27–17.37) |
| median break time from snap (IQR) | — | 3.45 s (2.30–4.60) |

The module definition of "pre-throw" (`break_found & ~break_after_throw`) includes 47 breaks on routes
with **no** tracking throw event; the strict count — breaks on routes whose throw frame is known, occurring
before it — is **164**, and that is the number to quote. Coverage rose **5.0x** overall and the pre-throw
count rose **8.6x** (19 -> 164) while the post-throw share fell from 76.5% to 59.1%: the relaxation is
finding earlier, designed cuts, not only late adjustments.

Break depth by route family at 35 deg/0.4 s (detected breaks only; count / median / IQR yd): CORNER 17 /
24.87 / 20.27–28.10; CROSS 16 / 10.08 / 1.19–17.50; FLAT 27 / 4.32 / 2.16–6.14; GO 66 / 18.89 /
14.50–24.56; HITCH 93 / 11.50 / 6.71–16.49; IN 76 / 10.04 / 5.49–12.99; OUT 72 / 7.04 / 4.58–12.33;
POST 23 / 21.20 / 18.23–29.99; SCREEN 15 / 1.00 / 0.27–3.00; SLANT 42 / 5.63 / 4.16–11.50; WHEEL 1 /
19.60 (single). The ordering is the one a route tree predicts (CORNER/POST deep, OUT/FLAT/SCREEN shallow),
which is weak evidence that the detector is reading geometry rather than noise.

**(4) How the depth estimate shifts versus 45 deg/0.3 s.** The 89 old detections are a **strict subset** of
the 448 (0 routes detected only by the old spec; 359 are new). On the 89 routes detected by **both**, the
depth estimate moves by **median -0.14 yd (IQR -0.33 to 0.00)**, mean |shift| 0.86 yd (dragged by one
SCREEN outlier at -2.80 yd), Pearson r = 0.954, and 14.6% of routes move by more than 1 yd; median depth
on that common set is 13.47 yd (new) vs 14.83 yd (old). So the relaxation mostly **adds** routes rather
than moving the depth of routes already detected — the timestamp rule "first frame of the sustained
change" walks back over the same frames at either threshold.

**(5) Fixture at the new resolution (truth known).** `ROUTE_BREAKS` now plants two *shallow* corners —
SLANT 38 deg and CORNER -42 deg — inside the 35–45 deg band that D61 §7 deliberately avoided as
unresolvable; the other five broken families stay at 60–120 deg. Results at 35 deg/0.4 s: **203/204 planted
breaks recovered (99.5%)** — 150/150 high-angle, **53/54 shallow**. Recovery error is reported honestly:
shallow band median 0.36 yd / max 0.78 yd versus high-angle median 0.08 yd / max 0.91 yd (one-sided
positive bias: the smoothed corner reaches the threshold one frame late). The single miss is a 38 deg
corner whose measured cumulative turn peaks at ~33 deg, just under the threshold — a genuine
near-resolution case, not a bug. No-break routes: 84, **0 false positives**, no depth fabricated. Late
breaks: 48 planted, all flagged. Curve: at 45 or 55 deg the shallow band is invisible *by definition*
(and 45/0.3 recovers 100% of what it can see), while 35 deg/0.2 s recovers only 86.8% — the **window** is
the fragile axis, which is why the relaxation moved both constants. On the breaks every spec detects, the
median depth spread across the whole curve is <= 0.75 yd. Under 0.05 yd position jitter 3/288 tracks flip
(1.0%): two 38 deg corners on the line and one 120 deg HITCH crossing the sustained-heading test.

**(6) Determinism.** Two consecutive `--mode sample` runs produced byte-identical `route_depth.csv`,
`route_depth_summary.json`, `route_depth_sensitivity.csv` and `SUMMARY.md` (sha256 of the table:
`d4fd13a3d2fe42e383dd8e4e1296ff283463d1e6ff0ee8f2868b537ec93474f9`). Fixed seed, single-threaded
DuckDB, stable sort order.

**(7) Plain judgement: the continuous depth control is still thin.** Coverage is 5x better and the
geometric ordering by family is right, so the control is no longer absurd. But **164 pre-throw breaks
across 143 players** is ~1.1 per player, and the headline fit admits post-throw breaks too (448, ~3 per
player). A continuous depth coefficient identified off that few breaks, on routes where the model already
carries family fixed effects, will have a wide interval and will mostly restate family. This is **reported,
not tuned away**: no further threshold change was made, and none should be. If the full run reproduces
~5% pre-throw coverage, the honest reading is that geometric depth is a **descriptive/rare indicator**, and
the control should revert to **option 4** (route family + break indicator, depth reported not estimated) —
which is also what the full run's interval will show in practice. Conversely, if the full tracking (967
games, 3.3x this sample) lifts pre-throw breaks to the high hundreds, the continuous term becomes
estimable; that is the one number to check first after the full `03_route_depth` run.

**(8) No prereg.** Per D61 §9 this project has no prereg stage and no prereg deliverable; nothing in this
cycle created, edited or reinstated one. The design record is `TASK.md` + this file.

## D63 — 2026-10-28 — The deliverable notebook runs the LC chain; the depth control gets a numeric keep-or-fall-back rule

**Decision (Conor, 2026-10-28).** The notebook is the deliverable and there is no command-line path
("notebooks only"). `notebooks/bdb27_full_run.ipynb` now runs the **current LC chain first, then the
preserved ceiling-v1 chain**, and it prints the route-depth evidence and the keep-or-fall-back rule.
This decision also supplies the number D62 §7 deliberately left out: the threshold for keeping the
continuous `break_depth_yd` control rather than falling back to D61 option 4.

**(1) What was broken, and since when.** The notebook was last edited at D50 (2026-10-20). At D59
(2026-10-26) the shared engine moved from `src/common.py` to `src/lc_common.py` and the ceiling-v1
modules were parked under `src/legacy_ceiling_v1/`. The notebook bootstrap required
`config.yaml + src/common.py`, so since D59 it stopped at cell 2 with
`RuntimeError: Cannot locate the BDB27 repo root`; its `STAGE_ORDER` was the ceiling-v1 chain and
contained no reference to `route_depth`, `lc_efforts`, `lc_models` or `lc_`. The deciding full-data
run was therefore reachable only from a shell — exactly the thing Conor's working rule forbids. This
is a regression introduced by the D59 move, not a notebook defect.

**(2) The real dependency order, read from the code.** No LC stage consumes another's output file.
`01_audit` reads Combine + `players`/`player_play` (+ sample tracking) and writes
`outputs/audit/lc/decision.json`; `02_combine_ceiling` reads only Combine files and writes
`outputs/ceiling/lc/`; `00_thresholds` writes `outputs/thresholds.json` from design constants and
refuses `FULL` on sample-sized input; `03_route_depth` reads `players`/`player_play` + tracking and
calls `lc_common.require_full_provenance`, which is the only cross-stage assertion in the chain. The
notebook keeps `TASK.md`'s declared phase order — `01_audit → 02_combine_ceiling → 00_thresholds →
03_route_depth` — because that is the order that reproduces the documented command sequence, and it
prints that reasoning next to `LC_STAGE_ORDER`.

**(3) Naming reconciled, not renamed.** Four pairs look like renames and are not:

| name | chain | output | reads |
|---|---|---|---|
| `02_combine_ceiling` | LC (current) | `outputs/ceiling/lc/` | Combine tracking/results/players |
| `02_combine_envelope` | ceiling-v1 (retired) | `outputs/envelope/` | Combine tracking/results |
| `03_route_depth` | LC (current) | `outputs/game/lc/route_depth/` | `player_play` + tracking |
| `03_game_demand` | ceiling-v1 (retired) | `outputs/game/` | legacy efforts + legacy thresholds |

`src/` is searched before `src/legacy_ceiling_v1/`, so `00_thresholds` and `01_audit` always resolve
to the **current** modules (there is no ceiling-v1 `01_audit` on disk any more; the legacy thresholds
module was rewritten for the LC task and keeps the name). Legacy names that no longer exist in `src/`
fall through to the parked copy.

**(4) Legacy preserved, with two location-only fixes in parked code.** Both were necessary to make
the preserved stages run at all, and neither changes behaviour:
* `legacy_ceiling_v1/common.py` — `REPO_ROOT` now walks up to the directory holding `config.yaml`.
  After the move, `parents[1]` was `src/`, so every relative path in `config.yaml` resolved to
  `src/data/raw/...` and **every** legacy stage died on a missing CSV.
* `legacy_ceiling_v1/run_diagnostics.py` — `check_capabilities()` looks next to itself for the D43/D44
  modules it inspects. It previously required `src/07_security_models.py` and
  `src/model_features.py`, which no longer exist there, so the checkout guard reported a clean tree as
  "STOP — this run needs safe odds ratios (D43)…" and blocked the whole notebook.
After both fixes the ceiling-v1 chain re-runs end-to-end in sample mode (8/8 stages `ok=True`, forced
where it matters), and §5–§12 keep reading its outputs.

**(5) Notebook changes.**
* `STAGE_ORDER = LC_STAGE_ORDER + LEGACY_STAGE_ORDER`; `GAME_SIDE` covers the full-data side of both
  chains. `02_combine_ceiling` is **not** in `GAME_SIDE`: the Combine file is complete, so a failure
  there must read `FAILED`, not `PENDING FULL RUN`.
* `run_stage()` passes `--config bdb27_run/config.yaml` explicitly to every stage and reloads the LC
  helpers (`lc_common`, `lc_route_depth`, `ceiling_core`, `lc_efforts`, `lc_models`) on every call, so
  D46's "always run the code on disk" holds for the new chain too.
* Stage status is read from each LC stage's own artefact (`audit/lc/decision.json`,
  `ceiling/lc/summary.json`, `game/lc/route_depth/route_depth_summary.json`); the LC checkpoint
  fingerprints (`data/interim/checkpoints/<stage>__<mode>__<fp16>.json`) are compared by the same D49
  rejection report the ceiling-v1 stages use.
* `CLEAN` now also clears those LC checkpoints. Without it, a cleaned LC stage would report
  "up to date" from a checkpoint whose outputs no longer exist — CLEAN promises a rebuild, so it is
  made one.
* §4's synthetic demo no longer calls `00_thresholds`: that name now resolves to the LC stage, which
  has no `--synthetic` mode. The ceiling-v1 synthetic path starts at `03_game_demand` (verified forced,
  both regimes).
* §7's run-log reader accepts `peak_rss_mb` (LC engine) or `peak_ram_mb` (ceiling-v1) — one log file,
  two writers.

**(6) The D63 rule (the number D62 left out).** Evaluated **on FULL data only**; sample counts are
descriptive and can neither satisfy nor fail it:

> KEEP the continuous centred `break_depth_yd` term iff
> **(a)** pre-throw breaks (break found, throw frame known, before the throw) **≥ 500**, **AND**
> **(b)** at least **6** route families each have **≥ 25** pre-throw breaks.
> Otherwise FALL BACK to D61 option 4: route family + `break_found` indicator, with depth reported
> descriptively and never estimated.

The constants live in `config.yaml` (`bdb27_lc.route_depth.decision_rule`) so nothing is typed into
the notebook. Derivation, using constants already in the project:
* A control must be estimated appreciably better than the estimand it adjusts for, or it adds variance
  without adjusting. Precision target δ = `power.smallest_effect_of_interest / 3` = **0.01** log-speed.
* σ = `power.assumed_within_player_sd_log_speed` = **0.10**, which is the value D60's analytic MDE
  implies (0.0443 ≈ 2.802 × 0.10 / √40 at 40 efforts/player).
* n = (z₀.₉₇₅ · σ / δ)² = (1.96 × 0.10 / 0.01)² = **384.2**.
* Allow a design effect of **1.3** for clustering of efforts within player: 384.2 × 1.3 = 499.4 →
  **500**. This also matches D62 §7's own language ("if the full tracking lifts pre-throw breaks to the
  high hundreds, the continuous term becomes estimable").
* Condition (b) exists because the model carries route-family fixed effects, so the depth slope is
  identified by **within-family** variation. At the tightest within-family spread observed in D62
  (σ_f ≈ 4 yd), n = 25 gives SE(median depth) ≈ 1.253 × 4 / √25 = **1.0 yd** — enough for a family to
  carry depth variation rather than one lever — and 6 of the 11 families is a majority of the route
  menu excluding the single-digit tails.

Consequences. If either condition fails on the full run, the depth column stays in the effort table
(missing for no-break routes, never imputed) and is reported descriptively by family; the model drops
the continuous term and keeps the indicator. The rule is allowed to return FALL BACK — D62 §7 expects
it to — and a re-run that fails (a) or (b) is an answer, not a failure. Nothing here was tuned on
sample output; the constants were fixed from precision arithmetic before the full run exists.

**(7) Provenance correction found while wiring (no design change).** D62 §3 describes the 3,054
tracked route snaps as "143 players; 967 games". The tracked table contains **50 players and 290
games**; 143/967 are the route-snap **key-table** counts *before* the tracking join (53,216 snaps, of
which 50,162 have no tracking). So 164 pre-throw breaks is 3.3 per tracked player, not 1.1. The
conclusion in D62 §7 does not change — total pre-throw coverage and per-family counts are the binding
constraints, not the per-player average — but §13 now prints both counts with their definitions so
they cannot be conflated again. Recorded here rather than silently edited into D62.

**(8) Acceptance evidence (sample).** Notebook executed end-to-end in `MODE="sample"`: **37 cells, 0
cell errors**, 67–100 s wall on the Pi, outputs cleared in the committed file. The LC chain rebuilt
from scratch after the config fingerprint change and reproduces D62 exactly: 3,054 tracked route
snaps, 448 breaks (14.67%), 401 breaks with a known throw frame, **164 pre-throw breaks (5.37%)**,
median break depth 10.94 yd — all `UNVALIDATED SAMPLE OUTPUT`, none of it a finding. `pytest tests`:
97 passed. No `data/` or `outputs/` artefact committed.

**(9) No prereg, no reviewer.** Per D61 §9 this project has no prereg stage and no reviewer role. The
only blocker recorded for this cycle is the absent full tracking files
(`game_tracking_{2023,24,25}.csv`), which is data, not a document — §13 names those three files when
it refuses to take the decision.

---

## D64 — 2026-10-29 — Full-run gate PASSED: KEEP the continuous break depth; pre-throw count corrected

**Decision.** The D63 rule was evaluated on the **full run** (executed on Conor's laptop; the deciding
run this project has been waiting for since D62). Both conditions pass, so the outcome is **KEEP
D63 option 2**: the continuous, centred `break_depth_yd` term stays in the effort model. The same entry
records a counting defect in the summary builder that would have overstated the pre-throw count had
anyone read that field instead of §13.

**(1) Full-run result.**
* Key table **53,216** tracked route snaps; **52,860** analysed after the tracking join — **356** snaps
  lost to the join.
* **143 players**, **967 games**.
* **7,526 breaks** (14.24% of analysed snaps); **6,472** have a known throw frame (86.0% of breaks).
* **2,614 PRE-THROW breaks** (4.95% of analysed snaps; 40.4% of known-throw breaks).
* Per player: mean **18.28**, median **10**, max **113**; **134/143** players with ≥ 1 pre-throw break,
  **124/143** with ≥ 2.

**(2) The D63 rule, evaluated.**
* (a) `≥ 500` pre-throw breaks: **2,614 ≥ 500 → PASS**.
* (b) at least 6 route families with `≥ 25` pre-throw breaks: **9 families → PASS** (6 required).
* Therefore the fallback in D62 §7 did **not** trigger, and the depth term is kept as declared. The
  gate constants, the D63 rule and every detector threshold are unchanged by this entry — only the
  counter below was wrong.

**(3) The sample's per-player thinness was an artefact, not a property of the data.** D62 §7 worried
that pre-throw breaks were too thin per player to support a continuous term. That reading came from the
sample: **1.1 per player** on a **50-player / 290-game** tracked sample. On the full run the median
player has **10** pre-throw breaks and **124/143** players have at least two. The constraint was sample
coverage (the 290-game tracking slice), not the phenomenon. Total coverage and per-family counts —
the binding constraints named in D62 §7 — pass comfortably.

**(4) Stability, and where the loss actually is.** The pre-throw rate is **5.37%** on the sample and
**4.95%** on the full run. That is the detector's strongest generalisation evidence so far: a 6x
increase in games barely moves the rate, against a definition frozen before either run (D61/D62). The
binding loss is the **throw join**, not break detection: **14% of breaks** (1,054 of 7,526) have no
usable throw frame, and those are excluded from the pre/post split by definition. Detection itself
finds breaks at a stable 14.2–14.7% across sample and full.

**(5) Effective analysis sample.** The **143 players / 967 games** are the route-snap key-table counts;
they are *not* 510 draft prospects. The 143 are the players with at least one tracked route snap, and
the full-run per-player figures above are over those 143. "510 prospects" is the Combine population and
must never be quoted as the analysed n for this route-depth question.

**(6) Design effect.** D63's **1.3** clustering allowance was an a-priori assumption (n = 384.2 × 1.3 →
500), not a fitted value. With **2,614** observed pre-throw breaks the margin over the 500 threshold is
**5.2x**. Even a design effect several times larger than 1.3 would leave condition (a) satisfied; the
decision does not depend on the clustering guess. Condition (b) passes with 9 families against 6, which
is the within-family identification D63 §6 required.

**(7) Counting defect fixed (the reason this entry is not just a result).** `coverage_summary()` in
`src/lc_route_depth.py` computed

```python
pre_throw = found & ~work["break_after_throw"].astype(bool)
```

which classifies **every** break with an unknown throw frame as "before the throw", because
`break_after_throw` is False by construction when the frame is unknown. D63 §6 defines a pre-throw break
as **break found, throw frame known, and before the throw**. On the sample the field reported **211**
instead of the true **164**: 47 found breaks have no known throw frame and were absorbed into "pre".
Applied to the full run the same expression would have added the **1,054** unknown-throw breaks,
reporting 3,668 for a true 2,614. The gate number quoted in (1) is §13's **independent** mask
(`found & known & ~after`), which was already correct and is unchanged; the stored field was the broken
one.

The fix:
* `pre_throw = found & throw_known & ~break_after_throw`;
* `after_throw = found & throw_known & break_after_throw` (made explicit rather than relying on the
  detector never setting the flag without a known frame);
* `unknown_throw = found & ~throw_known`;
* an **invariant asserted in the summary builder**: `n_found == n_pre + n_after + n_unknown`, so this
  class of double-count cannot return silently;
* denominators unchanged — `break_rate_before_throw` stays over **all analysed snaps** (164/3,054 =
  5.37% on the sample; 2,614/52,860 = 4.95% on the full run), so the reported rate and every downstream
  comparison are unaffected. The companion `median_break_depth_before_throw_yd` now also excludes the
  unknown-throw rows (sample: 7.05 → 5.75 yd); that is the same correction, not a new exclusion;
* a regression test (`test_pre_throw_count_requires_a_known_throw_frame`) reconstructs the sample split
  (164 pre / 237 post / 47 unknown / 2,606 no-break of 3,054), asserts the old mask gives **211**, the
  fixed field gives **164**, and the three found components sum to 448.

**Acceptance evidence.** `pytest tests`: **106 passed** (105 before this entry; the D63 note's "97" is
stale by the tests added since). The notebook's committed outputs stay cleared; no `data/` or
`outputs/` artefact is committed. §13 continues to compute pre-throw breaks from
`route_depth.csv` with its own mask and does not read the fragile summary field.

## D65 — 2026-10-29 — The LC chain ends in the estimand: `04_efforts` and `05_models` are real stages

**Decision.** Wire the two missing Phase 3/4 pieces as stages after `03_route_depth`:
`04_efforts` (one row per max-effort route) and `05_models` (the primary within-player leverage
estimand with its declared convergence fallbacks). Both join `LC_STAGE_ORDER` in the notebook, take
the same `--mode` / `--force` / `--config` contract, checkpoint through `lc_common`, and are
invocable from one Run All. The next full run therefore ends in the answer instead of stopping at
route depth.

**(1) Why this cycle exists.** The D64 full run produced the D63 gate result, but the notebook only
scheduled the LC chain through `03_route_depth`. `lc_efforts` and `lc_models` existed as library
code covered by synthetic recovery tests, yet they were not stages and not in `STAGE_ORDER`, so the
actual estimand was reachable only by hand. D65 closes that gap without changing any D61–D64
route-depth constant or the gate.

**(2) `04_efforts` — what it does.** It reads the WR/TE route keys from `player_play` (Parquet +
DuckDB), the tracking game by game (never the full file in pandas), and the **already-written**
`outputs/game/lc/route_depth/route_depth.csv` for the D61 depth control. It writes one row per
effort to `outputs/game/lc/efforts/efforts.csv`, plus `efforts_summary.json` and `SUMMARY.md`.
Each row carries: `max_speed_yds`, `peak_accel_yds2`, `route_family`, `break_depth_yd` and
`break_found`, `time_to_throw_s`, cumulative load before the effort (`prior_efforts`,
`prior_distance_yd`), `game_clock_s` / `game_elapsed_s`, `quarter`, `score_margin`, down and
distance, `field_position_yd` and `leverage` / `leverage_z` / `leverage_bin`. The outcome is
`log_max_speed`; leverage is `4 p (1-p)` from the pre-snap win probability only. No post-snap field
enters either definition. The effort gate (route extension >= 5 yd, duration >= 1.0 s) is applied
before writing and its removed rows are counted. `effort_seq` gives a dense per-player-per-game
index, so `(game_id, play_id, nfl_id, effort_seq)` is the effort key.

**(3) `05_models` — what it does.** It reads only `efforts.csv` and fits the TASK.md Phase 4
specification: `log_max_speed ~ leverage_z + route family + centred break_depth_yd + break_found +
time_to_throw + prior_efforts (cumulative load) + quarter + score margin + down + yards_to_go`,
player-clustered SEs, estimand = population coefficient on leverage. Depth is a **control** and the
output labels it as such. The convergence ladder is tried in the declared order and the rung used is
written into `model_result.json`:
1. `uncorrelated_re` — `(1|player) + (0+leverage|player)`, uncorrelated variance components;
2. `player_fixed_effects` — the existing player-FE OLS with clustered SE;
3. `random_intercept` — random intercept only.
A rung is rejected if it does not converge, returns a non-finite estimate or a zero clustered SE, or
puts the random-slope variance on the boundary (zero). The achieved analytic MDE is computed from
`z * sigma / sqrt(efforts_per_player)` and reported next to the interval; D60's 0.0443 at 40
efforts/player is restated. It is stated, never a gate.

**(4) Provenance and edge cases.** Both stages call `require_full_provenance`; in full mode
`04_efforts` additionally refuses a `route_depth.csv` whose rows are not `FULL`, and `05_models`
refuses an `efforts.csv` whose `provenance` column is not `FULL`. Sample runs are allowed and every
artefact is stamped `UNVALIDATED SAMPLE OUTPUT`. Players with one leverage bin, single-effort
players, players with no usable effort and games with no tracking are counted in the summaries and
do not crash a stage.

**(5) Notebook.** §14 reads `outputs/game/lc/efforts/` and `outputs/game/lc/models/` and renders
the effort audit (efforts and players per leverage bin, players with >= 5 efforts in both extremes,
per-player counts) and the model result (leverage coefficient with clustered CI, the fallback used,
the achieved MDE). It prints an explicit "depth is a CONTROL, not a finding" line. No number is
typed by hand. `LC_STAGE_ORDER` is now `01_audit -> 02_combine_ceiling -> 00_thresholds ->
03_route_depth -> 04_efforts -> 05_models`; `04_efforts` and `05_models` are in `GAME_SIDE`, and
their status is read from their own artefacts.

**(6) Sample-mode numbers (plumbing; NOT findings).** On the provided sample:
* key table 53,216 route-snap rows / 143 players / 967 games; **2,993 efforts** from **50 players**
  in **289 games** after the tracking join (`7,576` key rows had no tracking; `61` removed by the
  effort gate; `0` missing pre-snap WP);
* leverage bins 998 low / 997 medium / 998 high; **36** players have >= 5 efforts in both extremes;
  per-player efforts mean 59.9, median 46, range 5–174;
* leverage coefficient **+0.0070** log-speed per 1 within-player SD, clustered SE 0.0054,
  95% CI **[-0.0039, +0.0180]**, p = 0.2006; fallback used **`uncorrelated_re`** (rung 1 converged);
* achieved MDE **0.0362** at 59.9 efforts/player (D60's 0.0443 at 40);
* depth control: centred `break_depth_yd` 0.0105 (SE 0.0012), `break_found` -0.1510 (SE 0.0203) —
  reported, not interpreted.
These are sample-mode diagnostics and must not be quoted as results.

**(7) Tests.** `pytest tests`: **111 passed** (106 at HEAD). New tests in `tests/test_efforts_stage.py`
cover: exactly one row per effort key and dense `effort_seq`; the model stage refusing non-FULL
provenance in full mode; the fallback ladder being exercised in order; the MDE identity reproducing
D60; and a degenerate one-bin leverage table being labelled rather than crashing. The notebook
contract test now asserts the six-stage LC chain. The notebook was executed end to end in
`MODE="sample"` with **0 cell errors**; committed outputs stay cleared and no `data/` or `outputs/`
artefact is committed.

**(8) Phase 4/5 items deliberately NOT wired this cycle.** Recorded in `model_result.json` as
`not_yet_wired` and in §14, in this order: matched high-vs-low leverage design (CEM + clustered
bootstrap); headroom moderation (`leverage x baseline utilization`); ceiling-bound check; dose-response
across leverage bins; per-player leverage slopes with the reliability screen; TE replication; and all
of Phase 5 validation (leverage permutation, time/load orthogonalised refit, placebos,
tactical-demand and garbage-time checks, equivalence test). Nothing is stubbed into the notebook as
if it ran.

**(9) No prereg, no reviewer.** Per D61 §9 this project has no pre-registration stage and no reviewer
role. Self-review plus mechanical validation (schema, determinism, edge cases, regression) is the
gate; the real blockers are missing data or missing required fields, not documents.

---

## D66 — 2026-10-29 — Phase 5 is wired: validation, and a salvage review of the dead run's draft

**Decision.** Phase 5 validation is a real stage. `src/06_validation.py` runs after `05_models` in
`LC_STAGE_ORDER`, takes the same `--mode` / `--force` / `--config` contract, checkpoints through
`lc_common`, refuses non-`FULL` provenance in full mode (both the effort table and the `05_models`
result), and writes `outputs/game/lc/validation/`: `validation_result.json`,
`permutation_null.csv`, `placebo_checks.csv`, `garbage_time.csv`, `sensitivity_curve.csv`,
`orthogonalisation.csv`, `SUMMARY.md`. Notebook §15 renders it from disk with no hand-typed numbers.

**Why salvage rather than a clean rewrite.** A previous PM run died at 24 minutes and left one
untracked artefact, `src/lc_validation.py` (860 lines), containing every Phase-5 function by name but
never executed. It was reviewed as another author's draft and repaired in place; the function surface,
the estimator choice and most prose survived. The config block it assumed did **not** exist, which is
why it had never run.

**(1) The garbage-time rule, restated here because the log only pointed at a retired document.**
A play is garbage time when `|score_margin| >= 21` in Q4 or later, **or** `|score_margin| >= 28` in any
quarter. The margins live in exactly one place, `bdb27_lc.leverage.garbage_time` (`q4_margin: 21`,
`any_quarter_margin: 28`); `validation.garbage_time` supplies only `rule` and `q4: 4`. A conflicting
margin in the validation block raises rather than overriding, and a missing `quarter` or
`score_margin` column is reported as "rule not evaluable" rather than silently returning "no garbage
time". The rule is reported with and without the exclusion, plus the garbage-time share of each
leverage bin (blowouts are low-leverage, so the exclusion is not leverage-neutral).

**(2) The equivalence rule, restated.** TOST against the **margin 0.02** log-speed
(`power.equivalence_margin`, duplicated in `validation.equivalence` and asserted equal), alpha 0.05,
with the smallest effect of interest **0.03**. A null is *informative* only when the whole 95% interval
lies inside ±0.03; equivalence at 0.02 is reported separately. The stage returns an explicit verdict
string, not a bare p-value.

**(3) What the draft got wrong.** Thirteen defects, fixed in place:

1. **The config block it read did not exist.** `validation_block()` returned `{}`, so every permutation
   returned `status="no block columns available"` with a NaN p-value — and `run_validation` would have
   written a "successful" validation artefact containing nothing. The block is now declared in
   `config.yaml` (shuffle count, block columns, orthogonalisation controls, equivalence, garbage-time
   reference, placebo outcomes, sensitivity lists) and a missing block raises.
2. **The sensitivity grid was invented, not declared.** Booleans were read with `.get(key, True)`
   defaults and the 120 s window was hard-coded, so absent config produced specs nobody declared. The
   grid is now built only from configured lists; a missing key shortens the curve.
3. **The `median` bin cut kept every row** (`(x >= median) | (x < median)`), so that spec was a copy of
   the primary mislabelled as a sensitivity.
4. **Bin-cutoff specs fitted a continuous slope inside the extreme bins** while describing themselves
   as bin cutoffs. They are now explicit high-vs-low indicators (`_leverage_high_vs_low`), with the
   comparison labelled in the CSV; `beta_per_within_player_sd` is not reported for them.
5. **`apply_spec` silently no-opped when `route_displacement_yd` was absent** — the effort-threshold
   sensitivity would have been the primary again. A missing column now yields an empty frame and a
   "no rows after filter" status.
6. **Garbage-time margins could be silently ignored.** The merge dropped a local `q4_margin`, and the
   mask defaulted 21/28/4 in code. Margins now come from one place, conflicts raise, and the defaults
   are gone.
7. **A missing `quarter`/`score_margin` produced an all-False mask**, i.e. a false claim of "no garbage
   time". It is now an error reported into the result.
8. **The orthogonalised fit bypassed `fit_orthogonalised`**: it called `fit_within_player` on a
   hand-made residual, with a second demean+least-squares implementation for the R² diagnostic. The fit
   now goes through `lc_models.fit_orthogonalised`, and `lc_models.fit_orthogonalised` gained an
   `orth_controls` argument so the configured control list is authoritative rather than the module
   constant. The diagnostic is a variance ratio plus within-player correlations.
9. **`fit_design` was an unverified second FE estimator.** It is kept — 2,000 refits cannot afford the
   full `design_matrix` each time — but a test pins its beta and clustered SE to
   `lc_models.fit_within_player` at 1e-10, so there is one estimator with a fast path.
10. **The edge-case counter called every single-effort player "zero within-player leverage variance"**
    (NaN std → `fillna(0)` → `<= 1e-12`). It now counts only players with >= 2 efforts and SD <= 1e-12,
    and writes that rule into the output next to the count.
11. **Unguarded `table["quarter"]` and `game_id` lookups** would have crashed the stage on a table
    without those columns. Both are guarded.
12. **`_shuffle` rebuilt a DataFrame and re-grouped inside every replicate.** Blocks are computed once;
    the stage runs 1,000 shuffles × 2 schemes on the sample in about 14 s.
13. **The equivalence verdicts were ambiguous** ("informative null" with no statement of which interval
    test produced it) and the margin was not checked against the `power` block. Verdicts are explicit,
    `null_is_informative` is boolean, and drift raises.

**(4) What was written new.** The config block; `src/06_validation.py`; notebook §15 and the
`LC_STAGE_ORDER` / `GAME_SIDE` / status-reader wiring; `tests/test_validation_stage.py` (19 tests) plus
2 notebook-contract tests; README_RUN and notebooks/README updates; this entry.

**(5) The validation design, stated once.** Every refit uses the declared `player_fixed_effects`
fallback so the observed statistic and its permutation null come from the same estimator. The mixed
rung is not refit 2,000 times; the permutation null would otherwise mix estimators across replicates.
The permutation preserves each block's leverage mean, so the null is **not forced to zero** — the exact
randomisation test compares the observed statistic with its orbit, and the null mean, SD, 2.5/97.5%
quantiles and the full 2,000-row distribution are written out rather than only the p-value.
`break_depth_yd` remains a control in the primary model and a *placebo outcome* here; those are
different questions and the output labels both.

**(6) Sample-mode numbers (plumbing; NOT findings).** On the provided sample (2,993 efforts / 50
players / 289 games):

* permutation, 1,000 shuffles: `player_game` p = **0.2388** (480 blocks, 87 rows in singletons),
  `route_family` p = **0.1518** (336 blocks, 75 singletons); observed FE beta +0.0036 (SE 0.0050);
* orthogonalisation: naive **+0.0036** → orthogonalised **+0.0026**, attenuation **27.2%**, within-player
  R² of leverage on time+load **0.168**, within-player correlations time **-0.349**, load **-0.356**;
  the effect does not survive;
* placebos: break depth -0.4124 (SE 0.6366, p 0.52, n 441 among detected breaks), `break_found` +0.0072
  (p 0.42), time to throw +0.0023 (p 0.37);
* garbage time 125/2,993 (4.18%), all in the low-leverage bin (12.5% of that bin); with +0.0036 vs
  without +0.0063;
* sensitivity curve: 8 estimable specs, all null; high-vs-low tercile -0.0015, quartile -0.0027;
* equivalence: primary +0.0070 (SE 0.0054), 95% CI [-0.0039, +0.0180], TOST p = 0.0104, equivalent at
  0.02, **verdict "informative null"** — i.e. on this sample the interval rules out ±0.03.

None of that is evidence about receivers. The equivalence verdict in particular is a statement about
the sample's precision, and the sample is not the population the claim is about.

**(7) What still is not wired.** Phase 4: the CEM matched high-vs-low design with a clustered bootstrap;
headroom moderation (`leverage × baseline utilization`, with utilization estimated in an independent
half of low-leverage efforts); the ceiling-bound exceedance check; dose-response across leverage bins;
per-player leverage slopes with the reliability screen; TE replication. Phase 5: the non-max-effort
placebo, which needs the gate-rejected rows written by `04_efforts` (they are counted, not written) —
a new extraction pass, not a refit of the effort table. All are listed in `validation_result.json`
under `not_wired` and printed in §15; nothing is stubbed as if it ran.

**(8) Verification.** `pytest tests`: **132 passed** (111 at HEAD; +19 validation, +2 notebook
contract). The confounded synthetic world is run through the **wired stage** (not just the library) with
a deliberately stronger confound (`beta_time=-0.18`, `corr=0.9`): the naive FE estimate is -0.0185
(p = 0.015) and the orthogonalised estimate is -0.0023 (p = 0.75), attenuation 0.87. A separate test
uses the default confounded fixture. The planted world gives p < 0.01 and the null world p > 0.05. The
notebook executes end to end in `MODE="sample"` with **0 cell errors** (20 code cells, 2 min 07 s) and
committed outputs stay cleared. No `data/` or `outputs/` artefact is committed; the tracked writeup
figures were restored to HEAD after the execution.

**(9) No prereg, no reviewer.** Per D61 §9 there is no pre-registration stage and no reviewer role.
Self-review plus mechanical validation (schema, determinism, edge cases, regression) is the gate; the
real blockers are missing data or missing required fields, not documents.

## D67 — 2026-10-29 — Phase 4 is wired: two salvaged failures fixed at the root, and the chain now runs Phases 1–5

**(1) What this cycle was.** The previous Phase 4 run died after 34 minutes with no commit, leaving
`src/lc_phase4.py` (~56 KB), `src/07_phase4.py` (~18 KB), `tests/test_phase4_stage.py` (~26 KB) and a
41-line `config.yaml` block untracked in the working tree, the suite at 159 collected (was 132) and two
tests failing. This cycle salvaged that work rather than rebuilding it: the two failures were diagnosed,
fixed, the suite went green, and the library + stage + tests + config were committed immediately
(`7e72c8f`) — before the notebook section — because two consecutive runs had already died holding hours
of uncommitted work. Incremental commits are the protection; the notebook was a second commit.

**(2) Failure 1 — `test_player_with_one_high_leverage_effort_is_counted_not_crashed`.**
Expected `n_players_contributing == 6`, got 5. Root cause was the **fixture, not the estimator**: the
test built 6 players × 10 efforts over the exact-match strata (route family × depth bin × quarter ×
load bin — up to 48 cells), and at that density one planted player legitimately had no stratum holding
both a high and a low effort, so it contributed a one-sided cell and no pair. That is the design doing
its job and being counted (`n_strata_one_sided`). Fix: the fixture now gives the world 24 efforts per
player (the `_base_table` default) so every planted player reliably pairs; the assertion `== 6` is
unchanged, and the test additionally asserts the new `n_players_without_pair == 1`, which is exactly the
edge-case player the test is about. No assertion was weakened.

**(3) Failure 2 — `test_headroom_recovers_the_planted_moderation` (and the empty-slice warnings).**
Expected `n_players == 40`, got 25. Root cause was again the fixture: leverage was drawn
`N(0, 1)` per effort, so only ~24% of 16 efforts fell below the −0.7 low-leverage threshold; roughly
15/40 players had fewer than the configured `min_low_efforts_for_split: 4` low-leverage efforts and two
had none at all. The independent-half utilisation baseline therefore could not be formed for them
(correctly excluded), and the fixture's own fixed-point loop called `np.median` on an empty low slice —
the source of the "Mean of empty slice" / "invalid value encountered in scalar divide" RuntimeWarnings.
The estimator itself was right: on the 25 eligible players it recovered 0.297 against a planted 0.30.
Fix: `_headroom_table` now guarantees `n_low = max(4, n_efforts // 3)` low-leverage efforts per player
(sign forced below the threshold), so every planted player supports the split and the recovery assertion
holds at 40 players. The `np.median` guard is no longer reachable from the fixture.

**Hardening that came out of the diagnosis** (the "report, never silently drop" rule):
* `half_split_utilisation` returns `n_players_excluded` and a per-player `excluded_players` list with
  status "not estimable" and the reason (fewer low-leverage efforts than the split needs), and the count
  is printed in §16. Before, those players vanished without a trace.
* `matched_design` now reports `n_players_eligible` / `n_players_without_pair` alongside
  `n_players_contributing`, so a one-sided player is named rather than implied.
* `dose_response` marks an unestimable bin contrast `not estimable` and sets `monotone: null` /
  "not evaluable" instead of computing monotonicity over whichever bins happened to survive.
* `tactical_demand`'s time-to-throw comparison reports "not estimable" when the ±window sample is empty
  or has no within-player variation, instead of returning a NaN coefficient under `status: ok`.

**(4) A note on the "empty stratum must be reported" rule.** The matched design already reported empty
cells (`n_strata_one_sided`) and a collapsed sample (`status: collapsed`, `n_pairs == 0`); the remaining
silent paths were the four above. After the fixes, an empty stratum or an empty half produces an entry
with a status and a reason, and no NaN reaches a reported coefficient.

**(5) Sample-mode numbers (plumbing; NOT findings).** Sample: 2,993 efforts / 50 players / 289 games.
* matched (CEM within player, 1,000-rep clustered bootstrap): **130 pairs**, 30 of 50 eligible players
  contribute a pair (20 have no paired cell), 120 strata matched, 1,317 one-sided, 35/37 unmatched
  high/low; matched estimate **−0.0204** log-speed (SE 0.0180, 95% CI [−0.0529, +0.0172]);
* regression on the matched rows: **−0.0021** per within-player SD → **−0.0046** high-minus-low
  (matched-sample `leverage_z` gap 2.1495, 260 rows, 9 controls). The two differ in estimand by
  construction — the match is unadjusted beyond the strata — and both are labelled as such;
* headroom: 36 players with a baseline (14 excluded, ≥4 low-leverage efforts required), 395 baseline
  rows, 1,931 comparison rows; `leverage × baseline utilization` naive **+0.1052** (SE 0.0729),
  λ = **0.388**, corrected **+0.2712** (SE 0.1879); `× vmax` +0.0035 (λ 0.979); `× tau` −0.0230
  (λ 0.967);
* ceiling bound: 2,404 efforts carry a ceiling (589 do not); observed and predicted-at-high exceedance
  rates are **0.0000 in every bin**; max predicted fraction at high leverage 0.9629 (observed max
  0.8961) — no sample evidence of a binding ceiling, and that is a sample statement only;
* dose-response: medium −0.0069, high +0.0017 against low; **not monotone**, trend +0.0024 per bin
  (SE 0.0045, p 0.60);
* per-player slopes: **withheld**, reliability 0.000 against threshold 0.50 on 45 eligible players — no
  per-player table is emitted, which is the gate working;
* TE replication: −0.0013 (SE 0.0076, 95% CI [−0.0181, +0.0154]), 429 efforts / 12 players, labelled
  **exploratory** (threshold 30 players);
* time-to-throw contrast ran; **NOT RUN**: `defender_distance` — `defender_distance_yd` is not written by
  `04_efforts` and is not proxied from post-snap positions.

**(6) Checks that ran vs did not.** Ran: matched design + regression-on-matched, headroom moderation
(three moderators, naive + corrected), ceiling bound, dose-response, reliability-gated per-player
slopes, TE replication, time-to-throw. NOT RUN: defender distance (field absent, named in
`checks_not_run.csv` and §16). Still not wired anywhere: the Phase 5 non-max-effort placebo, which needs
the gate-rejected rows written by `04_efforts` (they are counted, not written) — a new extraction pass,
not a refit.

**(7) Pipeline completeness.** With `07_phase4` wired into `LC_STAGE_ORDER`, `GAME_SIDE`, the helper
reload list, the status reader and a new §16, the LC chain now runs **Phases 1–5 end to end**:
`01_audit → 02_combine_ceiling → 00_thresholds → 03_route_depth → 04_efforts → 05_models →
06_validation → 07_phase4`. Every Phase 4 item in `TASK.md` is either estimated or explicitly NOT RUN
with its missing field. What is *not* complete is the **full-data run**: sample-mode results above are
plumbing and the full tracking files are still required before any of them is a finding.

**(8) Verification.** `pytest tests`: **159 passed** (was 132 at D66; +27 Phase 4 acceptance tests, of
which the two failing ones now pass without any assertion being relaxed). The notebook executes end to
end in `MODE="sample"` with **0 cell errors**, `07_phase4` reporting
`OK (sample) · matched ok (130 pairs) · dose monotone=False · slopes=withheld · 1 NOT RUN`, and committed
outputs stay cleared. No `data/` or `outputs/` artefact is committed. Commits: `7e72c8f` (library, stage,
tests, config) and the notebook/docs commit that follows this entry.

**(9) No prereg, no reviewer.** Unchanged from D61/D66: no pre-registration stage and no reviewer role
in this project. Self-review plus mechanical validation (schema, determinism, edge cases, regression) is
the gate; the real blockers are missing data or missing required fields, not documents.

## D68 — 2026-10-30 — Task pivots to DL pass-rush: Combine acceleration ceiling → pressure, and ceiling-relative load

**(1) New binding spec.** Conor supplied a complete new `TASK.md` (four consecutive messages, transcribed
verbatim). Subject changes from *do receivers use more of their ceiling in high-leverage moments* to
*does a defensive lineman's Combine acceleration ceiling predict time to pressure, and does
ceiling-relative load decay better than absolute load*. The old spec, its prereg and its
prereg-deviations are archived in `notes/archive-tasks/` (not deleted); the LC code chain, its config
block and its 159 tests are left intact and untouched by this decision.

**(2) Pre-registration returns.** The 2026-10-27 correction (D61) retired the prereg stage: no prereg,
no reviewer, self-review plus mechanical validation only. The new spec explicitly requires
`/notes/prereg.md` fixing Combine features, outcome definitions, censoring rule, both load definitions
and tuning procedure, controls, minimum snaps, comparison metric, smallest effect of interest and
reliability thresholds, and lists a missing prereg as a stop condition before a full run. Newer explicit
instruction wins: **the prereg gate is live again for this task**, and only gates `--mode full`. The
no-reviewer rule is unchanged — self-review plus mechanical validation remains the gate.

**(3) Namespace.** The new chain is `01_audit → 02_combine_ceiling → 00_thresholds → 03_game_snaps →
04_models → 05_validate`, which collides by name with the live LC chain (`00_thresholds`, `01_audit`,
`02_combine_ceiling`, `04_models`, `05_models` …). The new stages must live in their own namespace so
both chains stay runnable and the existing suite keeps passing; resolution is left to the cycle that
implements it, but "rename in place and break the old chain" is not acceptable without Conor.

**(4) Carried-forward risk, stated up front.** The archived audit measured ICC on WR/TE straight-line
attempts as **a0 0.193, tau 0.263, vmax 0.725, peak_speed 0.975, speed_at_20 0.969** — i.e. the derived
acceleration parameters were the *least* reliable features and were dropped in favour of vmax. This spec
makes a0 the primary feature and adds speed at 1.0 s. Those numbers are from a different position group
and a different feature construction, so they are not evidence against the new task — but if the DL audit
reproduces an a0 ICC near 0.2, the Phase 1 go/no-go condition "key Combine features have ICC below the
config threshold" fires and the task needs a rethink before any modelling. This is the single largest
known risk and it is settled by measurement in Phase 1, not by argument.

## D69 — 2026-10-30 — Ceiling stage: a0 is primary by design, not by screen vote

**(1) What triggered it.** The first `02_combine_ceiling` implementation (sample mode,
UNVALIDATED) computed the DL reliability table and then let its ICC/VIF screen choose the
feature set. It selected `vmax` + `peak_accel` and therefore emitted
`feature_gate = NO-GO_PRIMARY_NOT_SELECTED`, because `a0` lost the `max_features` race to
`vmax`/`speed_at_2_0`. Reported ICCs for DL straight-line attempts: **a0 0.532, vmax 0.950,
tau 0.566, speed@1.0 0.640, speed@2.0 0.814, peak_accel 0.633, 10-yd split not estimable**
(71 players, 136 attempts, 6 one-attempt players excluded). These are sample numbers and are
not interpreted.

**(2) Correction: the screen may rank alternates; it may not remove the estimand.**
`TASK.md` names the Part 1 estimand as "hazard ratio per 1 SD of the primary Combine
acceleration feature", and Phase 2 names `a0` first. A selection rule that can drop `a0` makes
Part 1 unestimable, so `a0` is retained unconditionally and the ICC/VIF screen ranks only the
*alternative operationalisations* (`speed@1.0`, `speed@2.0`, `ten_yd_split`, `tau`, `vmax`,
`peak_accel`). The gate fails only if `a0` itself is below `icc_min`.

**(3) Why VIF cannot arbitrate here.** `a0 = vmax/tau` by definition. `vmax`, `tau` and `a0`
are three coordinates of one fitted curve, not three covariates; their collinearity is
arithmetic, not empirical. They are therefore *alternative* measures used in a specification
curve (one per model), never joint regressors. VIF applies to the control set in Phase 4
(alignment, depth, blitz, blockers, down/distance, margin, size, position), not to the
ceiling-feature menu.

**(4) Degenerate diagnostic removed.** The stage reported "within-player correlation of
vmax and a0" as mean −0.69 / median −1.00. With two attempts per player a within-player
correlation is identically ±1, so the statistic is an artefact. Report instead the
between-player correlation of the shrunken estimates, and state that a within-player
correlation is not estimable for this cohort (no player has ≥3 attempts in the fit drill).

**(5) Reliability is borderline, and that is the finding.** a0 ICC 0.532 sits barely above the
0.50 floor on 2 attempts per player. Required from here: a player-clustered bootstrap CI for
each ICC; an attenuation factor `sqrt(ICC)` used explicitly in the prereg's power calculation;
and a measurement-error (SIMEX-style) sensitivity analysis for the Part 1 coefficient. If the
CI straddles 0.50 the honest verdict is CONDITIONAL, not GO, and the fallback (`speed@1.0`,
ICC 0.640) is proposed to Conor — never adopted silently, and `icc_min` is never lowered.

**(6) The carried-forward risk did not reproduce.** D68 flagged that the archived WR/TE audit
measured a0 ICC 0.193, which would have fired the go/no-go condition. For DL straight-line
attempts the measured value is 0.532 (sample, unvalidated). Different position group, different
attempt structure (2 attempts, ~54 frames each). The risk is reduced, not eliminated: 0.53 is
thin, and the full run must re-measure it before the prereg freezes the reliability claim.

**(7) Lateral mechanics stay exploratory.** Shuttle/cone entry speed, peak, exit speed, total
time and braking distance are computed for 33/33 and 46/47 attempts. Braking distance uses the
first *local* speed maximum (the global maximum is the closing sprint, which would leave the
metric defined for 4/33 attempts). No lateral feature may enter a confirmatory model.

**D69 addendum (2026-10-31, after the correction landed).** `feature_gate` is now
`PASS (a0 admitted first)`; selected = `a0, vmax, peak_accel`; sample ICCs unchanged
(a0 0.532, vmax 0.950, tau 0.566, speed@1.0 0.640, speed@2.0 0.814, peak_accel 0.633;
10-yd split not estimable; 71 players / 136 attempts / 6 one-attempt players excluded).
The between-player correlation of the shrunken estimates is **vmax–a0 r = 0.07**,
vmax–tau r = 0.46, vmax–speed@1.0 r = 0.33 (natural scale, n=71). The low vmax–a0
correlation is the substantive point: although a0 = vmax/tau identically, between-player
variation in tau is large enough that vmax and a0 are **not** interchangeable across
players, so a0 is a distinct exposure rather than a reparameterised vmax. (Natural scale is
used because the shrunken estimates are reported there; the log-scale analogue would be
the more usual elasticity statement and is a candidate addition at M2.)

## D70 — 2026-10-31 — Pre-registration frozen for the DL task

**(1) What was frozen.** `notes/prereg.md`, marked FROZEN 2026-10-31, gating
`--mode full` only. It fixes: primary exposure `a0` (unconditional, with the
alternates used one-per-model as a specification curve); the three outcome
definitions and their null semantics; the censoring rule (censor at
`dropback_duration`, never a covariate, inconsistent `ttp > dropback` set to
censored and counted); both load definitions with five-point grids, identical
recency weighting and identical tuning budgets, tuned inside training folds
only; the control list; the 100-snap minimum; the estimator ladder (Weibull AFT
with log-normal frailty, Cox-cluster and AFT-cluster as reported rungs);
player-grouped 5-fold CV with held-out log score and a player bootstrap;
smallest effects of interest (HR 1.10; 0.005 nats/snap) with equivalence
margins (1.05; 0.002); and the reliability floor (`icc_min` 0.50, never
lowered).

**(2) Rules, not numbers.** The prereg quotes the measured sample values (a0
ICC 0.532, MDE 1.075 attenuated) only to justify the *rules*. Where a rule
depends on a measurement it is written so a different full-data measurement
changes the verdict, not the analysis. If the full-run a0 ICC falls below 0.50
the outcome is NO-GO and `speed_at_1_0` is proposed to Conor; it is not adopted
automatically and the threshold is not lowered.

**(3) Why now.** TASK.md requires the prereg after the Phase 1 evidence and the
Phase 2 ICC, and before Phase 4. Both are in hand (audit gate CONDITIONAL,
ceiling gate PASS with a0 admitted first). No Phase 4 code exists yet, and no
full-data file exists on this host, so nothing has been seen that could bias
the freeze.

**(4) The "no prereg" guidance is stale for this task.** `TOOLS.md` still
records the 2026-10-27 process correction that retired the prereg stage. D68
already noted that the newer TASK.md explicitly reinstates it. Confirmed: for
this task the prereg gate is live, and `00_thresholds` carries
`prereg_path`/`prereg_frozen`. The no-reviewer rule is unchanged — self-review
plus mechanical validation is still the gate.

**(5) Deliberate omission.** The prereg does not name a stopping alpha
correction for the specification curve because the curve is descriptive; the
two confirmatory tests (P1, P2) are each reported with their own interval and
no multiplicity claim is made across them. If Conor wants a family-wise
correction for the pair, that is a design change and must be decided before the
full run, not after.

## D71 — 2026-10-31 — Load hook wired; evidence-doc corrections; clock fix

**(1) `dl_load.add_load_columns` exists and is the table-level contract.**
`dl_snaps._apply_load_module` was looking for `dl_load.add_load_columns` and
falling back to omission; the function now exists.  It owns all five
`LOAD_COLUMNS` and populates them from the module's existing arithmetic
(`_weighted_sum` / `recency_weight`), not a second formula:

| column | contract |
|---|---|
| `seconds_since_last_snap` | clock gap to the rusher's previous snap in the game; `NA` on the first snap and when a clock reading is unusable |
| `total_distance_before` | unweighted yards on earlier snaps of the game; per-snap distance = mean of the two matched in-game speeds × the fixed `game.post_snap_window_s` width |
| `snaps_in_drive` | earlier snaps in the same `drive_id`; `NA` because the snap table has no drive key (never back-filled from play order) |
| `absolute_load` | causal, recency-weighted yards above the frozen `chosen.absolute_speed_yds` |
| `relative_load` | same, above `fraction × v_ceiling_yds`; requires a per-row `v_ceiling_yds` |

The flat load columns still stay `NA` until a threshold is frozen inside a
training fold, exactly as `flat_load_column`/prereg §7 require; the hook reads
`load.chosen` when a stage supplies it.  The relative column additionally needs
the per-player ceiling on the row, so a future stage must join it; no ceiling
is invented here.

**Why causal evaluation.** The prereg definition is load *before* snap *s*, so
each snap is weighted against its own clock.  `absolute_load` takes one fixed
`seconds_ago` reference for the whole list, which would scale every row by a
snap-specific constant; the hook therefore re-bases the gap per focal snap and
calls `_weighted_sum`, the single shared arithmetic path.  Zero gaps are
allowed (the clock has 1 s resolution, so consecutive plays can share a
reading).  The fallback path is untouched: if `dl_load` is missing or broken,
`dl_snaps` still imports and still omits the columns and says so.

**(2) `game_clock_s` was 60× too large — fixed.**  `player_play.game_clock` is
`mm:ss`, but `read_csv_auto` types `"08:42"` as TIME `08:42:00`, so the hour
part holds *minutes*.  `_clock_seconds_sql` multiplied by 3600, making a 8:42
clock read 31,320 s instead of 522 s.  That would have inflated every rest gap
by 60 and silently defeated the 300 s load half-life.  The expression now
multiplies the hour part by 60 (and the minute part is seconds).  New test:
`test_game_clock_seconds_is_minutes_seconds_not_hours_minutes` (fixture 10:00
→ 600.0 s).  No committed output used the old column (the DL snap table had
never been written to `outputs/`), so nothing downstream is invalidated.

**(3) Evidence doc corrected: the Q3 null boundary is the dropback play, not
"live pass play".**  Reproduced on the sample (DuckDB, `player_play` +
`players`):

* DL rows 46,911 = 17,481 dropback + 29,430 non-dropback.
* `sack` / `blitzing` / `unblocked_pressure` nulls **on the dropback frame:
  0 / 0 / 0** (`d_null_semantics.csv` `null_rate_dropback = 0.0`).
* Non-dropback: 7,739 / 7,854 / 7,740 non-null, 21,691 / 21,576 / 21,690 null;
  the 352 `sack` / 351 `unblocked_pressure` nulls on live pass plays with no
  recorded dropback are the recorded residual (109 after the participation
  rule).

The snap table and every model require `censor_time = dropback_duration`, so
the dropback play **is** the analysis frame; on it these fields are complete and
the verdict is *not applicable outside, observed inside*.  The `UNRESOLVED`
label in the local item-d output is scored on the wider "live pass play"
diagnostic frame and is superseded for modelling by this entry.  Per the rule
that the doc follows the code, `dl_audit_ce.py` and `stage_01_audit.py` were
**not** changed: their wider-frame residual stays as a reported diagnostic, and
the document no longer claims it is the analysis boundary.

Three further doc errors were corrected in `notes/dl-evidence.md` §3/§7:
`quick_pressure` disagreement is 83 rows (not 15; agreement 1,396/1,479, with
15 separate `ttp`-without-`quick` rows), the DL `time_to_pressure` count is
1,455 plays / 1,494 rows (not 2,116), and the DL dropback hurry/ttp counts are
1,520 / 1,494 (not the all-position 3,879 / 3,804).  `notes/prereg.md` §4 now
states the dropback frame explicitly; **no rule changed** — the model set
already required a censorable dropback snap and never included non-dropback
plays.

**(4) Verification.**  Full suite green in `/home/conormalone/.venvs/bdb27`:
**338 passed, 0 failed in 82.9 s**.  New tests cover the wired hook
(order/rest, cumulative distance with missing speeds, frozen absolute load hand
arithmetic, relative load needing a per-row ceiling, `drive_id` requirement,
order invariance, missing clock, empty frame, schema rejection) plus the
integration path through `build_snaps` and the clock fix.

**(5) Still open (not blockers).**
* `snaps_in_drive` needs a drive key.  `player_play` has no drive column, and a
  possession-run proxy would be wrong whenever a play has no cohort row, so the
  column is `NA` and counted rather than guessed.  Stage 03 must add a
  `drive_id` (possession sequence from the full play list) before Part 2.
* Load is currently accumulated over **pass-rush snaps only**: run-defense
  snaps are not in the DL table, so "total distance" undercounts total work.
  Flagged for the full-run design, not fixed here.

## D72 — 2026-10-31 — Stage 03 lands; the drive key is real (possession sequence, D71 closed)

**(1) `drive_id` is derived from the play list, not from play order in the snap
table.** D71 refused a back-fill from play order, and this keeps that refusal:
the key is built over `player_play`'s distinct `(game_id, play_id)` plays — the
only play list the provided data contains — ordered by `play_id` within a game.
`play_id` order is validated against the quarter/clock order: **20 inversions
on the full sample** (pairs where the play_id order puts a later play at an
earlier clock reading; every disagreement is a clock that runs backwards, which
cannot be chronological), counted in `snaps_summary.json` rather than hidden.

**(2) The rule.** Within a game, a new drive starts on: the first observed play;
a change of `possession_team`; the play after an unknown possession; and **a
score change between consecutive observed plays with the same possession**.
The last clause is not a heuristic: any score (TD, FG, safety, defensive score)
ends the possession, so if the same team has the ball on both sides of a score
change, at least one possession of the opponent was played and is absent from
the cohort play list — the previous drive must have ended. Without it the two
drives merge; with it the key never splits a genuine drive, because a score
cannot occur inside one. Counted separately (`boundaries.score_change_same_possession`).

**(3) Missing / conflicting possession is never guessed.** A play whose
`possession_team` is null (or disagrees across rows) gets `drive_id = NA` and
the **next** play starts a new drive — an unknown possession could belong to
either team, so continuity across it cannot be asserted. Both cases are counted
(`n_plays_missing_possession`, `n_plays_conflicting_possession`), and every snap
on such a play carries `snaps_in_drive = NA`, never a zero. On the provided
sample there are **0** missing and **0** conflicting possession rows, so this
path is exercised by the tests only.

**(4) What it produces (sample mode, `PLACEHOLDER_SAMPLE`, not a finding).**
`dl_load.add_load_columns` now receives the key and populates
`snaps_in_drive`; `snaps.csv` is unchanged in schema (the key is an internal
helper column, dropped before emission). Sample run: 17,159 snaps, 87 rushers,
event rate 0.0821, **19,835 drives** over the 114,954-play list (median 20 per
game, min 1, max 34), boundaries first-play 1,001 / possession-change 17,547 /
score-change 1,287. `snaps_in_drive`: max 13, **0 snaps without a drive key**,
32 snaps `NA` from an unusable game clock for the sequence (kept and counted).
The emitted table is byte-identical across two `--force` runs.

**(5) Residual exposure (declared, not corrected).** The play list is the
cohort-observed list, not full participation: 11 games have <20 observed plays
and the median is 115/game. A possession that is entirely unobserved **and
contains no score** is invisible, so two of that team's drives can merge; the
score rule removes every merge that involves a score, which is the detectable
subset. `snaps_in_drive` is therefore a conservative lower bound on the true
drive position for those rare rows. The count is not estimable from the
provided data (the missing plays are, by definition, absent); it is recorded
here and in the stage summary rather than patched with a clock-gap heuristic.

**(6) Stage 03 (`src/dl/stage_03_snaps.py`).** Writes
`outputs/dl/game/snaps/snaps.csv` in the exact `dl_snaps.SNAP_COLUMNS` order and
`snaps_summary.json` with every exclusion counted (coverage rows, null censor
times, `ttp > censor` demoted to `event = 0` while kept, unmapped alignments,
snaps without a drive key, snaps whose sequence has no usable clock) plus the
per-column NA rates. Full mode refuses unless the config provenance is `FULL`,
`thresholds.json` carries `FULL` provenance, and `prereg.md` is `FROZEN`;
sample mode stamps `PLACEHOLDER_SAMPLE`, sets `reportable: false`, and refuses
to overwrite a `FULL` artefact. Nothing is zero-filled.

## D73 — 2026-10-31 — Stage 04 lands: estimators, tuning discipline, the SAMPLE gate verdict

**(1) What was built.** `src/dl/dl_survival.py` (Weibull AFT with a log-normal
frailty by Gauss–Hermite quadrature; the random-slope extension; a
cluster-robust AFT; Cox PH via statsmodels `PHReg`; a logistic random-intercept
model; a Gaussian random-intercept model; predictive log score and Harrell's C),
`src/dl/dl_models_core.py` (frame construction, the §7 load panel, player-grouped
folds, tuning inside the fold, the P2 fallback ladder, S1, E1, the §13/§15 gate),
`src/dl/stage_04_models.py` (the artefacts and the summary) and
`src/dl/dl_fixtures.py` (the four committed synthetic worlds). No new
dependency: lifelines is not used; the frailty model is a direct maximum-
likelihood implementation with an analytic gradient.

**(2) The sample cannot support a prereg-conformant P1, and the stage says so.**
On `outputs/dl/game/snaps/snaps.csv` (17,159 snaps, 1,409 events, 87 rushers)
the §8 control set is incomplete **by construction**: `blitz` 0/17,159,
`n_blockers` 0/17,159, `primary_blocker_group` absent, `pre_snap_depth_yd`
399/17,159. Complete cases on the required set = **0**. P1 is therefore reported
`NOT_ESTIMABLE` with the fields named; no reduced model is substituted, because
dropping §8 controls would change the estimand. P2 is `NOT_ESTIMABLE` for the
same reason plus the load columns (`absolute_load`, `relative_load`) being
100% `NA` — the per-game tracking sequences the load needs are absent from the
sample. The §15 verdict is **PENDING_FULL_RUN** in sample mode: every absent
field is one the full run derives from tracking. The same absence in a full run
is a **NO-GO** (`gate_evaluate` branches on mode; tested).

**(3) Design decisions inside the build (all logged, none tuned on a result).**
* A **constant column** is part of the design. Without it the Weibull scale is
  forced through the covariates and the shape parameter drifts to a corner
  (observed on the fixtures: k = 11.6, HR 7.6 against a planted 1.35). With the
  constant the same data recover the planted value.
* **Part 2's slope dimension uses a 20×7 product rule.** Prereg §10 fixes 20
  nodes for the *frailty*; it does not fix a slope node count. A 20×20 product
  rule is the literal extension but is ~7× the cost of the frailty model and
  makes the CV unaffordable. `models.slope_quadrature_nodes: 7`; the intercept
  dimension keeps 20. Every fit records both counts.
* **CV fits use one optimiser start and `cv_max_iter: 250`**
  (`cv_starts`, `cv_max_iter`); the reported fits keep `max_iter: 500` and two
  starts. The CV score is a prediction metric; the reported models are fitted
  with the full budget.
* **`models.full_run_fields`** names the fields the full run supplies
  (blitz, n_blockers, pre_snap_depth_yd, primary_blocker_group, both load
  columns). It exists only so the *sample* verdict can be PENDING rather than a
  false NO-GO; it cannot turn a full-run failure into a pass.
* **§9 fold rule**: players below 100 snaps never enter Part 2; a fold needs
  ≥10 such rushers or the fold count is reduced and logged. On the 40-player
  fixtures this reduces 5 folds to 4 (logged in `fold_info`).
* **P2 bootstrap** resamples players with all their snaps (1,000 seeded reps)
  and evaluates the already-fitted per-snap scores. It is the sampling
  variability of the CV metric, not a refit-uncertainty interval; the artefact
  says so.
* **E1** is a player-clustered *multiplier* bootstrap (same exponential weights
  in both equations, so the covariance between the get-off and survival
  coefficients is preserved) rather than 1,000 refits. It is labelled
  DESCRIPTIVE and is suppressed (`NOT_ESTIMABLE`) when the information matrix
  is not positive definite or the bootstrap spread is implausible.
* **S1** carries a0's uncertainty by posterior draws: 200 seeded draws of `a0`
  from its shrunken estimate and SE, each evaluated with a one-step Newton move
  from the naive fit (labelled as such), plus the model-based slope reliability
  `var(slope)/(var(slope)+mean(SE²))`.
* **Verdict vocabulary** gained `GO_PART1_ONLY`: §15 says a snap-coverage
  failure drops Part 2 and lets P1 continue, which is neither a GO nor a NO-GO.

**(4) The four fixture worlds (committed, ~2.7 MB total).** `planted` (relative
decay), `absolute` (absolute decay), `null`, `censoring` (~2% pressures).
Fixture truths were chosen so the machinery is *identifiable* at 40 players:
planted a0 HR 2.0, frailty sd 0.15, load γ 1.5. On the committed files the
recovery is: absolute world HR 2.17 (planted 2.0), censoring world 2.13,
null world 1.09, planted world 1.04 — **the relative world's total a0 effect is
partly self-cancelling**, because a higher a0 raises the ceiling and therefore
lowers relative load. That world exists to test the load-definition comparison,
where it behaves as planted: relative − absolute = +0.055 nats/snap, 95% CI
[0.030, 0.083], `RELATIVE_BETTER`; the absolute world gives −0.013
[−0.028, 0.0003] favouring absolute. Both directions are tested.

**(5) Test counts.** 403 passed (full suite, bdb27 venv), 0 failed, plus the
17 recovery tests and 28 mechanical tests added here.

**(6) Still open (not blockers).** `primary_blocker_group` has no source column
in any provided file (F15); the fixture supplies it. Until the full tracking run
derives blocker assignments, the §8 crossed effect cannot be fitted on real data
— which is precisely why the sample verdict is PENDING, not GO.

---

## D74 — 2026-11-01 — Leverage × ceiling task closed; assets handed to the DL task

**Decision.** The leverage × ceiling (WR/TE) line is **closed**, not paused. The
supersession itself was D68 (2026-10-30); this records the *closeout*: a written
record of what was tested, what the FULL run found, what could not be measured,
and which assets transfer. Written to `notes/lc-closeout.md`. No further
analysis on this line; reopening requires a new question, not a new model.

**(1) Why it is being closed rather than re-analysed.** The question was causal —
does in-game max speed fall when leverage is low, and does ceiling headroom
moderate that fall. The FULL run answers it in the negative on both counts, and
the answer is *precise* rather than merely null: the within-player, within-exact-
stratum matched estimate is −0.0003 log-speed (SE 0.0026, 95% CI
[−0.0053, +0.0049]), which excludes any effect beyond about half a percent of
max speed. Searching for a further angle is therefore not an analysis problem —
the estimand is answered, and the remaining variation is in the range where
composition decides the sign (matched −0.0204 / dose non-monotone on the sample;
matched −0.0003 / dose monotone on the FULL run).

**(2) The internal contradiction is recorded, not resolved.** On the same 11,914
matched rows the unadjusted pair mean is ≈0 and the control-adjusted slope
implies −0.0108 over the observed 2.2161 SD leverage gap. A thirty-fold gap
produced entirely by the adjustment means the adjusted estimate rests on the
control set and on linearity in leverage. The diagnostic that would have
explained it — the distribution of within-pair leverage gaps — is named in the
closeout as not run, with the reason: it changes the explanation, not the
conclusion.

**(3) What transfers to the DL task.** (a) the shrunken Combine ceiling curve
(vmax, tau, a0), which is the P1 exposure; (b) the ceiling-bound exceedance check
(44,597 efforts, 0.0001 exceedance, max predicted fraction 0.9735) as a methods
statement that the ceiling never predicts an impossible speed; (c) the CEM
within-player matched-design machinery as the pattern for any like-for-like
contrast; (d) the reliability-gated slope screen (0.211 vs 0.50 → withheld) as
the worked example of why per-player slopes are not reported without a gate.

**(4) What is explicitly NOT claimed anywhere.** That low leverage causes slower
running; that headroom moderates effort; that any individual player is more or
less leverage-sensitive; that the dose gradient (−0.0075 high vs low,
p 0.0131 unadjusted) is physiological. The only surviving statement is
descriptive and hedged: adjusted comparisons show a 0.4–0.8% per-bin decline that
the matched design does not reproduce, with no measurable player-level
heterogeneity.

**(5) Filing.** `notes/lc-closeout.md` (new) is the citable record; the archived
task spec, prereg and deviations remain under `notes/archive-tasks/`. The Pi's
`outputs/game/lc/` artefacts stay sample-stamped and are not results; the FULL
run's own files remain on the operator's machine.

## D75 — 2026-11-02 — Rusher/blocker derivation lands (§8 controls 3–6)

**(1) What was built.** `src/dl/dl_blockers.py` — a pure, vectorised geometric
derivation of `n_rushers`, `blitz`, `n_blockers`, `primary_blocker_group` and
`primary_blocker_group_source`; `_blocker_columns` in `dl_snaps.py` (all-22
coverage guard applied in SQL, per-game chunks, roster-first labels); the
`crosscheck` block and `outputs/dl/game/snaps/derivation_crosscheck.csv` written
by stage 03; `dl_models_core.derivation_gate` plus an optional `derivation=`
argument on `gate_evaluate`; and the design/config contract in §5.4a. Commits
`f33e01b` (design), `25d7d90` (module), `8d4ea8f` (per-rusher label + unit
tests), `ab4589b` (stage-03 wiring), `6c38366` (gate), `54f0c76` (cross-check
population), `e30a06e` (the two planted stage-level worlds).

**(2) Why it is derived, not read.** Evidence F9 stands unchanged:
`player_play.blitzing` is a per-player rush assignment (TRUE on 98.3 % of
interior rows), so team blitz has to be counted from tracking. The derivation
never consults `blitzing`; that column remains only the primary-snap filter.

**(3) The sample cannot exercise it, and says so.** Of 14,677 plays in the snap
table, **0** carry ≥ `all22_min_players` (20) distinct tracked players at the
snap frame (max 4, median 1 — F14). All four columns are therefore 100 % NA with
the reason `insufficient_tracking` and the statement in
`snaps_summary.json::blockers.coverage_reason`. `snaps_summary.json` counts
`n_plays_with_all22 = 0`, `n_snaps_with_n_rushers = 0`, `n_snaps_blockers_na =
17,159`. The stage-04 gate reports `PENDING_FULL_RUN` with all four derivation
items pending and nothing failing — never a sample PASS. This is the whole point
of the cycle: the columns are now wired, and the sample honestly reports that it
cannot populate them.

**(4) Planted-truth recovery (exact, no tolerance).** The 21 unit tests in
`tests/test_dl_blockers.py` and the two stage-level worlds in
`tests/test_dl_snaps_blockers.py` build all-22 plays whose truth is known by
construction. Recovered exactly: 4-man rush / 5 protectors / no blitz; 6-man
pressure (blitz, five linemen + the tight end = 6 protectors, the tight end's
rusher labelled `TE`); a tight-end-stays-in world with a genuine double team
(the guard is nearer in every window frame, so the interior label wins and the
other rushers keep their own labels); an ambiguous-engagement world that returns
`NA` + `ambiguous_engagement` while the play still carries its counts; and a
12-player truncated world that returns `NA` + `insufficient_tracking`. Zero
rushers is `zero_rushers_implausible`, never 0.

**(5) Independent cross-check against `player_play` (sample, not a finding).**
The alignment split is identical on both sides — snap table 6,226 edge / 10,904
interior / 29 unmapped, `player_play` restricted to the same cohort 6,226 /
10,904 / 29 — so stage 03 neither loses nor reclassifies rows.
`unblocked_pressure` is rare where it should be: 1.32 % of edge rows vs **0.16 %**
of interior rows (DE 1.02 %, DT 0.17 %, NT 0.00 %). The blitz-vs-non-blitz
pressure contrast is `NOT_RUN` with a reason, because the derived blitz is NA in
sample mode. **Defect found and fixed during the cycle:** the first version of
the cross-check compared the table against *every* `EDGE`-labelled defender
(12,899 / 11,365, n = 24,264) instead of the DL cohort — a manufactured
"disagreement". `lined_up_position` is a formation label, so linebackers carry
it too; the check now joins `players.csv` exactly as stage 03 does. The
disagreement was an artefact of the comparison, not of the derivation, and it is
recorded here because the mis-specified version would have looked like a
finding.

**(6) Judgement calls inside the build (all declared, none tuned on a result).**
* `primary_blocker_group` is **per rusher per snap** (its own primary blocker's
  label). The first cut collapsed it to one plurality label per play, which
  would have flattened the §8 crossed effect to a constant within a snap; fixed
  in `8d4ea8f` and pinned by a test that asserts the four rushers' labels differ.
* `gate_evaluate` gains an **optional** `derivation=` argument. With it unset the
  returned dict is byte-identical to D73's, so the existing gate tests still
  assert the same verdicts. A FAIL reaches `NO-GO` through the existing
  precedence rules; no second verifier was written.
* One extra reason code, `missing_play_geometry` (a scored play absent from the
  geometry table). `line_y` falls back to the median snap `y` of all tracked
  players when nobody is inside the LOS band, because a NaN there would silently
  kill the QB rule and every geometry label.
* The QB is excluded from the protector count and from engagement (a central
  player ≥ 3 yd behind the line). An under-centre QB ~1.5 yd deep is not caught;
  `qb_rule_misses` counts the plays where no QB was identified at all.
* `n_blockers` counts protectors that **engaged** a rusher, so a lineman with
  nobody within `blocker_radius_yds` in the window is not counted; the gate's
  5–7 band tolerates the small shortfall, and the full run will report it.
* A robustness fix rode along (`e30a06e`): the pressure/censor columns are now
  `TRY_CAST` to DOUBLE in the `base` view. A table where every
  `time_to_pressure` is null was being sniffed as VARCHAR, so the event
  comparison could not even bind. A null still stays a null.
* The planted-world generator was delivered as stage-level test worlds rather
  than a committed CSV fixture directory: the model lane failed four times on
  that milestone and the worlds are cheaper and more readable as code. The
  worlds are still all-22, 12-column, and driven through `build_snaps`.

**(7) Tests.** Suite collected 404 tests at D74 and **455** after this cycle
(+51: 21 derivation unit tests, 17 stage wiring/cross-check tests, 7 gate tests,
2 planted worlds and the rest schema/edge cases). No pre-existing assertion was
removed, loosened or skipped.

**(8) Open for Conor.** (a) If the full run's coverage guard still finds 0 plays
with all-22 tracking, the gate is a genuine `NO-GO` and the §8 controls 3–6 are
not implementable from the provided tracking — that is a §8 decision, not a
licence to amend the prereg. (b) If most blockers on full data are outside the
510-prospect cohort, their labels come from geometry rather than roster
position; `label_source_counts` reports the mix on every run, and a
geometry-majority result weakens (but does not invalidate) the crossed effect.

---

## D76 — 2026-11-02 — Derivation follow-up: empty frames, expectedness, non-rushers, motion misfire

**Decision.** Tightened the all-22 rusher/blocker derivation and its gate before
the full-run review.  Five changes, all mechanical; no constant was tuned and no
existing assertion was weakened.

**(1) F1 — empty players frame.** `derive_blockers` now returns early when the
players frame has zero rows.  Previously the emit step merged an empty `res`
frame that had no `_pk` column and raised `KeyError: '_pk'`.  The early return
has the declared leading columns (`game_id`, `play_id`, `nfl_id`, other players
columns, the five `BLOCKER_COLUMNS`, `derivation_expected`, `status`, `reason`),
`status`/`reason` as object, `derivation_expected` a real `bool`, and a summary
with the same keys as a scored run plus `coverage_reason` ("no rows: the players
frame is empty…") and `empty_frame: true`.  A caller can merge without branching.

**(2) F2 — expectedness, non-rushers, gate denominator.**  Every derivable play
now gives every player a row status.  Rushers are `OK` (or `NA` with their own
engagement reason); every other player is `non_rusher` with an explicit reason
from `{not_defensive_player, not_a_pass_rush_snap, wide_stunt, containing,
held_at_line}` and no label.  `derivation_expected` is `True` exactly on rushers
of derivable plays (including a rusher whose own derivation failed) and `False`
elsewhere.  The gate's coverage denominator is those expected rows —
`value` = worst per-column NA share over them, `n` = expected count — with a
non-gating `coverage` block reporting the raw all-row share.  Three vacuity
rules: a missing `derivation_expected` column, zero expected rows, and a
zero-row frame all FAIL rather than passing silently.

**(3) Motion misfire and its residual.**  The old band rule classified by
motion, so a bull-rushed tackle (snap depth −0.5, advance +2.3) became a rusher
and a standing-up end (snap depth +0.9, lateral ≥ 2.0) became offensive.  Side
is now `roster → pre-snap depth sign → motion`, with motion consulted only at
exactly `depth == 0`.  Where motion alone would make a player a rusher, the
play is `NA` with the new reason `side_inference_ambiguous`; its row count is a
declared gate check (`derivation_side_conflicts ≤ max_side_inference_rows = 0`,
added to `config.yaml`).

**(4) Stage 03 I/O.**  `_blocker_columns` used to re-read the tracking CSV even
though `_tracking_columns` had already built the TEMP TABLE `track` on the same
DuckDB connection.  It now reuses `track` when present (detected via
`information_schema.tables`) and keeps the CSV read as the direct-call fallback;
both paths were exercised (the stage tests use the table path, the derivation
unit tests the direct call).  The snap table gains
`derivation_expected`/`derivation_status`/`derivation_reason`; plays filtered by
the SQL coverage guard are labelled `not_derived` with the short coverage code
(`insufficient_tracking` / `no_tracking_input` / `tracking_input_missing`), and
`derivation_expected` is filled `False` and cast to real `bool`, never NaN.
Summary counts `n_snaps_derivation_expected`, `n_snaps_derivation_non_rusher`,
`n_snaps_expected_na` and `n_snaps_side_inference_ambiguous` were added;
`n_snaps_blockers_na` keeps its raw five-column meaning.

**(5) No assertion was weakened; replacements where semantics changed.**
* `status == "OK"` for every row of the standard world → `OK` on rusher rows,
  `non_rusher` on the rest (a stricter, per-row assertion).
* `set(status) <= {OK, NA}` → `<= {OK, NA, non_rusher}`, plus new invariants
  (expectedness, reason sets, non-rushers carry no label).
* Stage `unclassified_player` world moved from `(44.5 → 45.0)` to
  `(45.0 → 45.4)` and off the roster: under the new precedence only exactly-on-
  the-line, no-roster players can be motion-undecidable.
* Gate `GATE_KEYS` gains `derivation_side_conflicts`; `_gate_frame` gains
  `derivation_expected=True` (and `derivation_reason`) so the existing numeric
  assertions keep their values.
* New tests: empty frame, bull-rushed tackle (with and without roster),
  standing-up end (`containing`, plus the `held_at_line` boundary at lateral
  2.5), motion-only residual, mixed expected/non-expected gate worlds (0 NA and
  1/40), vacuity, missing column, side-conflict rows, and the stage-level mixed
  world.

**Evidence.** `src/dl/dl_blockers.py`, `src/dl/dl_snaps.py`,
`src/dl/dl_models_core.py`, `src/dl/stage_03_snaps.py`,
`src/dl/stage_04_models.py`, `config.yaml`; `notes/dl-design.md` §5.4a;
`tests/test_dl_blockers.py`, `tests/test_dl_derivation_gate.py`,
`tests/test_dl_snaps_blockers.py`.

---

## D77 — 2026-11-02 — DL runnable notebook lands; 05 deferred; item-j type fix

**Decision.** Delivered `notebooks/bdb27_dl_full_run.ipynb` as the only interface for the current DL
task. It runs `00_thresholds → 01_audit → 02_combine_ceiling → 03_game_snaps → 04_models`, each
stage through its own `main()` in-process via `stage_runner.run_stage` (bytecode purged and the
stage plus its helpers reloaded from disk on every call). One config cell, Kaggle-aware data
auto-detection, a scratch `bdb27_run/dl_config.yaml`, and per-stage wall time / peak RAM read from
`outputs/run_log.csv`.

**Sample vs full.** Sample artefacts stay `PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT`; the
headline cell prints **WITHHELD** and no sample number. A full-mode run with the three
`game_tracking_{2023,24,25}.csv` files absent skips `01_audit`, `03_game_snaps` and `04_models` as
`PENDING FULL RUN` and names the missing files — it does **not** execute those stages on the
sample. That guard is deliberate: `03_game_snaps` will otherwise build a `FULL`-stamped table from
the 17,159 sample-sized `player_play` snaps, which is exactly the failure the notebook must prevent.

**Phase 5 deferred, not stubbed.** `src/dl/stage_05_validate.py` is not built. It is absent from
`DL_STAGE_ORDER`; the headline prints Phase 5 as `NOT RUN`; `notes/blockers.md` records the exact
prereg clauses it must implement (§5 censoring, §6 strata/coverage, §7 load definitions and order
permutation, §8 controls/placebos, §9 minimum snaps, §11 metric/folds, §12 SOI/equivalence, §13
reliability) and the design §5.6 artefact contract.

**Bug fixed on the way.** `dl_audit_power.item_j` fed `read_csv_auto` columns that are entirely
`NA` in sample mode; DuckDB inferred `VARCHAR` and `corr()` raised a binder error. The query now
`TRY_CAST`s both load columns to `DOUBLE`. Regression test:
`tests/test_dl_audit_item_j.py`.

**Verification.** Clean-kernel, cell-by-cell execution of the notebook in `--mode sample` with
`CLEAN=True` / `CLEAN_CONFIRM="DELETE"`: all five stages ran, 0 exceptions, headline `WITHHELD`,
figures skipped. Full-mode execution reported the three missing tracking files as
`PENDING FULL RUN` and printed `NOT RUN` for every absent result. `pytest tests`: **478 passed**.

---

## D78 — 2026-11-02 — §8 amended for a prospect-only tracking product (blitz / n_blockers / blocker group removed)

**(1) The deviation.** `notes/prereg.md` §8 listed three controls that were to be
derived from all-22 tracking: `blitz`, `n_blockers` and `primary_blocker_group`
(plus the `primary_blocker_group` crossed random effect). They are removed from
§8 and from the model. Team fixed effects and season fixed effects are added;
`quarter`, `seconds_since_last_snap` and `snaps_in_drive` are added to the
primary control list; `team_coverage_man_zone`, `offense_formation` and
`pre_snap_depth_yd` are declared as a **sensitivity panel**, never primary. This
is a pre-results amendment: it was made **before any full-data result was seen**
and before any full-data tracking file existed on this host. No number in §8
depends on an outcome.

**(2) The evidence.** The 2027 game tracking is a prospect-only product.

* Full run of the derivation (prior cycle, D75/D76): **0 of 14,677 plays** in the
  snap table carried `all22_min_players` (20) distinct tracked players at the
  snap frame; 0 derivable plays; all four derived columns `NA`; both Part 1 and
  Part 2 returned `NOT_ESTIMABLE` naming `blitz` / `n_blockers` as the empty
  fields.
* Tracking density: **1.5 players per play on full data**, **1.16 on the
  provided sample** (median 1, maximum 4 tracked at the snap frame).
* `player_play` itself carries **2.73 rows per play** (median 2): it is the
  prospect cohort, not a participation table (D69/F9).

You cannot count four rushers or five protectors when one or two of them are
tracked. The columns were not "missing on the sample and present on full": they
are **unobservable on the product**.

**(3) What was removed, and why no proxy.** `blitz`, `n_blockers`,
`primary_blocker_group`. `player_play.blitzing` cannot stand in for team blitz —
it is a per-player rush assignment (TRUE on 98.3 % of interior rows, and
equivalent to `player_get_off IS NOT NULL` on 15,633/15,635 DL rows). Using it
would silently redefine the control as "this player rushed", which it already is
in the primary-snap filter. `n_blockers` and `primary_blocker_group` have no
substitute in the provided files. No proxy was substituted for any of the three.

**(4) What replaced them.** Team fixed effects (`player_play.team_abbr`) and
season fixed effects (`games.season`, from the `game_id` join to `games.csv`)
absorb *average* team rush/protection tendency and era; they do **not** absorb
play-level variation (blitz versus four-man rush on this snap), and §8 says so.
`quarter`, `seconds_since_last_snap` and `snaps_in_drive` are added as
play-context and rest controls that §7 already used for the load definitions.
Height, weight and years since Combine are **retained** from the frozen text
because §1 fixes the estimand as "beyond position, size and play context" and
`TASK.md` Phase 4 lists them as fixed effects. `pre_snap_depth_yd` moves to the
sensitivity panel: it is observable only where the prospect's own tracking row
exists (2.3 % of the provided sample), so as a complete-case primary control it
would silently restrict the estimand to tracked plays.

**(5) Code semantics.** `src/dl/dl_snaps.py` short-circuits the derivation when
`tracking_product: prospect_only` and a deterministic probe of the lowest
`all22_probe_games` games confirms no snap frame reaches `all22_min_players`;
it records one `NOT_APPLICABLE` (`not_applicable_prospect_only`) line instead of
scanning 14,677 plays (the previous full pass spent 261 s to derive nothing). If
the probe ever finds a 20-player snap frame, the data wins and the derivation
runs. `src/dl/dl_models_core.derivation_gate` reports the three count checks,
the coverage check and the motion-residual check as `NOT_APPLICABLE` with the
dataset reason, and `NOT_APPLICABLE` never blocks a gate. A required §8 control
that is absent still FAILs `missing_required_field` — pinned by a regression
test in each direction. `src/dl/dl_blockers.py` is untouched apart from a
`PARKED` note: kept for a future all-22 product, not deleted, not called.

**(6) Retained measured controls, flagged for Conor.** The amended §8 list names
the play-context controls; height, weight and years since Combine are retained
because dropping size would change the estimand that §1 fixes and that
`TASK.md` Phase 4 specifies. If the intent was to drop them, that is a one-line
`config.yaml` change plus a §8 edit — but it should not be done silently,
because `a0` (from the 40-yard dash) is correlated with mass.

**(7) Effect on the models (sample, UNVALIDATED).** With the amended list, P1
and P2 now estimate on the provided sample instead of returning
`NOT_ESTIMABLE`. Sample numbers are machinery checks and are not findings; see
`outputs/dl/models/SUMMARY.md`.

**(8) Evidence files.** `notes/prereg.md` §8; `config.yaml`
(`bdb27_dl.game.tracking_product`, `models.controls`,
`models.crossed_fixed_effects`, `models.sensitivity_controls`); `src/dl/dl_snaps.py`;
`src/dl/dl_models_core.py`; `src/dl/stage_03_snaps.py`; `src/dl/stage_04_models.py`;
`tests/test_dl_derivation_gate.py`; `tests/test_dl_snaps_blockers.py`;
`fixtures/synthetic/dl/*` (regenerated with the `team` column and 11 snaps per
game so every fixture rusher stays above the §9 floor after the
`seconds_since_last_snap` complete-case exclusion).

**(9) §8 coverage in the sample snap table (17,159 rows; UNVALIDATED sample).**
Non-null counts, read from `outputs/dl/game/snaps/snaps.csv` after the rebuild:
`position_group` 17,159 · `alignment` 17,130 (29 unmapped formation labels,
counted) · `down` 17,159 · `yards_to_go` 17,159 · `quarter` 17,159 ·
`score_margin` 17,159 · `seconds_since_last_snap` 15,701 (1,458 first-of-game
snaps, structurally undefined) · `snaps_in_drive` 17,127 · `team` 17,159
(32 levels) · `season` 17,159 (2023/2024/2025, from the `games.csv` join).
`height_in`, `weight_lb` and `years_since_combine` are not snap columns: they
are joined in `build_frame` from `combine_results` / `players` and are present
on 17,159/17,159 model-frame rows. Sensitivity panel: `team_coverage_man_zone`
16,732, `offense_formation` 17,155, `pre_snap_depth_yd` 399 (tracking-limited).
`blitz`, `n_blockers`, `primary_blocker_group`: 0 non-null, `NOT_APPLICABLE`
(D78 §1–§3), not a coverage gap.

## D79 — 2026-11-04 — Phase 5 lands as the ceiling permutation, with the other checks named as stubs

**(1) What was built.** `src/dl/stage_05_validate.py` implements the Phase-5
**ceiling permutation** and is wired into `dl.run.STAGES` after `04_models` and
into the notebook's `DL_STAGE_ORDER`. Per replicate it moves each player's whole
shrunken ceiling (a0, a0_se, a0_icc, vmax, tau, vmax_se, tau_se) to a donor
player **within the same position group**, rebuilds the relative-load panel with
the stage-04 arithmetic, refits the Part-1 model with the same estimator, rung
ladder and in-fold standardisation, and refits the S1 population model on the
rebuilt relative load. The observed statistic is computed by the same code
path, so observed and null are commensurable. Reported per statistic: observed,
null mean/SD/2.5–97.5 quantiles, two-sided and one-sided permutation p, valid
replicate count and seed.

**(2) Rep count.** `validate.permutations.reps = 199` (p resolution 0.005),
`sample_reps = 9`. The design sketch's 1000 was a placeholder; no prereg value
fixes the rep count, and a refit costs seconds, so 199 is the configured design
and sample mode stays a machinery check. Permutation refits use one optimiser
start (the `models.cv_starts` convention) and skip the unused secondary block —
a new, default-preserving keyword on `fit_p1` (`include_secondary`, `starts`)
and on `fit_s1` (`include_reliability`). Stage-04 output is unchanged: the
defaults reproduce it exactly.

**(3) Position group — the one genuinely underspecified detail.** The prereg
fixes *that* the shuffle is within position group and names interior vs edge,
but not how the group is derived on a prospect-only product. Chosen reading
(most conservative, config-driven): a player's group is his **modal
`alignment`** over his own snaps — the labelled edge/interior field — with ties
broken alphabetically; players with no alignment fall back to the roster
`position_group` map (`DE→edge`, `DT/NT→interior`) and each player records the
source. Players in neither are `unknown` and are never mixed into a real group.
This is recorded as an open question for Conor, not as a result. On the sample,
alignment splits players 28 single-alignment / 59 two-alignment, so the modal
rule decides 59 players by frequency; a full run should report the split.

**(4) Relative-load column.** Taken unchanged from stage 04's plurality of
held-out fold selections (`part2_result.json`); if absent, the middle grid
value is used and the source is stamped `grid_midpoint_default`. The validation
stage never tunes a value.

**(5) Stubs, never passes.** `within_player_order_permutation`,
`cv_vs_controls_only_baseline` and `measurement_error_correction` raise
`CheckNotImplemented` with a message naming the check. The stage calls them,
catches the refusal, and records `NOT_IMPLEMENTED` in `validation_result.json`,
`check_status.csv` and `SUMMARY.md`. The remaining §5.6 checks (placebos,
rotation/survivorship, context sensitivity, mechanical coupling, equivalence)
also get `NOT_IMPLEMENTED` rows in their own artefact files and a reason — so an
unbuilt check can never read as a pass. No status in the artefact is `PASS`
except through an estimated statistic.

**(6) Evidence.** `tests/test_dl_validate.py` (shuffle integrity within group,
singleton groups, planted recovery, null calibration, determinism, artefact
contract, refusal to overwrite a FULL artefact);
`notebooks/bdb27_dl_full_run.ipynb` cells 0/5/6/8; `config.yaml` `validate:`;
`notes/blockers.md` (build item closed).

## D80 — 2026-11-04 — Phase 3 IDR columns: rest, power load, per-snap deficit

Binding spec: `TASK.md` (IDR, replaced 2026-10-30 ceiling spec). This entry
records the four decisions Conor fixed on 2026-11-04 and the exact rules the
code implements (`src/dl/dl_phase3.py`, wired into `src/dl/stage_03_snaps.py`
as **additive** columns; no existing column changes meaning).

**(1) Rest is a play-team quantity, not a player quantity.**
`inter_snap_rest_s = t_snap(current play) − max(time) of the team's previous
defensive play`, computed once per `(game_id, team, play_id)` and joined to
every rusher on the play. "End of play" is the **last recorded timestamp** of
that play (all-22 tracking), not the rusher's last frame: using the rusher's
own frame would make rest depend on how long he personally kept moving. The
sequence is built from **every** defensive play in `player_play`, not only the
pass-rush snaps in the table — otherwise a run between two rushes would be
skipped and rest overstated. First own snap of a player-game is `NA` and
counted (`n_first_snap_na`). Rest is `NA` when the previous play has no
recorded end timestamp (`n_prev_play_without_end_timestamp`). It is never
filled, averaged or carried forward. A gap in the **player's** own sequence
(rotation, or a row missing from the table) does not change the measured play
interval; it is recorded in `rest_gap_plays` and the per-player-game flag
`rest_sequence_complete` (false when any own snap is more than
`phase3.rest.max_gap_plays` team plays after the previous own snap). Downstream
may restrict to complete sequences as a declared sensitivity; the primary
table keeps the rows and the flag.

**(2) Load is mechanical POWER, not ∫a²dt.**
`rep_kinematic_load_w_per_kg = ∫ max(0, a·v) dt` over the rep's post-snap
trace. With `a` in m/s² and `v` in m/s the integrand is power per unit mass,
so the unit is **W/kg (m²/s³)**. Tracking is in yards: the yard-space integral
is multiplied by `0.9144² = 0.83612736` (`phase3.load.yard_to_metre`).
`rolling_kinematic_load_w_per_kg` is the sum over the prior `n` included snaps
(`phase3.load.window_snaps = 3`). Negative power (deceleration) is clipped to
zero so a braking segment cannot subtract from a rep's load; the flag is
`phase3.load.clip_negative_power`. Segments longer than
`phase3.load.max_dt_s = 0.5 s` are tracking gaps and are not integrated across.
Penalty-nullified plays and plays with no snap/timing are **excluded from the
accumulation and counted** (`n_excluded_penalty_nullified`,
`n_excluded_no_play_timing`); the row stays in the table and simply adds
nothing. A window containing an excluded snap is refused rather than silently
shortened, and counted (`n_window_contains_excluded_snap`).

**(3) The per-snap acceleration is FITTED, not twice-differentiated.**
`v(t) = vmax·(1 − exp(−t/tau))` is fitted per in-game rep on the post-snap
speed trace, restricted to the acceleration phase (`fit_window_s = 1.5 s`): a
pass-rush rep plateaus or declines once engaged, so the whole play is outside
the model's domain. `a0_snap_yds2 = vmax / tau`; `tau` is kept as a by-product
(`tau_snap_s`). Fits are bounded (`vmax` 1–15 yd/s, `tau` 0.05–5 s), require
≥8 frames and ≥0.5 s, and are refused below `min_r2 = 0.80`. Status is carried
per row: `fit_ok`, `fit_at_bound` (a parameter pinned at a bound — the short
window can make `vmax` and `tau` only weakly identified even though `a0` is
well determined), `rep_too_short`, and the failure count in the summary. The
raw double-difference `a0_snap_raw_yds2` = max `ds/dt` over the first 0.5 s is
kept as a **robustness column only**, clearly labelled; it is never the
primary.

**(4) Deficit = `a0_snap / baseline_a0`.**
The denominator is the player's **shrunken Combine `a0`** from the Phase-2
ceiling table (`outputs/dl/ceiling/player_ceiling.csv`, `feature = a0`,
`estimate`). `deficit_ratio` is a fraction: **below 1 is a deficit**.
`baseline_a0_se` is carried on every row so a downstream model can propagate
the baseline's uncertainty (measurement-error attenuation), and
`baseline_a0_yds2` is carried so the ratio can be rebuilt.

**(5) Coverage is reported, never assumed.** Tracking coverage of the rusher's
own snaps is PRESENT on full data per Conor but UNVERIFIED on this host (the
full tracking files are absent). Every row therefore carries
`rows_rusher_tracked`, `frames_used`, `fit_ok`, `fit_at_bound`,
`rep_too_short`; the summary block (`snaps_summary.json → phase3`) carries the
totals and shares, including `share_rows_rusher_tracked`,
`share_fit_ok_of_tracked` and `n_rows_without_play_timing`. A row that fails
is counted, never dropped without a count.

**(6) Evidence.** `tests/test_dl_phase3.py`: units (a·v), gap-skipping,
envelope fit, rest rules, penalty exclusion, and the synthetic recovery —
a planted rest×load interaction on `log(deficit)` (`b_int = +0.040`) is
recovered to within ±0.02 through the production path
(`src/dl/dl_phase3_fixture.py`), and a null world returns |coef| < 0.02.
No sample number is a finding; every sample artefact stays
`UNVALIDATED SAMPLE OUTPUT`.

---

## D81 — 2026-11-04 — Phase 4 Part 1 (IDR): load × rest → per-snap deficit

Binding spec: `TASK.md` (IDR, replaced 2026-10-30). This entry records how the
confirmatory Part 1 — *"mixed-effects model predicting current snap
acceleration based on prior load and rest duration"* — is operationalised. The
new code is `src/dl/dl_idr_models.py`, called from
`src/dl/stage_04_models.py`; the old a0 → pressure Part 1 is **superseded**
(kept in `part1_result.json` as provenance, marked SUPERSEDED in SUMMARY.md,
never quoted as a result).

**(1) The estimand is the interaction, on `log(deficit_ratio)`.**
Outcome `log(a0_snap / baseline_a0)`, so a *larger* deficit is a *more
negative* outcome. Fixed effects: standardised rolling kinematic load,
standardised inter-snap rest, their product, plus the spec's controls. Both
predictors are z-scored on the estimation sample, so the interaction is "per
SD of load × per SD of rest"; the raw-scale coefficient is reported alongside
(`coef_std / (sd_load × sd_rest)`, exact because the transform is linear).
Because the interaction is a product of z-scores, the sign of a
deficit-worsening interaction is **positive** when the load main effect is
negative; every artefact therefore also carries the implied contrast (slope of
log-deficit on load at −1 SD rest vs +1 SD rest) so the direction is read from
a quantity with an unambiguous meaning, not from the bare coefficient.

**(2) The load window is the Phase-3 window; 1 and 5 are a declared panel.**
`rolling_load(frame, w)` reimplements the D80 rule (sum over the prior `w`
*included* own snaps; an excluded snap in the window or a short window yields
NA, never a silently shortened sum) with `w` as a parameter. Window 3 is
primary because it is Phase 3's declared load window; 1 and 5 are fitted by the
same code path and reported as a sensitivity panel. The window is never chosen
by looking at a result. A test asserts the window-3 rebuild equals the
`rolling_kinematic_load_w_per_kg` column exactly where both exist.

**(3) Controls: declared, and reported when unavailable.**
`alignment`, `down`, `yards_to_go`, `score_margin`, `blitz`. `blitz` is
structurally absent on the prospect-only tracking product (D78); the model
records it as UNAVAILABLE/ABSENT with a reason instead of dropping it from the
specification silently. Any declared control below
`idr_part1.min_control_coverage` non-null is reported the same way. Continuous
controls are standardised on the estimation sample; categorical levels with
fewer than `min_level_n` rows are not given a dummy (reported by omission from
the term table, which lists what was fitted).

**(4) Convergence ladder, in the declared order.**
`correlated_random_slopes` (1 + z_load | rusher, lbfgs, with bfgs/powell
retries **inside** the rung — an optimiser retry is not a change of
specification) → `uncorrelated_re_alt_optimiser` (random intercept + a separate
uncorrelated load variance component, powell first) → `random_intercept` →
`fixed_effects_only` (population OLS, player-clustered). A rung is usable only
if it converged **and** every variance component is strictly above
`variance_component_floor`. The interaction is present in every rung; if no
rung estimates it the result is NOT_ESTIMABLE with the reason, never a reduced
model.

**(5) Inference respects repeated snaps.**
The standard error is a CR1 player-clustered sandwich on the fixed effects
(same correction as `lc_models._cluster_cov`), with t and `G−1` dof. A
game-clustered SE is added when the number of games reaches
`min_clusters_game` (10); otherwise it is reported NOT_ESTIMABLE rather than
quoted. The naive i.i.d. SE is retained as a diagnostic only
(`se_iid`, `design_effect`).

**(6) Synthetic recovery (deterministic, no network).**
`src/dl/dl_idr_fixture.py` builds three balanced worlds in the snap-level
schema; the regressor is generated **from the windowed load**, not the current
rep. `planted` (`b_int = +0.05`, `b_load = −0.08`): recovered 0.0504,
95% CI [0.0490, 0.0518], n = 888, 24 rushers, rung
`correlated_random_slopes`. `null`: coefficient 0.0006, CI [−0.0008, 0.0020],
covers zero. `random_slopes` (population interaction exactly zero, per-rusher
slope sd 0.14): clustered estimate 0.0135 with CI [−0.055, 0.082] covering
zero while the i.i.d. interval (`p = 0.0035`) would have announced a
non-existent interaction — design effect 7.15. Plus an end-to-end test that
runs the Phase-3 tracking fixture through `add_phase3_columns` into the model
and recovers the Phase-3 plant (`b_int = 0.040`) within ±0.02.

**(7) Sample diagnostic, explicitly a non-finding.**
On the provided sample only 7 snaps have outcome + window-3 load + rest
(74 at window 1, 0 at window 5), so the primary is NOT_ESTIMABLE at every
window. That is a coverage artefact of the ~100 MB sample and is labelled
UNVALIDATED SAMPLE OUTPUT; no sample number is a finding.

**(8) Artefacts (additive; no existing key redefined).**
`outputs/dl/models/idr/part1_idr.json`, `idr_windows.csv` (one row per window),
`idr_terms.csv` (fixed effects of the primary window, standardised and raw);
`model_result.json` gains a `part1_idr` block; SUMMARY.md gains the IDR section
and marks the old Part 1 SUPERSEDED.

## D82 — 2026-11-04 — Phase 5 (IDR): the two TASK.md items only

Binding spec: `TASK.md` (IDR). Phase 5 names **two** checks and this entry
records how they are built. New module `src/dl/dl_idr_validate.py`, called
from `src/dl/stage_05_validate.py`.

**(1) The within-player rest permutation is the binding permutation.**
`inter_snap_rest_s` is shuffled **within each `(nfl_id, game_id)` block**; the
load, the outcome and every other column stay in place. The per-block rest
multiset is preserved exactly and no value crosses a player or game boundary —
both **asserted** on every replicate (`block_multiset_preserved`,
`missingness_preserved`), so a broken permutation raises rather than producing
a null. Missing rest values stay on the snaps that had them, which keeps the
complete-case sample identical across replicates.

**(2) The observed statistic and the null use one code path.**
Both the observed interaction and every replicate refit go through
`build_idr_frame` → `fit_idr_ladder`: same ladder, same controls, same
player-clustered inference, same standardisation rule (recomputed on the
estimation sample). The statistic is the standardised load × rest interaction;
the raw-scale coefficient is reported alongside. Inference is the permutation
p `(1 + #{|null| ≥ |observed|}) / (reps + 1)`, two-sided, with the one-sided
value, the null mean/SD/quantiles, valid and failed refit counts, the seed and
the number of snaps in **singleton blocks** (blocks with < 2 finite rest
values, which cannot be permuted — counted, never dropped).

**(3) Context controls re-fit, they do not re-measure.**
The windowed load and the rest of a filtered row keep the values Phase 3
measured on the **full** sequence: an exclusion changes the estimation sample,
not the measurement. Rebuilding the window on the truncated sample would
silently redefine the load as "the prior three *remaining* snaps" and is
rejected. Garbage time uses the Phase-3 `garbage_time` column when present
(cross-checked against the config rule) and otherwise the config rule
`quarter_and_margin` with the margins in `bdb27_dl.game.garbage_time`
(q4 4 / 21 / 28). The two-minute exclusion is `quarter ∈ {2, 4}` and
`game_clock_s ≤ 120`, both from config.

**(4) No rule exists for "obvious passing situations"; the gap is reported.**
Neither `config.yaml` nor `notes/prereg.md` defines which situations are
obvious (no down/distance/score/formation rule anywhere). Per the task's
instruction the check is reported `DEFINITION_UNDEFINED` with the reason and
recorded as blocker B-DL-IDR-01; no threshold is chosen. The same applies to
any other exclusion whose rule is not in the repo.

**(5) The ceiling permutation is SUPERSEDED, not deleted.**
`validate.ceiling_permutation.enabled: false` removes it from the default run
path; its code (`ceiling_permutation`, `permute_ceilings`, the position-group
and relative-load helpers) stays in `stage_05_validate.py` for provenance, and
every artefact labels it `SUPERSEDED` with the reason that TASK.md replaced it.
The retired spec's other stubs (snap-order permutation, CV-vs-controls
baseline, measurement-error correction, placebos, rotation, mechanical
coupling, equivalence) remain listed as `NOT_IMPLEMENTED` — never as a pass.

**(6) Artefacts.** `validation_result.json` (verdict, checks, design, counts,
the `gap` block), `rest_permutation_null.csv` (one row per valid replicate),
`context_controls.csv` (baseline plus the three exclusions),
`context_sensitivity.csv` (the same rows under the legacy name),
`check_status.csv`, `permutation_null.csv` (a single SUPERSEDED row unless the
retired check is re-enabled), `SUMMARY.md`. Sample output stays stamped
`UNVALIDATED SAMPLE OUTPUT` and the verdict is `NOT_INTERPRETED_SAMPLE`.
On the provided sample the Part-1 frame carries 7 complete rows at window 3
(below `idr_part1.min_rows` 100), so both items are `NOT_ESTIMABLE` with that
reason — the expected sample outcome, not a failure.

## D83 — 2026-11-04 — Obvious-passing context control skipped by decision

**Decision (Conor, 2026-11-04, verbatim):** *"Don't care, skip"* — asked to
define the "obvious passing situations" exclusion that `TASK.md` Phase 5 names
(blocker B-DL-IDR-01), he chose **not** to run that one context control.

**What changed.**
`bdb27_dl.validate.context_controls.obvious_passing` is now
`{enabled: false, rule: null}`. `src/dl/dl_idr_validate.py::_obvious_passing`
reports the row as **`SKIPPED_BY_DECISION`** with a one-line reason naming the
decision date, instead of `DEFINITION_UNDEFINED`. The row still appears in
`validation_result.json` (its `gap` block now mirrors this status),
`context_controls.csv`, `check_status.csv` and `SUMMARY.md`, so the writeup can
say **two of the three context controls ran and the third was skipped by
decision**. Nothing is silently dropped and no rule or threshold was invented.

**Unchanged.** Garbage time and the final-two-minute exclusions stay `enabled:
true` exactly as before. No Phase-3 definition, Part 1/Part 2 model spec, rest
permutation, or threshold was touched.

**Why.** The control has no definition in the repository, and defining one after
seeing the data would be a result-moving analysis knob. Conor's call is to skip
it rather than invent one; the writeup reports the skip.

**Reversible.** Setting `enabled: true` plus a concrete `rule` restores the
control. With `enabled: true` and `rule: null` the row returns to
`DEFINITION_UNDEFINED` — that state is still tested.

**Blocker.** `notes/blockers.md` B-DL-IDR-01 is closed: **RESOLVED — skipped by
decision**.

---

## D84 — 2026-11-05 — New binding spec: burst vs. jump; Phase 0+1 only · scope

**Decision.** `TASK.md` (commit `bcfd48b`) supersedes the intra-drive-regeneration
spec. This cycle implements **Phase 0 + Phase 1 only**. Phases 2–5 exist as
modules that exit 3 with `STAGE NOT IMPLEMENTED`; they create no checkpoint, so a
later run cannot mistake a stub for finished work.

**Why.** The spec's own run order requires the layout to be real and addressable,
and Phase 1 is the gate. Implementing later phases before the gate is decided
would build on an unvalidated premise.

**Evidence.** `TASK.md` "## Phases" and "## Layout and run order`.

**Reversible.** `src/02_combine_physical_time.py` … `05_validate.py` are additive.

## D85 — 2026-11-05 — Archive the retired analyses; keep only genuine infra

**Decision.** `git mv` every retired analysis into
`archive/legacy-2026-11-05/`, preserving history:

* `src/00_thresholds.py` … `src/07_phase4.py`, `ceiling_core.py`, `lc_*.py`,
  `synthetic_lc_fixture.py`, the whole `src/dl/` package, `legacy_ceiling_v1/`,
  `legacy_db_decay/`;
* all 38 legacy test modules and `tests/conftest.py`;
* `notebooks/bdb27_dl_full_run.ipynb`, `notebooks/bdb27_full_run.ipynb`;
* `notes/{dl-design,dl-evidence,dose_stage_spec,phase-0.5-external-proxies,
  lc-closeout,external_data_manifest,prereg}.md`;
* the old `blockers/limitations/assumptions/scale/pending_full_run` notes;
* the 5 retired figures under `outputs/figures/`;
* `config.yaml` → `archive/legacy-2026-11-05/config.legacy.yaml`.

**Kept (reusable, rewritten into a clean namespace).** The infrastructure ideas
in `dl_common.py` — DuckDB connect with a memory limit and redirected temp dir,
explicit-column CSV→Parquet, provenance writers that stamp every artefact,
`timed_stage` with peak-RSS sampling and `run_log.csv`, config-fingerprint
checkpoints, disk guard, and the prereg/provenance gates — were re-implemented
self-contained in `src/common.py`. Nothing else was kept.

**Not kept: `bdb27_run/`.** Untracked scratch notebooks from the retired task.
Moved out of the repo to `/mnt/project_data/bdb27_scratch_archive/` rather than
deleted, because `trash` semantics beat `rm` and it was never in git.

**Also removed from the working tree (untracked build output, retired spec):**
`outputs/{ceiling,dl,envelope,external,game,models,security,security_models,
validate,thresholds.json,run_log.csv,run_log_legacy_2026-10-26.csv}` → same
scratch archive.

**Why.** The tree was full of analyses for two superseded specs. Leaving them
would imply unimplemented stages and pollute `pytest` collection. Archiving keeps
history and the option to recover a helper.

**Evidence.** `git status` shows renames (`R`), not deletions, for every archived
file.

**Test impact.** No *passing* test was deleted without archiving: the legacy tests
moved with their modules and are not collected (`pytest.ini testpaths = tests`).
The new suite is 116 tests and exits 0.

## D86 — 2026-11-05 — One `bdb27:` config block; every number commented

**Decision.** `config.yaml` now has a single top-level `bdb27:` block. Every
numeric design choice carries an inline comment saying what it is and why. Paths,
thresholds, sizes and seeds all live there; no magic numbers in code.

**Why.** `TASK.md` Engineering: "Paths, thresholds and sizes live in config.yaml;
no magic numbers." The previous 82 KB file mixed three retired specs.

**Evidence.** `git mv config.yaml archive/legacy-2026-11-05/config.legacy.yaml`.

**Reversible.** The legacy config is intact at the archive path; archived code
would need `--config` pointed at it.

## D87 — 2026-11-05 — Onset definition: 1.0 yd/s sustained for 2 frames

**Decision.** Onset is the first frame where `s >= 1.0 yd/s` for **two
consecutive** frames, inside a search window starting at the snap (game) or the
first frame (Combine), with a 0.5 s pre-onset baseline recorded.

**Why these values.** Stationary tracking jitter sits at ~0.1–0.3 yd/s, so 1.0
yd/s is clearly above the noise floor at 10 Hz. One frame is too easy to trip on a
single bad sample; two frames (0.2 s) costs 0.1–0.2 s of detection lag but
prevents a spike from starting the clock.

**This is provisional.** `TASK.md` requires the onset definition in
`notes/prereg.md` **before the first full-data run**; the values move there
unchanged or with a logged reason. Phase 5 varies the threshold over a config
grid.

**Evidence.** `01_audit` item (h): all 177 DL 40-yard attempts produced a
detectable onset at this threshold, so it is not so high that it misses real
starts.

**Reversible.** `onset.speed_yds`, `onset.sustained_frames`.

## D88 — 2026-11-05 — Item (g) is computed but the gate stays PENDING

**Decision.** The split-half ICC, the SD decomposition and the between-player
share are **computed and written**, because `player_play.csv` is complete
(1,001/1,002 games). The gate condition is nevertheless `PENDING_FULL_RUN`,
because `TASK.md` marks item (g) "full data only" and because the split-half here
is unadjusted for pre-snap depth.

**Why not withhold the number.** Conor needs to know whether reliability is
plausible before committing to a full run. Withholding it would be
over-cautious; presenting it as a result would be dishonest. So it is labelled
`PROVISIONAL` in `audit_items.csv` with its scope (`FULL_RAW` label file, not
tracking) and never becomes a PASS.

**Evidence.** `outputs/audit/audit_items.csv` item `g`; `notes/phase1-audit.md`
§g.

## D89 — 2026-11-05 — The sample's coverage is measured, not assumed

**Decision.** `01_audit` reports the tracking sample's structure explicitly
(1,048,575 rows = 2²⁰−1; 333 games; 1–4 players per play; 1–90 plays per game;
154 players) and which code paths it cannot exercise.

**Why.** `TASK.md` Engineering: "Check how the sample was drawn and note which
code paths it cannot exercise." The sample is a **player-in-play** draw, not a
head truncation and not a random row sample — that distinction changes what can
be tested (no relative geometry, no blocker inference, 2023 only).

**Evidence.** `notes/schema.md` §3; `outputs/audit/units_measured.json`
(`tracking_contract`).

## D90 — 2026-11-05 — Missing rusher–blocker data is a blocker, not a proxy

**Decision.** No blocker assignment is inferred from tracking geometry. Phase 3's
"assigned blocker(s) and blocker count" is recorded as **unavailable**
(`notes/blockers.md` B2).

**Why.** `TASK.md` is silent on how to infer it, and the sample cannot support the
inference anyway (1–4 players per play). Per the standing rule, silence is a
question, not an opening.

**Reversible.** Full tracking plus a Conor-approved matching rule.

## D91 — 2026-11-05 — Edge/interior split uses `lined_up_position`, with the conflict reported

**Decision.** The split uses `player_play.lined_up_position` (`EDGE` /
`INTERIOR_LINE`) because it is the only in-game alignment label. The conflict with
`combine_position` (53 `EDGE` players attended as `DB`; 440 get-off reps on
`CB`/`SLOT_CB` rows) is reported in `notes/schema.md` §5a and logged as blocker
B3.

**Why.** At most one label is right for those players and the data cannot say
which. Choosing "trust the in-game label" is the least-inventive option, but it is
**still an assumption** and is flagged as needing Conor's rule.

**Not done.** No player was silently reassigned, and no conflicting row was
dropped.

## D92 — 2026-11-05 — Sample artefacts are stamped by the writer, not by convention

**Decision.** Every artefact goes through `common.write_csv/json/markdown`, which
prepends a provenance stamp keyed off `mode`. In sample mode the stamp contains
the literal `UNVALIDATED SAMPLE OUTPUT`. `--mode full` refuses to run unless (a)
provenance is `FULL`, (b) the tracking input exceeds the sample row cap, and (c)
`notes/prereg.md` declares `**Status: FROZEN.**`.

**Why.** `TASK.md`: "Label every sample output `UNVALIDATED SAMPLE OUTPUT`" and
"Before the first full-data run, write /notes/prereg.md". A convention that
depends on the author remembering is not a control.

**Evidence.** `tests/test_common_gates.py` asserts the stamp on CSV, JSON and
Markdown, and that a mislabelled full mode raises.

## D93 — 2026-11-05 — New run log; the old one had a different header

**Decision.** `outputs/run_log.csv` was moved to the scratch archive and restarted.
The legacy log had two incompatible row formats (an old header with `when` first,
new rows with `stage` first), so appending would have corrupted it.

**Why.** `TASK.md` requires wall time and peak RAM per stage; a log that cannot be
parsed provides neither.

**Evidence.** The restarted log has one header and one row after the Phase 1 run.

## D94 — 2026-11-05 — Tests: 108 new, legacy suite archived not deleted

**Decision.** A new `tests/` suite covers schema/dtype/unit failures, the tracking
contract, provenance and full-run gates, onset and crossing primitives, reliability
estimators, run order and stub behaviour, determinism (including a byte-identical
re-run of Phase 1), and the edge cases `TASK.md` names.

**Why.** `TASK.md` "Tests before handoff". The schema test also *found two real
bugs* during this cycle: `game_clock` is a `TIME` and `sack_allowed` is a
`DOUBLE` (0/0.5/1.0), not the `VARCHAR`/`BOOLEAN` the first registry assumed.

**Not done.** The synthetic-recovery test required by `TASK.md` Tests item 2 for
the **Phase 4 decomposition** (planted b, shared baseline error) is not written,
because Phase 4 does not exist. A Phase-1-level version (planted jump recovered by
the one-way share, plus a null world) is written and passing.

**Evidence.** `pytest -q` → 116 passed.

## D95 — 2026-11-05 — The gate artefact is committed; bulky outputs are not

**Decision.** `outputs/audit/*.csv` and `*.json` are committed (≈44 kB) and
exempted from the `outputs/*` ignore rule. Everything else under `outputs/` stays
gitignored.

**Why.** The Phase 1 gate decision is a deliverable, not a build product: Conor
and any later run need to see *which* conditions passed and on what evidence
without re-running a 43 s stage. Everything larger (Parquet, per-snap tables) is
regenerable and is excluded.

**Evidence.** `.gitignore` lines 47–50; `git check-ignore -v outputs/audit/gate.json`
matches the negation.

## D96 — Q1: smallest effect of interest raised to 0.25 · 2026-11-05

**Decision (Conor, 2026-11-05, Q1).** `bdb27.audit.smallest_effect_of_interest`
goes **0.15 -> 0.25**. The rationale recorded with the decision is: with 105
eligible players the sampling-error floor is ~0.271, so a 0.15 target could never
be detected; the paper therefore claims "**at least a quarter** of between-player
variance", not a target the design cannot resolve.

**Implementation.**
- `config.yaml` is the only place the value lives; no code constant.
- The realised floor is recomputed and printed, never hardcoded:
  `stats.min_detectable_share(P)` remains the single formula
  (`z*sqrt(2/P)`), and Phase 1 item (i) already reports it.
- Phase 2 reports **three** computed counts/floors because they answer different
  questions: all get-off reps (105, floor 0.271 — reproduces the audit and Q1),
  primary-DL reps (103, floor 0.273), and primary-DL **with a Combine 40-yard
  attempt** (80, floor **0.310**).

**New computed number, needs Conor's confirmation — not a decision of mine.**
The 80-player / 0.310 floor is the design that Phase 4 can actually fit: 23 of
the 103 DL players with >=50 reps have no 40-yard attempt, and Q3 removes the
CB/SLOT rows. At that count a 0.25 share is *below* the detectable floor, so the
"at least a quarter" claim is not yet supportable by the measured design. This is
reported in `outputs/phase2/phase2_summary.json` (`design`) and surfaced to
Conor; I have **not** changed the 0.25 target.

---

## D97 — Q3: `lined_up_position` is the position authority · 2026-11-05

**Decision (Conor, 2026-11-05, Q3).** Trust `player_play.lined_up_position` as
the position/alignment authority. Do **not** require the `combine_position`
vocabulary to agree, and do not invent a second reconciliation rule.

**Implementation.**
- `primary_analysis_set` is defined as: the player has at least one
  `player_get_off` row whose `lined_up_position` is `EDGE` or `INTERIOR_LINE`.
- Players whose only get-off reps are on `CB`/`SLOT_CB` rows are **excluded**
  from the primary set, not silently included. They still get a Combine physical
  time in the per-player table (`pooling_group = "other"`) so a sensitivity
  analysis can add them back explicitly.
- `outputs/phase2/position_conflicts.csv` lists every player whose two labels
  disagree, with rep counts split by `REPS_EDGE` / `REPS_INTERIOR_LINE` /
  `CB`+`SLOT_CB`, plus a `has_forty_attempt` flag.
- The reported counts are computed from the complete label file, not hardcoded:
  **53** players with EDGE reps who attended the Combine as `DB`; **9** with
  INTERIOR_LINE reps as `DB`; **25** players with CB/SLOT_CB get-off reps who
  attended as `DL`; **440** get-off reps on CB/SLOT_CB rows. All four match
  `notes/schema.md` §5a.

**Consequence for the player universe.** The audit's 196 "DL rushers with >=1
get-off" used the Combine position vocabulary. Under the Q3 rule the primary set
is 169 players (>=1 rep at EDGE/INTERIOR_LINE); the difference is the CB/SLOT
reps group plus players with no DL-labelled rep.

---

## D98 — Q4: drop the blocker term; spec amendment · 2026-11-05

**Decision (Conor, 2026-11-05, Q4).** There are no rusher-blocker assignments in
the data. Drop the blocker term from **every** model, do **not** infer
assignments from tracking geometry, and remove the blocker / blocker-count
fields from the Phase 3 field list.

**Implementation.**
- `TASK.md` Phase 3 bullet no longer asks for "assigned blocker(s) and blocker
  count"; an amendment note cites this decision.
- The Phase 4 variance-decomposition formula and the outcome model no longer
  contain `+ blocker`.
- `notes/blockers.md` B2 is **RESOLVED — NOT APPLICABLE** (it was a question
  about a field that is no longer in the design, not a missing input).
- `notes/limitations.md` records the consequence: the physical/jump split is
  **unadjusted for opponent quality**, and the design cannot distinguish a
  player effect from a matchup effect. That is a stated limitation, not a
  quantity to be estimated from geometry.
- No stage reads `pass_rushers_encountered` or `unblocked_pressure` as a blocker
  assignment.

---

## D99 — Phase 2 engineering choices (Combine physical time) · 2026-11-05

**Scope.** `src/02_combine_physical_time.py` implements `TASK.md` Phase 2 only,
one code path for `--mode sample|full`, config-driven, checkpointed, with
`--force`. The Combine tracking file is complete (463,189 rows / 84 MB) and fits
in RAM, so the 40-yard measurements are real; the *mode* still controls
provenance because the in-game side of calibration is sample-only.

**Onset.**
- Primary definition: provided `s` crosses `onset.speed_yds = 1.0` yd/s for
  `sustained_frames = 2`; the **same function** (`physical.detect_onset`) is used
  by Phase 1's in-game check, so the definition is literally shared.
- The threshold is **absolute**, not baseline-relative. The config comment said
  "relative to a pre-onset baseline"; the code has always compared the absolute
  speed, and Phase 1's published ICCs depend on that behaviour. I kept the
  behaviour and corrected the comment rather than silently changing a frozen
  measurement. The baseline is still reported per attempt as context.
- Sensitivity, not a second rule: onset from speed recomputed from `x,y`
  (central differences) and onset from cumulative displacement
  (`onset.displacement_yds = 0.10`). Median absolute difference from the primary
  onset: **0.000 s** for both; no attempt differs.
- 793/793 attempts have a detectable onset.
- The 10 Hz assumption is asserted at runtime (`assert_sampling_rate`):
  per-attempt median frame spacing must be within
  `onset.sampling_tolerance_s = 0.02` of 1/`onset.hz`; measured 0.100 s, with
  100% of 40,707 gaps inside the tolerance.

**Direction and model-free time.**
- Direction of travel is the principal axis of the post-onset coordinate path
  (`physical.travel_direction`); `dir` is never used (it wraps).
- Distance along that axis is projected from the onset frame; the crossing time
  at each `d` is linearly interpolated between the bracketing frames
  (`physical.time_at_distance_qc`). This is the estimand.
- The parametric fit `v(t) = vmax(1-exp(-t/tau))` is fitted on the first 2 s
  post-onset by a deterministic grid plus local refinement, and is used **only**
  as a cross-check. It never enters a per-player value. Median disagreement
  (parametric minus model-free) falls from **+0.077 s at 0.25 yd** to **+0.020 s
  at 10 yd**, and its p95 absolute disagreement at 40 yd is 0.29 s — i.e. the
  fit is worst exactly where the estimand lives, as `TASK.md` warned.

**Gap policy.** The "first second" clock is **motion onset** in the Combine (the
in-game counterpart will be the snap). A crossing inside a frame gap in that
first second is reported `crossed=true, ok=false` and never filled. A gap below
`onset.interpolate_max_gap_s = 0.5 s` is bridged **only** outside the first
second; a gap at or above 0.5 s is never bridged. The whole real Combine file has
zero gaps larger than 0.1 s, so the policy fires only in tests/synthetic data.

**Backward-start QC (an engineering addition, logged as such).** One tracked
segment (55999 attempt 4) moves 1.8 yd **backwards** for ~1.5 s before starting
forward; two segments exceed 0.5 yd of backward excursion. "Time from onset to
*d*" is then measuring a walk-back, not a sprint. I added
`physical.max_backward_excursion_yd = 0.5` (design choice: tracking noise is
~0.03 yd; half a yard is real motion) and flag those attempts
(`qc_exclude`, reason `backward_start`). They are **excluded from pooling, not
re-origined** at the turnaround, because re-origining would be a new onset rule
that `TASK.md` does not authorise. Two attempts are excluded; both are
non-primary. Attempt numbers and the original clock times remain in
`combine_attempts.csv`.

**Pooling.** Per distance, per pooling group (`primary_dl`, `other`):
`y_ai = theta_i + e_ai`, `theta_i ~ N(mu, tau2)`. `sigma2` is the pooled
within-player variance; `tau2` is the one-way random-effects moment estimator
(the same moment as `icc_oneway`). Posterior mean and SE are the precision-
weighted combination; **every usable attempt contributes**, there is no
"best attempt" path. Degenerate cases are reported, not floored: no repeated
attempts -> no pooling and `posterior_sd = null`; `tau2 <= 0` -> raw mean with
its unpooled SE.

**Distances.** The config grid is dense from 0.25 to 5 yd (whole in-game range)
and sparse to 40 yd for the cross-check. Per-frame curves are also written, so
Phase 3 can interpolate at each rep's exact depth rather than snapping to the
grid.

**Player universe.** All 418 Combine players with a 40-yard attempt are measured
(the kinematics do not need a position label). `primary_analysis_set` and the
pooling group come from the Q3 rule. Measuring everyone avoids a position filter
silently deleting a player before he can be reported.

**Calibration.** `outputs/calibration.json` is written with
`status = "PLACEHOLDER"` and `estimates = null` in sample mode. It cannot be
estimated from the sample (`TASK.md` forbids it), and it cannot be estimated at
full scale either with these files: no column labels stance or Combine start
type, and the global Combine-to-game offset needs Phase 3 in-game times. The
estimator is specified in the file rather than replaced by a guessed number.
`common.require_calibration_full` refuses any placeholder/non-estimable record
in full mode; there is no override.

**Fixture.** `src/synthetic.py` now writes `players.csv` and
`combine_results.csv`, uses matching Combine/game ids, and plants the Q3 label
conflict for every seventh player. `fixtures/synthetic/demo` was regenerated with
the same parameters. The existing four fixture files are byte-identical (ids have
the same width), so no other test's expectations moved.

---

## D100 — Phase 2 outputs, tests and the realised design floor · 2026-11-05

**Artefacts** (all stamped `UNVALIDATED SAMPLE OUTPUT` in sample mode):
`outputs/phase2/combine_attempts.csv` (one row per attempt: onset, flags, fit),
`combine_attempt_times.csv` (attempt x distance, model-free + parametric),
`combine_attempt_curves.csv` (per-frame elapsed/along curve for exact-depth use),
`physical_time_by_player.csv` (**deliverable**), `hierarchical_fit.csv`
(variance components per distance/group), `position_conflicts.csv` (Q3),
`phase2_summary.json`, and `outputs/calibration.json`.

**Per-player table schema.** `nfl_id, pooling_group, display_name, draft_year,
combine_position, linedup_positions, reps_dl, reps_cb_slot,
primary_analysis_set, conflict_class, distance_yd, in_game_depth_range,
n_attempts_used, posterior_mean_s, posterior_sd_s, posterior_var_s2, raw_mean_s,
raw_sd_s, shrinkage_weight, pooled, pooling_note, attempt_numbers`. One row per
player x distance; `attempt_numbers` keeps the original numbers.

**Sanity-check counts on the real Combine file.** 793 attempts / 418 players;
793 with a detectable onset; **0** speeds above 12 yd/s; **0** duplicate attempt
keys; **0** duplicate frame rows; **0** attempts with a first-second gap; maximum
frame gap 0.1 s; **2** backward-start attempts excluded from pooling. The checks
are exercised with planted failures in `tests/test_phase2.py`, so "0" here means
the file is clean, not that the check is inert.

**Tests.** 26 Phase-2 tests added: schema/dtype/unit and sampling-rate failures that must raise,
known-answer interpolation, the full gap policy (inside/outside the first second,
under/over the cap), onset determinism, backward-start detection, duplicate
detection, speed-cap flags, hierarchical shrinkage, one-attempt players, the
no-repeat-attempt fallback, the calibration full-mode gates, and a byte-identical
re-run of the stage on the fixture. Full suite: **142 passed**.

**Realised design floor.** Computed, printed, not hardcoded: 105 players ->
0.2705; 103 primary-DL -> 0.2731; 80 primary-DL with a 40-yard attempt ->
**0.3099**. The last is the number Phase 4 can actually use; it is flagged for
Conor (D96) rather than adjusted here.

**Runtime.** 9.6-10.1 s, peak RSS 273-303 MB on the Pi for the full Combine file.

---

## D101 — Q2: `quick_pressure` null -> false · 2026-11-05 (09:27)

**Decision (Conor, 2026-11-05, Q2, relayed 09:27).** `quick_pressure` remains the
secondary outcome, and a **null is treated as "no pressure"**. The outcome is
`(quick_pressure IS TRUE)` over **all non-blitz DL pass-rush snaps**. The
assumption must be stated explicitly in the prereg when it is written; it is not
stated there in this cycle because `notes/prereg.md` still does not exist.

**Evidence base (unchanged, from the complete label file).** On the 43,562 DL
rows carrying `player_get_off`: 3,378 non-null `quick_pressure`; **40,184 null
(92.2%)**; among rows with a pressure time, **2,334 explicit `false`**, 1,044
`true`, and 40 with a null flag. The decision therefore manufactures **40,184
negative labels** from nulls. That is a stated assumption, not a verified
semantic: the audit's observation that "null is not false" remains a true
description of the raw data.

**Pre-specified sensitivity, not an alternative primary.** The offence-row family
(`pressure_allowed`, `time_to_pressure_allowed`, `sack_allowed`) is recorded as a
sensitivity comparison. It does not replace the primary outcome and must not be
selected after seeing results.

**Contradiction check.** `notes/blockers.md` B1 previously said "Do not choose
(b) silently"; Conor has now chosen (b) explicitly, so the choice is not silent.
No code or note states that null *is* false as a data fact; the policy is labelled
an assumption. Phase 3/4 remain stubs, so no implemented stage changes.

**Consequence for Phase 3.** Add `quick_pressure_binary = (quick_pressure IS
TRUE)` to the per-snap table as the secondary outcome, over non-blitz DL
pass-rush snaps, and carry the null share and explicit-false count beside it. The
sensitivity columns are `pressure_allowed`, `time_to_pressure_allowed`,
`sack_allowed` from the offensive row. B1 moves from OPEN to RESOLVED BY DECISION;
the label-provenance risk moves to `notes/limitations.md` §5.

---

## D102 — Q5: prereg timing, option D (two-part freeze) · 2026-11-05 (09:27)

**Decision (Conor, 2026-11-05, Q5, relayed 09:27).** The prereg is frozen in two
parts:

1. **Confirmatory core**, next cycle and **before Phase 3 results**: estimand,
   model form, reliability threshold, smallest effect of interest (0.25), and the
   outcome hierarchy.
2. **Operational annexe**, a second frozen document **immediately after Phase 3**:
   onset threshold, distance grid, minimum snaps per player.

**Consequence for this run.** `notes/prereg.md` remains absent. The full-mode gate
is unchanged and still refuses with exit 4; Phase 2 stays sample + synthetic only.

**Contradiction to flag, not reconcile.** `TASK.md` Rules currently require the
prereg, before the first full-data run, to fix *all* of: onset definition,
distance definition, stance handling, variance-decomposition model, reliability
thresholds, outcome definitions, minimum snaps per player, and the smallest effect
of interest. Option D moves **onset threshold, distance grid and minimum snaps**
to an annexe frozen *after* Phase 3. Unless the operational annexe is also frozen
before Phase 3's first full run, the first full run will use operational constants
that are not yet frozen, which is a deviation from the current `TASK.md` wording.
I have recorded the decision as given and have **not** edited `TASK.md` to
reconcile it. The choice for Conor is either (a) freeze the operational annexe
before Phase 3's full run too, or (b) amend `TASK.md` to state that the Phase 3
run is operational/exploratory and only Phase 4 is confirmatory.

---

## D103 — Phase 3 engineering: one row per DL pass-rush snap · 2026-11-05

**Scope.** `src/03_game_snaps.py` implements `TASK.md` Phase 3, one code path for
`--mode sample|full`, config-driven, checkpointed, with `--force`. No Phase 4-5
work was done.

**Result on the complete label file (real counts, not sample):** 44,002 rows
carry a `player_get_off`; **33,061** are in the primary set (EDGE 18,658 /
INTERIOR 14,403); 10,501 are removed as `RUN_PLAY` and 440 as
`NON_DL_ALIGNMENT`. 158 rushers have ≥1 primary snap, 99 clear the 50-snap
minimum, 79 clear 100; median 101 snaps and 12 games per rusher.

**Definitions (each is a choice that must be in the prereg annexe).**

* **Pass play** = `pass_result` in `{C, I, S, R, IN}`. `""` is a run; `R` is a
  QB scramble on a pass play and is kept. `pass_result` is one value per play
  (114,954/114,954 plays), so the definition is at play level, not player level.
* **Primary set** = `lined_up_position ∈ {EDGE, INTERIOR_LINE}` (Q3) AND pass
  play AND `player_get_off` inside `schema.get_off_range`. Base set = *every*
  row with a `player_get_off`, so the CB/SLOT rows are counted as excluded
  rather than dropped.
* **Depth** = `sign * (x_at_snap − LOS_x)`, `sign=+1` for `play_direction=right`.
  The defence lines up on the *far* side of the ball: for a right-moving offence
  the rusher sits at `x > LOS` and moves toward decreasing x. Validated on the
  sample (n=1,369 usable): median depth 0.59 yd, p10-p90 0.21-1.02, range
  0.01-1.78. The audit's LOS formula is
  used unchanged (`notes/schema.md` §6).
* **Tracked crossing** = the x-axis LOS crossing, interpolated between the
  bracketing 10 Hz frames, through the same Phase 2 gap policy
  (`physical.time_at_distance_qc`): no bridging inside the first second, none
  over 0.5 s. Chosen over an along-travel projection because the audit showed
  the x-axis crossing reproduces `player_get_off` better (abs median 0.383 s vs
  0.679 s). On this sample the signed difference is **−0.370 s** (n=1,368), i.e.
  the tracked crossing leads the label by ~0.37 s — consistent with the audit's
  measurement-lag explanation.
* **Onset** = `physical.detect_onset`, unchanged threshold (absolute 1.0 yd/s
  sustained 2 frames), searched from the snap. `onset_latency_s` is therefore the
  same quantity as in Phase 2, in the same units. The only code change to the
  shared function is an optional `baseline_from` argument that makes the
  *context* baseline pre-snap; it cannot move the onset because the threshold is
  absolute. Phase 2's tests are unchanged and pass.
* **`onset_to_cross_s`** = crossing − onset, and is null with
  `cross_status = CROSSED_BEFORE_ONSET` when the crossing precedes the detected
  onset (115 of 1,454 detected onsets). A negative "duration" is not written.
* **Post-crossing pressure** = `time_to_pressure − player_get_off` observed;
  `dropback_duration − player_get_off` as the censoring time; `post_cross_event
  = 1` iff a pressure time exists. Nothing conditions on pressure occurring;
  rows without it are censored rows. Negative observed times are flagged
  (`NEGATIVE_OBSERVED`), never clamped or dropped.
* **`quick_pressure_binary`** = `quick_pressure IS TRUE` with null → false
  (Q2/D101). The raw tri-state is carried beside it.
* **Stance and shade** are null with an explicit `NOT_AVAILABLE_NO_LABEL`
  status. No inference rule was invented.

**Memory and speed.** Both raw CSVs are converted once to Parquet with explicit
column lists; tracking is partitioned by `game_id` and each game is read from its
own partition. The label-side base table (44,002 rows) is the only table held in
memory. The per-game loop reads ~3-12 k rows, and part files checkpoint every
`snaps.games_per_part = 25` games so a long full run resumes.

One measured defect was fixed rather than tolerated: DuckDB's row-at-a-time
insert (`executemany`) cost **2.7 s per ~800-row part**, making the sample stage
207 s; the columnar insert costs **0.04 s** and the whole sample stage now runs
in **19 s**, peak RSS **355 MB**. Nulls and dtypes round-trip exactly
(`tests/test_phase3.py`).

**Determinism.** Two `--force` runs produce byte-identical Parquet and JSON.

**Test evidence.** 24 Phase-3 tests: known-answer crossing (0.8 yd at 4 yd/s =
0.200 s), known-answer onset-to-cross, pre-snap baseline, insufficient baseline,
missing onset, missing frames (gap inside the first second), no tracking, no snap
event, no post-snap frames, depth near zero and non-positive, crossed-before-
onset, censoring and negative observed times, null→false, empty primary set,
zero-row fallback typing, output schema/type/value assertions, blitz flag
carried-not-applied, one-snap players, checkpointing, determinism, and recovery of
the fixture's planted get-off from its own frames. Full suite: **166 passed**.

**Outputs** (under `outputs/phase3/`): `snaps.parquet` (65 columns, one row per
(game, play, rusher); schema in `snaps_schema.csv`), `phase3_summary.json`,
`exclusion_counts.csv`, `null_provenance.csv`, `null_cooccurrence.csv`,
`snap_counts_by_player.csv`, `blitz_diagnostic.csv`.

---

## D104 — `blitzing` is not a blitz indicator; the exclusion is NOT applied · 2026-11-05

**Finding (evidence, not interpretation).** On the complete label file:

| observation | value |
|---|---:|
| DL pass-rush reps with `blitzing = true` | 32,987 of 33,061 |
| ... `false` | 27 |
| ... null | 47 |
| CB/SLOT rows on the *same* plays with `blitzing = true` | 234 of 12,005 (1.9%) |
| plays with ≥2 DL reps carrying *mixed* `blitzing` values | 2 of 5,836 |
| DL reps on run plays (`pass_result = ''`) with `blitzing` non-null | 77 of 10,501 |
| pressure labels (`quick_pressure` non-null) on `blitzing = true` | 3,375 of 3,375 |

`blitzing` is null on 99.3% of DL run-play reps, true on 99.8% of DL pass-rush
reps, and false on 99.6% of the CB rows of the same plays. That is the signature
of a per-player **pass-rush participation** flag ("this defender rushed the
passer on this play"), not of a blitz call. It is certainly not "the defence sent
more rushers than the line".

**Consequence.** `TASK.md`'s "Blitzers ... are excluded from primary analyses"
cannot be implemented with this column: the literal rule
(`blitzing IS NOT TRUE`) leaves **74** of 33,061 rows. Deleting 99.8% of the
primary set is not an exclusion, it is a destruction of the analysis.

**What the code does.** The exclusion is **not applied**. Every row carries the
raw tri-state `blitz_flag` (`TRUE`/`FALSE`/`NULL`) and
`blitz_exclusion_status = NOT_APPLIED_FLAG_IS_NOT_A_BLITZ_INDICATOR`, so a future
rule can re-filter without re-running. `phase3_summary.json` reports both the
literal-rule counts and the flag distribution.

**Status of this decision: PROPOSED, NEEDS CONOR.** I have not decided that
blitzers are unimportant; I have recorded that this release cannot identify them.
Options for Conor: (a) accept "all DL pass-rush reps" as the primary set and state
that blitzes are unadjustable (recommended, since the evidence is unambiguous);
(b) supply a rule that identifies blitzers from another field if one exists;
(c) obtain a data dictionary that gives `blitzing` its documented meaning and
re-open. Recorded as blocker B9.

---

## D105 — Q4 amendment, implemented in the per-snap table · 2026-11-05

**Decision recorded (D98) and implemented here.** The per-snap table contains no
"assigned blocker(s)" and no "blocker count". `pass_rushers_encountered` is
carried as `pass_rushers_encountered_max` (max over the play's offensive rows)
plus `pass_rushers_encountered_rows` (how many rows carried it), documented as
**descriptive only**.

**Why it cannot be more than descriptive.** The column sits on offensive rows and
is *per blocker*, not per play: its values are 0/1/2/3 and it disagrees across
offensive rows of the same play in 1,550 plays of 45,056. Only players in the
510-attendee release appear, so summing it would silently undercount. It never
enters a model and never stands in for an assignment.

**Consequence.** No blocker control exists; the split is unadjusted for opponent
quality (`notes/limitations.md` §14).

---

## D106 — Q1 revised: SOI 0.30, and the min-reps threshold is a live decision · 2026-11-05 (11:11)

**Decision (Conor, 2026-11-05 11:11, relayed by Main).**
`audit.smallest_effect_of_interest` goes **0.25 -> 0.30**. Because the
detectable floor is `z*sqrt(2/P)`, a 0.30 share needs **86** fittable players
(derived by evaluating `stats.min_detectable_share` itself: P=86 gives 0.2996,
P=85 gives 0.3014 — the number is computed in `phase3_summary.json`, not typed).
The design has fewer than that at the current 50-rep minimum, so
**`min_pass_rush_snaps_per_rusher` is now a live decision**, not a frozen 50.

**What Phase 3 does.** It reports the table and does **not** choose. The active
threshold in `config.yaml` stays **50**, so the primary set is unchanged
(33,061 snaps). New artefact `outputs/phase3/min_reps_sensitivity.csv`:

| min reps | players (primary) | with a 40-yd attempt | floor @ all | floor @ fittable | meets 0.30 |
|---:|---:|---:|---:|---:|---|
| 30 | 109 | 85 | 0.2655 | **0.3007** | no (fittable) |
| 40 | 103 | 80 | 0.2731 | 0.3099 | no |
| 45 | 100 | 78 | 0.2772 | 0.3139 | no |
| **50 (active)** | **99** | **77** | 0.2786 | 0.3159 | no |
| 60 | 92 | 71 | 0.2890 | 0.3290 | no |

Plus the full per-player rep distribution (`reps_per_player_histogram.csv`,
14 bins, 158 players): 31 players have 1-4 primary reps, 13 have 25-49, 12 have
50-74, 20 have 200-299, and 12 have 500+.

**The finding Conor needs.** *No candidate threshold reaches a 0.30 floor on the
fittable set.* The closest is 30 reps with 85 fittable players -> 0.3007, i.e.
0.0007 above the target. Choosing 30 would also add the 31 players with 1-29
reps, whose player means are dominated by sampling error. So the honest options
are (a) accept a floor marginally above the SOI and say so, (b) lower the SOI
again, or (c) treat "at least 0.30" as not resolvable by this design. This is
reported, not resolved here.

**Stated reading of the instruction.** "Config keeps its current value" is read
as applying to the *minimum-reps threshold* (50). The SOI value itself is updated
to 0.30, because the message states the SOI has changed. If instead the whole
config block was to stay untouched, only `audit.smallest_effect_of_interest` needs
reverting.

**Side effect, handled by regeneration.** The Phase 1 gate artefact
(`outputs/audit/`) was produced under SOI 0.25. The test suite re-runs `01_audit`
on the real label file, so it has been regenerated under SOI 0.30 and is committed
in the same change: item (i) now reads "design resolves the smallest effect of
interest: PASS" at **105 label-file players** (floor 0.2705).

**Read that PASS carefully.** It is computed on the label-file player count, which
is not the set Phase 4 can fit: 105 players have ≥50 get-off reps, but only 77 of
those have a Combine 40-yard attempt, and at that count the floor is 0.3159, above
0.30. The Phase 3 table is the design statement that matters; the audit row keeps
its original phrasing and is superseded on this point by D106.

---

## D107 — Phase 3b added: per-player reliability and the selection-rule evidence · 2026-11-05

**What was asked (Conor, relayed by Main).** Compute **both** selection rules so
he can see per-player reliability. Do not choose the threshold. Do not touch the
Phase 4 models. Produce the honest reliability number whether or not it confirms
his expectation ("I'd say it is very low").

**What was added.** A new stage `03b_reliability`, run between Phase 3 and
Phase 4 (`python src/run.py 03b_reliability --mode sample`), plus
`src/reliability.py` (pure estimators) and `tests/test_reliability.py`. It reads
`outputs/phase3/snaps.parquet` (label-side columns only),
`data/raw/player_play.csv`, `games.csv`, `combine_tracking.csv`, `players.csv`.
It writes `outputs/phase3b/` and `notes/selection-rule.md`.

**Deviation from TASK.md's layout, stated openly.** `TASK.md` fixes the order
01→05. `03b` is an evidence stage inserted before Phase 4 because the Phase 4
analysis set has to be defensible before a model is fitted. The 01→05 chain is
unchanged; `tests/test_run_order.py` now asserts the six-stage order.

**Provenance ruling (this is the part that matters).** Every input to this stage
is a complete file, so its outputs are stamped **`FULL`**, not
`UNVALIDATED SAMPLE OUTPUT`, and the basis is written per file to
`outputs/phase3b/input_provenance.csv`. This is not the mode stamp: `common.write_*`
gained an explicit `stamp=` override for exactly this case, and the stage refuses
to write if the Phase 3 table's label side does not reconcile **exactly** with a
direct query on the complete `player_play.csv` (44,002 get-off rows, 33,061
primary rows — both match). No tracking column is read, so the 2023 tracking
sample cannot contaminate the numbers.

**Findings (evidence, not decisions).**

1. **Reliability is not "very low" for players above ~10 reps.** Two-level
   `k = sigma_e^2/sigma_u^2 = 6.17` (bootstrap 4.80–8.76) → 0.942 at the median
   101 reps. The three-level model (reps in games in players) gives `sigma_u =
   0.0988 s`, `sigma_v = 0.0815 s`, `sigma_e = 0.2364 s` → **0.898** at the
   median. The rep-level formula overstates reliability by ~4 points because reps
   within a game are correlated; only the three-level model reproduces the
   observed split-half attenuation curve (RMSE 0.018 vs 0.072).
2. **Split-half, two samples.** 141 of 158 players are estimable at all
   (17 have a single game); raw half-half `r = 0.452`, SB 0.622. Restricted to
   the 119 players with ≥2 games per half, `r = 0.736`, SB 0.848. Both are
   attenuated by unequal halves — the curve is in `split_half_curve.csv`.
3. **Phase 1's 0.634 is reproduced exactly** (ICC(2,1) = 0.634145, r = 0.634872,
   n = 159) on all 44,002 `player_get_off` rows with league-wide chronological
   rank parity. It is a control on a different sample, not the primary-set number.
4. **Between-player share is 0.139 on the primary set**, noise-corrected
   (0.233 by the uncorrected Phase 1 method). Phase 1's 0.285 pooled run plays and
   non-DL rows and did not remove sampling error. This is roughly half the SOI of
   0.30 — see the question in D108.
5. **Rule B, as specified, is Rule A in disguise.** With a common `k`,
   `n/(n+k) ≥ r` ⇔ `n ≥ k·r/(1−r)`: at k = 6.17 the implied cuts are 6.2, 9.3,
   14.4, 24.7 reps for r = 0.5/0.6/0.7/0.8. It cannot admit a different set. It
   only becomes a distinct rule if reliability is computed per player with games
   as a level (reported as `B_three_level_PROPOSED`, a proposal, not a decision).
6. **The power artefact is real in principle but small at these cuts.** Median
   fittable reliability is 0.94 at 20 reps and 0.95 at 50; the Kish effective
   count at the 20-rep cut is 89.3 against 90 heads, and the floor moves 0.2922 →
   0.2938. All 33 players below 0.5 reliability have ≤6 reps and are admitted by
   no candidate rule. The threshold question between 20 and 50 reps is a
   headcount question, not a reliability one — stated so nobody can use
   "reliability" as a post-hoc justification for a threshold.
7. **At 20 reps the SOI is met for the first time**: 90 fittable players, floor
   0.2922. At 30 reps it is 0.3007 (fails), at 50 it is 0.3159. Adding the
   candidates below 30 is what crosses the line.

**Not chosen.** `audit.min_pass_rush_snaps_per_rusher` stays 50; no Phase 4 input
was written; `threshold_chosen: false` in the summary.

**Determinism.** Two consecutive `--force` runs produced byte-identical outputs
(md5 over all eight artefacts + the note). Full suite: **187 passed**.

---

## D108 — The SOI's denominator is ambiguous, and 0.30 may exceed the effect · 2026-11-05

**Status: PROPOSED, NEEDS CONOR.** Not a decision and not resolved by inference.

**The evidence.** On the primary set (33,061 DL non-run pass-rush reps) the
between-player share of *total* get-off variance is **0.139** after removing
sampling error (one-way moments: `s2u = 0.0101`, `s2e = 0.0622`), or **0.233**
by the uncorrected Phase 1 method (variance of player means over variance of
means + pooled within). Phase 1 reported **0.285**, on all 44,002 get-off rows
including run plays and non-DL alignments. Restricting to the primary set halves
the corrected share, and every Phase 4 adjustment (depth, alignment, opponent)
can only lower it further.

**Why it matters.** D106 set `smallest_effect_of_interest = 0.30` and derived
that 0.30 needs 86 fittable players. If the estimand is the between-player share
of total variance, then 0.30 is about twice the observed value, and **no
selection rule can make the study able to resolve it**: the detectable floor is
0.2922 at 20 reps (90 fittable), 0.3159 at 50 reps (77 fittable), and 0.2499
over all 123 fittable players. The threshold decision is then moot until the
SOI or the design changes.

**The ambiguity I will not resolve myself.** D106 calibrates 0.30 against the
between-player share of *total* variance (Phase 1's 0.285). `TASK.md` Phase 4
asks for "the share of between-player variance explained by `b*phys`, by `u`,
and the within-player remainder" — i.e. a split of the *between-player*
variance into physical and residual parts. Under that second reading the SOI is
a share of a smaller quantity, the observed value is not yet estimated (it is
Phase 4's job), and the floor comparison above does not transfer unchanged.

**Question for Conor (one line).** Which share does `smallest_effect_of_interest
= 0.30` refer to: (a) the between-player share of **total** get-off variance, or
(b) the share of **between-player** variance attributed to `u`? If (a), the SOI
is above anything this design can detect and needs revisiting before the prereg
is frozen.

---

## D109 — Crossing diagnostic: the label is a plane crossing ~0.86 yd past the line, not the line crossing · 2026-11-05

**Status: EVIDENCE (decided: how to diagnose, what not to change). One question
is left open for Conor (B10) and is not resolved by inference.**

**Context.** Conor approved the investigation at 13:36: on the 1,819 snaps with
both quantities the tracked crossing precedes `player_get_off` by median
−0.379 s, and the gap (SD 0.205) is not a constant. Five hypotheses were to be
tested in order and a verdict given for each.

**What I decided (method):**

1. The diagnostic is a **standalone script** (`src/crossing_diagnostic.py`), not
   a stage. `run.py`'s STAGES is frozen by TASK.md and checked by
   `tests/test_run_order.py`; adding a stage would either break that contract or
   imply an order TASK.md does not have. The script still uses
   `C.stage_bootstrap`, so the full-mode gates (prereg, tracking) apply to it too.
2. The candidate definitions and the reporting cuts are in config
   (`bdb27.diagnostic`), not in code; the only new threshold is
   `min_share_of_gap_explained: 0.5` (a candidate must account for half the
   median gap to be called explanatory) and `los_systematic_bound_yd: 0.30`
   (a physical bound from the snapper's body geometry, not an estimate).
3. The crossing time is **not changed**, and no offset is applied anywhere.
   `player_get_off` is not touched, and no Phase 4 input is written.

**What it establishes (sample, 1,819 reps / 44 players / 253 games):**

* **H1 LOS error — rejected.** The formula and sign are validated; the line
  error is bounded at ≈0.3 yd = 0.15 s, 39% of the gap. The ball is not tracked.
  The integer `yardline_number` leaves a per-play spot error of ≈0.29 yd
  (uniform ±0.5), which explains the *scatter*, not the *bias*.
* **H2 sign — rejected.** 99.9% of defensive reps move against the offence;
  a mirrored axis would make the depth non-positive for nearly all of them.
* **H3 reference point — confirmed.** The label is a crossing of a plane
  **+0.86 yd** past the line (median, SD 0.31). It is a distance (gap ↔ speed
  +0.57; a clock offset removes no scatter) and a plane (paired within-play
  slope 0.99 ± 0.11, where a fixed displacement gives 0). The fixed 1.5 yd
  alternative wins on raw RMSE only because it discards the noisy depth — an
  errors-in-variables artefact, which is why the paired test is the decisive one.
* **H4 onset — rejected.** The crossing is computed from the snap; the onset
  only gates it. corr(gap, onset) = −0.13.
* **H5 per-group — not a player constant.** 93% of the plane offset's variance
  is play-level (paired within-play SD 0.06 yd vs between-play 0.23 yd).

**The reading I am recording, and its limit.** The magnitude and structure fit a
**body-reference offset**: a lineman's bounding box is ≈1.7 yd from fingertips to
heels, so its rear edge is ≈0.85 yd behind the tracked centre. That is
consistent with the slope, the play-level structure, the magnitude and the
absence of a height correlation, but it **cannot be verified** — no bounding box,
no ball track and no data dictionary (B4) exist in the release. So the
*definition* is not adopted; only the structure is.

**What this changes for Phase 2/4.** Phase 2 computes `phys` for the pre-snap
depth (0.65 yd median); the label times 1.50 yd. `b` would come out ≈1.31 and be
misread as a stance/start-type difference. No time offset can fix that, because
the error is a distance. The distance definition must be chosen and frozen
before the prereg — **B10**, question for Conor:
*"Should Phase 4's physical time be evaluated at the pre-snap depth (as TASK.md
says), at the label's effective distance (needs a preregistered definitional
constant estimated on full data), or should the outcome itself move to the
tracked crossing so both sides use the same definition?"*

**Determinism.** Two consecutive runs produced byte-identical artefacts (md5).
Full suite: **206 passed**, including 19 new known-answer tests for the LOS,
the sign and the crossing interpolation.

---

## D110 — The outcome is the tracked LOS crossing on both sides; `player_get_off` is validation only · Conor, 2026-11-05

**Status: DECIDED (Conor, 2026-11-05). Clears B10.**

**Decision.** The outcome in Phase 3 and Phase 4 is the **tracked
line-of-scrimmage crossing**: `cross_time_s`, the time from the snap to the
first tracked position that crosses the LOS derived from `yardline_side` /
`yardline_number` / `play_direction`. It is used on **both** sides of the model:
the outcome is the crossing, and `phys_i(d_ij)` is evaluated at the pre-snap
depth, the distance to that same crossing. `player_get_off` is demoted to a
**validation check**, never an outcome and never a model input. The two tracked
quantities are the same event, so Phase 2's existing distance definition is
correct as written and **no offset of any kind is applied**.

**Why this is the coherent choice.** The 2026-11-05 crossing diagnostic (D109)
showed the published label times a plane ≈0.86 yd past the line (sample median),
i.e. a median 1.50 yd of travel, against a pre-snap depth of 0.65 yd. Using the
label as the outcome while evaluating `phys` at the pre-snap depth compares two
different events and would return `b ≈ 1.31`, misreported as a stance difference.
Moving the outcome to the tracked crossing makes the estimand internally
consistent: both sides measure the same line crossing.

**Costs, recorded not hidden.** The tracked crossing inherits the integer-yard
ball-spot error (≈0.29 yd, ≈0.14 s; sample estimate, `UNVALIDATED SAMPLE
OUTPUT`) and the ≈0.3 yd line bound. The reliability
and power evidence were measured on the published label, so the power table is
an optimistic upper bound for the locked estimand (D114). These are limitations,
not reasons to reintroduce an offset.

**Hard constraints.** The 0.86 yd plane offset is `UNVALIDATED SAMPLE OUTPUT`
and is **not hard-coded anywhere** in code, config or the prereg. The label
remains measured and reported as validation: median label − tracked crossing =
+0.379 s (p10 +0.191, p90 +0.664) and median plane offset 0.86 yd (sample).
Those numbers are stamped as sample evidence and never enter a model.

**Consequences.** `notes/prereg.md` fixes the tracked crossing as the outcome;
TASK.md Phase 2/3/4 carry in-line amendments; Phase 4's `u_i` is estimated from
the tracked crossing. B10 is closed as *decided*; it is no longer a blocker.

---

## D111 — The smallest effect of interest is a 50 ms jump SD, on the between-player denominator · Conor, 2026-11-05

**Status: DECIDED (Conor, 2026-11-05). Supersedes the 0.30 variance-share SOI
of D106/D108.**

**Decision.** The SOI is a **50 ms SD of the residual jump**, expressed as a
share of **between-player variance** (denominator B: the variance of player
effects), not of total get-off variance. On the Phase 3b FULL evidence the
between-player SD is **0.1004 s**, so 50 ms is **49.8% of that SD** and
**24.8% of between-player variance**. The share is the **square** of the SD
ratio; comparing the unsquared ratio to a variance-share floor would overstate
power by roughly four times in players required.

**Inputs (all `FULL`, complete label files, not sample tracking).**
- Between-player SD: 0.1004 s (`sd_between_player_anova` = 0.100387).
- Between-player share of total variance (noise-corrected): 0.139.
- Three-level reliability at the median player: 0.898; two-level `k = 6.17`.

**Why this resolves D108.** D108 asked whether 0.30 referred to (a) the
between-player share of total variance or (b) a share of between-player
variance. Conor's answer is neither 0.30 nor a share at all for the estimand:
the SOI is an **absolute jump SD of 50 ms**, and the share is a derived
diagnostic on denominator B. The old 0.30 share is withdrawn; it is not carried
into the prereg.

**Power consequence.** Computed in D114: 50 ms is **not detectable** at any
candidate minimum-reps cut (smallest detectable 54.4 ms at 20 reps; 51.8 ms with
all 123 fittable players), and would need `P_eff ≥ 125`. This is recorded as the
honest power answer, not adjusted away.

---

## D112 — The pressure secondary is dropped entirely · Conor, 2026-11-05

**Status: DECIDED (Conor, 2026-11-05).**

**Decision.** Spec question 3 (secondary: does the residual predict quick
pressure beyond physical burst, in held-out games?) is **dropped**. No pressure
field enters any model. The `quick_pressure` null → false rule (Q2/D101) is
**withdrawn**, and its offence-row sensitivity family is withdrawn with it. The
Phase 4 outcome-test subsection is removed (struck through in TASK.md for
provenance), and the three-model outcome figure is dropped from the
deliverables.

**Why.** Pressure labels in this release are model-derived and mostly null
(89.8% of primary reps), so the secondary rested on a missingness assumption
(limitations §15). With the outcome moved to the tracked crossing (D110) and the
SOI fixed as a jump SD (D111), the secondary adds a large, weakly identified
side analysis that cannot change the primary answer.

**What remains.** `sack`, `quick_pressure`, `time_to_pressure` and the other
pressure columns are not analysed and not modelled. They may be described in the
data audit as missingness evidence only. `player_get_off` remains a validation
check (D110).

---

## D113 — The blitz exclusion is dropped; all DL pass-rush reps stay · Conor, 2026-11-05

**Status: DECIDED (Conor, 2026-11-05). Supersedes the "non-blitz" primary-set
wording and the D104/B9 interim posture.**

**Decision.** The primary set is **all DL pass-rush reps**. The blitz exclusion
is not applied, not approximated and not inferred. The raw `blitzing` flag may
be carried per row as descriptive metadata, but it does not filter any analysis.
This is documented as a limitation: the estimated player effects mix blitz and
base reps, and the writeup must not claim a non-blitz sample.

**Why.** The release has no blitz-call field; `blitzing` behaves as pass-rush
participation (true on 99.8% of DL pass-rush reps), and excluding it would leave
27 rows of 43,562 (B9, limitations §16). Dropping the exclusion keeps the
primary set complete and avoids pretending a flag is a call.

**Consequences.** TASK.md Phase 3 is amended in-line. `notes/limitations.md` §16
is updated from "not implementable" to "deliberately dropped (D113)".

---

## D114 — Power: 50 ms is not detectable at any candidate cut; the min-reps rule has no solution · 2026-11-05

**Status: EVIDENCE + OPEN QUESTION. Not a decision. The min-reps proposal needs
Conor.**

**What was computed.** `src/power_calc.py` (standalone, not a `run.py` stage)
reads the complete-file Phase 3b artefacts and writes
`outputs/prereg/power.json` + `outputs/prereg/power_table.md`. The estimator is
the Phase 1(i)/Phase 3b design floor `z·sqrt(2/P_eff)` — a normal approximation
to the sampling SD of a between-player variance share — with `P_eff` the Kish
count of the fittable players' three-level reliabilities, inverted through
`share = (jump_sd / between_sd)^2`. The functions live in `src/stats.py`
(`effective_players`, `min_detectable_share_effective`,
`variance_share_from_sd`, `sd_from_variance_share`,
`players_required_for_share`). It is a design approximation, not a posterior
width; Phase 4's fit is the confirmation.

**Result (FULL evidence, no tracking input).**

| min reps/player | P admitted | fittable P | median reliability, fittable | P_eff | smallest detectable jump SD (ms) | 50 ms? |
|---:|---:|---:|---:|---:|---:|:---:|
| 20 | 115 | 90 | 0.943 | 89.3 | 54.4 | no |
| 25 | 112 | 87 | 0.944 | 86.4 | 54.8 | no |
| 30 | 109 | 85 | 0.945 | 84.5 | 55.1 | no |
| 40 | 103 | 80 | 0.945 | 79.7 | 55.9 | no |
| 50 | 99 | 77 | 0.946 | 76.8 | 56.5 | no |
| 60 | 92 | 71 | 0.949 | 70.9 | 57.6 | no |
| no cut (all) | 158 | 123 | 0.897 | 108.0 | 51.8 | no |

**Answers.** 50 ms = 0.248 of between-player variance. **No candidate cut
resolves it**, and neither does the whole fittable set: the best case is
**51.8 ms** with all 123 fittable players. It would become detectable at
`P_eff ≥ 125`; the release supplies at most **123** fittable players, so the
locked SOI is outside what this design can resolve. The 20-rep cut gives 90
fittable players and 54.4 ms.

**The min-reps rule has no solution.** The agreed rule — "the most restrictive
rep threshold whose fittable P still resolves 50 ms" — selects nothing, because
no cut resolves 50 ms. I will not silently substitute a different rule. My
**proposal, pending Conor**, is the least restrictive candidate, **20 reps per
player** (P_fittable = 90, P_eff = 89.3, smallest detectable 54.4 ms), because
when the target is unreachable the defensible move is to maximise fittable
players rather than tighten a threshold that cannot reach it. The prereg records
20 reps as DRAFT, explicitly does **not** claim it satisfies the SOI, and cannot
be frozen until Conor confirms or changes this.

**Honesty limits.** (1) Reliability is measured on the raw get-off mean, an
upper bound on the residual jump's reliability (0.869 at 25% removed, 0.815 at
50%); the table is a best case. (2) The locked outcome is the tracked crossing,
which adds the sample-estimated ≈0.29 yd spot error; that noise is absent from the reliability
evidence, so the true smallest detectable SD is larger. (3) The 0.86 yd plane
offset is not used anywhere.

**Determinism.** The script is pure arithmetic on committed artefacts; two runs
produce byte-identical output. Full suite: see the commit message.

---

## D115 — Prereg frozen with interval reporting; no confirmatory SOI threshold · Conor, 2026-11-05

**Status: DECIDED (Conor, 2026-11-05 22:16 GMT). Supersedes the SOI gate of
D111/D114 as a decision rule; the resolution numbers remain as evidence.**

**Decision.** Conor's instruction was **"just report whatever it gives you"**.
The prereg is frozen with **no smallest-effect-of-interest threshold**, no
confirmatory jump-share gate and no pass/fail rule. The primary result is the
**share of between-player variance with a 95% interval**. The design's
**resolution limit** (smallest detectable jump SD, ~52–54 ms depending on the
candidate cut; `outputs/prereg/power.json`) is reported alongside as a
**resolution statement**, not as a test the estimate must pass.

**Why.** The D114 evidence showed that 50 ms is at or below the design floor at
every candidate cut, so a threshold at 50 ms would either never bind or would
turn an inconclusive interval into a false "null". Reporting the interval and the
resolution limit is the honest alternative and removes the min-reps deadlock:
the 20-rep minimum is now frozen as the least restrictive candidate that
maximises fittable players, not as a cut that "resolves" a threshold.

**What changed in the frozen document.** `notes/prereg.md` status is
`**Status: FROZEN.**`; §9 freezes 20 reps with the new rationale; §10 is
retitled "Resolution limit and interval reporting — no confirmatory threshold"
and the power table loses its pass/fail column; §11 items 4 and 8 and §13/§14
are reworded accordingly. `TASK.md` Phase 5 item 5 and the Rules bullet carry
in-line 2026-11-05 22:16 amendments. Everything else — onset (D87), distance =
pre-snap depth to the LOS, outcome = tracked LOS crossing on both sides with
`player_get_off` validation-only (D110), no stance label / stance absorbed into
the residual (B8), model form, reliability threshold, minimum snaps — is
unchanged.

**Freeze discipline.** The commit that sets the status line is the freeze. No
Phase 4 model may be fitted before it. The 0.30 `audit.smallest_effect_of_interest`
value and the earlier Phase 1/3 design diagnostics remain in the historical
evidence and code; they are **not** used for inference and are not carried into
Phase 4 as a threshold.

**Verification.** `common.prereg_is_frozen` returns true for the new status line;
`python src/run.py 01_audit --mode full` now passes the prereg gate and is
refused only by the missing full tracking files (the expected full-mode gate on
this Pi).
