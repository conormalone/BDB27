# TASK: Turn Coverage and the Cut Card: can this player run the turns his job requires, at game speed?
NFL Big Data Bowl 2027 (Kaggle), deadline Jan 6, 2027 23:59 UTC.

## Goal
Build a physics-based measure of each player's turning capacity from Combine tracking, compare it with the turns his position actually makes in games, and deliver (1) a per-prospect Turn Coverage score (share of his position's game turn demand that falls inside his Combine-derived feasible region), (2) cut gaps (specific turns he cannot execute at speed), and (3) a one-page Cut Card visual.

Questions:
1. Transfer (confirmatory): does lateral turning capacity estimated from Combine cuts predict the same capacity estimated from game turns, relative to the noise ceiling set by each setting's reliability?
2. Feasibility (confirmatory): how often do players execute game turns outside their Combine-derived feasible region? The region should rarely be exceeded in the observed-speed range.
3. Value (confirmatory, primary group only): does Turn Coverage predict pass-rush outcomes beyond a stopwatch-and-size baseline (3-cone, shuttle, 40, height, weight)?
4. Descriptive: transfer and feasibility for the other groups. OL and DB are exploratory (contamination risk, see below).

Claims are limited to players who reached the NFL (only draftees have game tracking). In games a turn reflects capacity plus contact, scheme and opponent; the chips cannot see contact. Never claim more than the data support.

Primary group: DL, provided the audit finds DL Combine drills with direction changes. If not, the audit picks WR/TE as primary and the outcome test becomes descriptive.
Priority if time runs short: Phases 1, 2, 3, 5, then 6 transfer and feasibility, then outcome tests, then extra positions.

## Environment
You run on a Raspberry Pi with limited RAM. Game tracking here is a ~100 MB SAMPLE; the final run happens on a bigger machine. Produce correct, memory-safe, tested code that runs unchanged on full data. Scripts 01-06 must run end-to-end in --mode sample on sample or synthetic data. Label every sample output UNVALIDATED SAMPLE OUTPUT. Never interpret, report or tune against sample results.

## Data constraints (known)
- Combine files contain only x, y, dir and dis (plus identifiers and time or frame fields; verify). There is **no speed, acceleration or orientation (o)** in the Combine data.
- Therefore all kinematics are derived from smoothed x/y/time, identically in Combine and game data. Provided dir, dis (and game s, a, o if present) are cross-checks, never inputs to the primary definitions.
- Orientation (o) can be used in game data only, for validation and contamination reporting; it can never be part of the turn definition.
- Tracking is ~10 Hz. Smoothing choices will be the most influential methodological decision; they are pre-registered and stress-tested.

## Rules
- Never invent data, columns or results. If something is missing or ambiguous, stop and write to /notes/blockers.md.
- Official competition docs and rules are the source of truth; third-party repos are unverified hints. No external data.
- Verify every column name against the actual files, including units, angle convention for dir (zero reference, direction of rotation, wrap-around), field orientation and coordinate system. Log mismatches in /notes/schema.md.
- Log decisions and reasons in /notes/decisions.md. Report null results honestly.
- Fixed seeds, scripts not ad-hoc cells, strict sequential gating.
- Before the first full-data run, write /notes/prereg.md fixing: smoothing method and parameters, derivative method, speed floor, turn and plant definitions, bin edges for the demand map, envelope model, definition of "feasible", loss-of-speed tolerance, coverage formula, outcome definitions, baseline model, minimum turns per player, kill-test thresholds, smallest effect of interest. Do not change it after seeing full-data results; log any deviation as exploratory.

## Engineering
- Scripts take --mode sample|full and --force, one code path for both. Paths, thresholds and sizes live in config.yaml; no magic numbers.
- Never load full CSVs into pandas. Convert to Parquet, then query with DuckDB or lazy Polars with explicit column selection. Process game data per game or player in chunks and write small per-turn tables; modeling stages read only the turn tables.
- 00_thresholds.py writes outputs/thresholds.json (smoothing window, minimum turn angle, speed floor, bin edges). Provenance "PLACEHOLDER_SAMPLE" in sample mode and "FULL" in full mode; scripts 02 to 06 assert provenance == "FULL" in full mode. Never set thresholds from the sample. Freeze before Phase 3.
- Downcast dtypes. Log wall time and peak RAM per stage to outputs/run_log.csv.
- Stages checkpoint to disk and skip finished work unless --force.
- Check how the sample was drawn and note which code paths it cannot exercise. If it lacks enough players, generate a synthetic schema-compliant set (x/y/dir/dis for Combine-like data, plus game fields) with planted turn capacities and 10 Hz noise.

## Phases

0. Setup. Download data via Kaggle API. Summarize rules and limits (writeup and figure caps, notebook requirements, external data) in /notes/rules.md. Record schemas and file sizes in /notes/schema.md.

1. Audit (gate: kill test). Evidence, not assumptions:
a. Combine drills per position; which contain direction changes (cone, shuttle, route or position drills); whether DL have any; attempts per player per drill; class years covered. Record any drill geometry provided in documentation.
b. Time and frame fields: sampling rate, timestamp regularity, dropped frames, duplicate frames, start and end of each attempt.
c. Cross-check derived kinematics against provided fields, both settings: compare heading from smoothed x/y velocity with dir (accounting for convention and wrap); compare cumulative dis with path length from x/y; compare derived speed with dis / frame interval; compare with game s and a if present. Quantify disagreement by speed band and flag systematic lag, vendor smoothing or under-counting (a participant repo claims Combine dis under-counts; unverified).
d. Detector validation on Combine drill geometry: run a provisional turn detector on cone and shuttle attempts; detected turn locations and angles should cluster at the physical cone positions. If drill coordinates are not documented, estimate cone locations from the aggregate density of detected turns across players and check consistency across players and years. Report precision and recall of turn detection against that geometry.
e. Smoothing diagnostics: for straight-line sprints, report plausibility of derived acceleration under each candidate smoother (SG with several windows, Kalman or cubic smoothing spline): share of frames exceeding a physical acceleration cap, speed-curve monotonicity during the initial acceleration phase, and agreement across smoothers. Choose the primary smoother in prereg by these diagnostics, never by downstream results.
f. Matched Combine-to-NFL players per position; snaps per matched player by snap type (pass-rush, pass-pro, route, coverage); games per player.
g. Turn counts per player in each setting (provisional config definition) and the distribution of entry speeds in each. Report overlap of Combine and game entry-speed ranges per position; this sets the observed-versus-extrapolated boundary.
h. Frame resolution: frames covering a typical turn at relevant speeds; sensitivity of detected turns to the smoothing window; speed floor below which heading is unreliable.
i. Game-only orientation checks: confirm o exists in game data; record units, zero reference, wrap and offset against dir at steady forward running. Report the orientation signature of kinematic plants (shoulders change before or with direction) and the share of kinematic turn candidates with sustained |o - dir| above the divergence threshold (backpedal, shuffle, slide contamination), by position and snap type.
j. Kill-test statistics (full data only for the game side; script now, mark PENDING FULL RUN):
 - Within-setting reliability (ICC, model-based) of the lateral and plant capacity parameters, Combine and game separately.
 - Correlation between Combine and game capacity per position, and the noise ceiling.
 - Turns per player and share of players with enough turns in both settings.
 - Sensitivity of these statistics to the smoother.
k. Outcome labels: null semantics for quick_pressure, unblocked_pressure, time_to_pressure; provenance of pressure labels (record whether any is derived from the player's own tracking); which labels exist for WR, TE and DB.
l. Rusher-blocker assignments: opponent ID present for all reps, including untracked opponents? (Used only as random effects.)
m. Power: smallest detectable transfer correlation and smallest detectable gain in held-out outcome prediction.
Go/no-go: stop if no Combine drill contains usable direction changes for the primary group; detector precision and recall against drill geometry fall below the config thresholds; derived heading and speed disagree with provided fields beyond the config tolerance without explanation; reliability of the capacity parameter is below the config threshold in either setting under the prereg smoother; Combine and game entry-speed ranges barely overlap; or turns per player fall below config minima. If only the outcome side fails (labels or counts), continue with transfer, feasibility and descriptive coverage, and log it.

2. Kinematics and turn detection.
- Smoothing and derivatives (both settings, same code): smooth x/y with the prereg smoother; derive velocity, speed, heading (atan2 of velocity, angular differences computed mod 360 in [-180, 180]), tangential acceleration and angular rate. Never differentiate raw positions. Do not use provided dir as an input.
- Uncertainty from smoothing: run the primary smoother plus at least two alternatives from the prereg set on every attempt and snap; carry the spread into the turn table (store primary values and smoother-to-smoother ranges), and report whether conclusions depend on the smoother.
- Turn candidates: heading change above the minimum angle within a short window with speed above the speed floor at entry and exit. Record per turn: entry speed, exit speed, apex (minimum) speed, turn angle, duration, path radius, peak lateral acceleration (v * angular rate, with v^2 / r as a cross-check), tangential acceleration at entry and exit, speed retention (exit / entry), time to regain entry speed, and context (drill or snap, position, alignment, snap type, player, game).
- Classification from kinematics only: plant (apex speed below (1 - delta) x entry speed, entry and exit above the speed floor, with re-acceleration) or arc (heading change with apex speed at or above (1 - delta) x entry). Facing-decoupled movement (backpedal, shuffle, slide) cannot be identified in the Combine. In game data only, flag candidates with sustained |o - dir| above the divergence threshold; use the flag for contamination reporting and a game-side sensitivity analysis, never in the primary definition.
- Flag turns with frame gaps inside the turn window; interpolate only outside the turn core, gaps under 0.5 s. Sanity checks: speeds above 12 yd/s, duplicate frames, accelerations above the cap, detections per 1,000 frames versus a shuffled-heading null.
- Write one row per turn to Parquet; later stages read only this.

3. Turn envelope (capacity).
- Observed turns are not all maximal, so estimate the upper envelope, not the mean. Per player, fit lateral capacity as a function of speed with a hierarchical upper-envelope model (a high-quantile or censored-style likelihood, or top-k turns per speed bin), with partial pooling across players within position and posterior SEs. The friction-circle form sqrt(a_tangential^2 + a_lateral^2) <= a_max(v) is the physical anchor; fix the form in prereg and cross-check with a model-free upper quantile.
- Capacity measures: (i) arc capacity (lateral limit at speed), (ii) plant capacity (speed retention and time to regain speed as functions of entry speed and angle). The primary measure is chosen in prereg by audit reliability and counts, never by outcomes.
- Feasible region: (entry speed, turn angle) cells where predicted speed retention is at least the prereg tolerance (for example 90%), with uncertainty from posterior draws and smoother spread (probability each cell is inside).
- Separate observed cells (inside the Combine entry-speed range) from extrapolated cells; always display and score them separately.
- Estimate capacity separately from Combine and game turns (for transfer) and in a combined version for the card. Use shrunken estimates with SEs everywhere; never a best turn.
- Output a per-player table of envelope parameters with uncertainty.

4. Demand map.
- Per position, build the distribution of game turns over (entry speed x turn angle) bins (prereg bin edges), restricted to relevant snap types: DL pass-rush; WR/TE route snaps; DB and OL only as exploratory. Use all tracked players of the position.
- Weights: frequency (default) and, for DL, outcome-weighted (turns on reps with quick pressure or a short time to pressure), with unblocked_pressure reps excluded in a sensitivity version.
- Tracked players are a selected group (drafted, in the data); report this as a limitation of the demand map. Bootstrap over players for bin intervals.

5. Coverage and the Cut Card.
- Turn Coverage per player = demand-weighted share of cells inside his feasible region, reported as a distribution, separately for observed and extrapolated cells and combined.
- Cut gaps: the demand cells with the highest weight and low probability of being feasible.
- Cut Card (figure): one page per illustrative player showing the speed-by-angle heatmap, his feasible region boundary with uncertainty, the NFL demand overlaid, the coverage score and top cut gaps, plus a position-level summary. Do not generate cards for all players.
- Position-level rankings of coverage and the most common cut gaps by role (edge vs. interior, slot vs. outside).

6. Validation and outcome tests.
- Transfer: correlation (and a measurement-error-corrected version) of Combine versus game capacity per position, as a fraction of the noise ceiling; repeated under each smoother.
- Feasibility: share of game turns exceeding the Combine-derived region, by position and speed range (observed vs. extrapolated), reported separately.
- Role and result: within position, do players with low coverage of a turn type execute it less often, or with lower speed retention, in games? Include alignment and role controls.
- Primary-group outcomes (DL): estimate coverage from Combine data only. Test against quick_pressure and post-crossing time to pressure (censored survival model, with dropback_duration used only for censoring). Random effects for player, assigned opponent and team-game; alignment, down and distance and blocker-count controls.
 - Compare three models: baseline (3-cone, shuttle, 40, height, weight, position), baseline plus Turn Coverage, and baseline plus raw capacity. Player-grouped cross-validation with leave-one-class-out as a second scheme; held-out log score or concordance with bootstrap CIs over players.
 - Wherever game-derived quantities enter a predictor, estimate them on odd-numbered games and test outcomes in even-numbered games.
- OL and DB (exploratory): same tests, reported with the contamination share from Phase 1 and flagged accordingly.
- Permutation: shuffle player envelopes within position; transfer and outcome gains should vanish.
- Placebo: height and weight alone should not reproduce the coverage effect after controlling for the baseline model.
- Definition sensitivity: (i) all heading turns, (ii) kinematic plants (primary), (iii) arcs only, (iv) kinematic plants with game-side orientation-flagged contamination removed (game-only; the Combine side cannot be filtered the same way). Also vary smoother, window, minimum angle, bin widths, speed-retention tolerance, envelope form, minimum turns per player, blitz exclusion and unblocked_pressure handling. Report whether conclusions depend on the choices.
- State final sample sizes and the smallest detectable effect. Interpret a null as informative only if the interval excludes the smallest effect of interest.

7. Deliverables. Public Kaggle notebook and writeup within official limits (placeholders for results until the full run), max 5 figures: (1) the Cut Card for two or three illustrative prospects; (2) demand map with feasible-region overlay for one position; (3) transfer scatter with noise ceiling; (4) feasibility results by speed range; (5) held-out outcome comparison against the stopwatch-and-size baseline. Sections: the football question, data audit, kinematics and turn detection, envelope, demand map, coverage, validation, uses for scouts and coaches, limitations. /notes/limitations.md lists every threat and mitigation: contact invisible to the chips; 10 Hz heading noise and smoother dependence; no Combine speed, acceleration or orientation fields (derived from x/y only); plants not separable from backpedals and shuffles in the Combine; Combine entry speeds below game speeds so some cells are extrapolated; selected tracked sample for the demand map; model-derived pressure labels; draftee-only selection; short follow-up for the 2025 class; possible overlap with other entries' cutting metrics.

## Tests before handoff
1. Schema assertions on columns, dtypes, units, angle convention and coordinates; fail loudly on mismatch.
2. Synthetic recovery: simulated players with planted capacity curves, drill geometry (cones), 10 Hz positional noise, sub-maximal turns, and dropped frames. The detector must recover cone turns; the envelope must recover capacities and the feasible region; the coverage score must recover planted differences. Include a null world (all players identical) and a Combine-only speed-range world to test extrapolation flags. Include a world where derived-versus-provided dis disagree to test the cross-check.
3. Smoother tests: each candidate smoother must reproduce known synthetic speed and heading, and the pipeline must report smoother-to-smoother spread.
4. Edge cases: players with few turns, missing frames, ties, no turns in one setting, no game data, missing labels, stops mid-drill (heading undefined at low speed).
5. Scale: run on the sample, then on the sample concatenated several times with renamed IDs. Check roughly linear memory growth, extrapolate to full size in /notes/scale.md, and flag stages likely to exceed RAM.
6. Determinism: same seed gives identical outputs.

## Handoff
- README_RUN.md: clone, environment setup, data download, ordered commands, expected runtime and RAM per stage.
- Pinned requirements.txt that works on ARM and x86.
- /notes/pending_full_run.md: questions only full data can answer, with the script for each (including the kill-test statistics).
- /notes/assumptions.md: unverified assumptions about full data, each with a runtime assertion.
- .gitignore data; commit only code, configs, notes and synthetic fixtures.

## Stop and write to blockers.md if
No Combine drill has usable direction changes for the primary group; detector precision and recall against drill geometry fail; derived kinematics contradict provided fields without explanation; capacity reliability is below threshold in either setting under the prereg smoother; thresholds provenance is not "FULL" in a full run; prereg.md is missing before a full run; or any instruction conflicts with competition rules. If only outcome labels or counts fail, continue with transfer, feasibility and descriptive coverage and log it.

## Layout and run order
/data/raw /data/interim /src /notebooks /notes /outputs/figures
00_thresholds -> 01_audit -> 02_turns -> 03_envelope -> 04_demand_coverage -> 05_validate -> 06_figures
