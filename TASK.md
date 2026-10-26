# TASK: In-game decay of DB performance relative to the Combine ceiling
NFL Big Data Bowl 2027 (Kaggle), deadline Jan 6, 2027 23:59 UTC.

> **Scope change (2026-10-25):** this spec replaces the WR/TE draft-stock ("security") task, which is
> archived at `notes/archive-tasks/TASK-wr-security-2026-10-20.md` and in git history. Prior combine-fatigue
> work (archived tag `archive/combine-fatigue-v1`) found no within-session Combine fatigue signal; that result
> says nothing either way about in-game decay, which is the question here.

## Goal
Using each defensive back's Combine-derived ceiling as a baseline, test whether high-intensity effort (as a
fraction of ceiling) decays with accumulated in-game load, and whether a higher ceiling protects against decay.

Questions:
1. **Primary:** is there a population-level decline in effort fraction with cumulative in-game load, on
   max-effort plays, after context controls?
2. **Secondary:** does Combine ceiling moderate the decline (load x ceiling interaction)?
3. **Exploratory:** do reliable per-player decay slopes exist?

Claims are limited to DBs who reached the NFL (only draftees have game tracking). Interpret decay as an
in-game decline in effort fraction under the stated controls, not as proven physiological fatigue. A
within-session Combine fatigue analysis found nothing; do not assume that predicts the in-game result either way.

## Environment
You run on a Raspberry Pi with limited RAM. Game tracking here is a ~100 MB SAMPLE; the final run happens on a
bigger machine. Produce correct, memory-safe, tested code that runs unchanged on full data. Scripts 01-05 must
run end-to-end in `--mode sample` on sample or synthetic data. Label every sample output
`UNVALIDATED SAMPLE OUTPUT`. Never interpret, report or tune against sample results.

## Rules
- Never invent data, columns or results. If something is missing or ambiguous, stop and write to
  `/notes/blockers.md`.
- Official competition docs and rules are the source of truth; third-party repos are unverified hints. No
  external data unless permitted.
- Log decisions and reasons in `/notes/decisions.md`. Report null results honestly.
- Fixed seeds, scripts not ad-hoc cells, strict sequential gating.
- Before the first full-data run, write `/notes/prereg.md` fixing: effort definition, load definition,
  controls, garbage-time rule, minimum snaps per player, primary and secondary estimands, reliability
  threshold, and smallest effect of interest. Do not change it after seeing full-data results; log any
  deviation as exploratory.

## Engineering
- Scripts take `--mode sample|full` and `--force`, one code path for both. Paths, thresholds and sizes live in
  config.yaml; no magic numbers.
- Never load full CSVs into pandas. Convert to Parquet, then query with DuckDB or lazy Polars with explicit
  column selection. Process game data per game or player in chunks and write small per-effort summary tables;
  modeling stages read only summaries.
- Downcast dtypes. Log wall time and peak RAM per stage to outputs/run_log.csv.
- Stages checkpoint to disk and skip finished work unless `--force`.
- Check how the sample was drawn and note which code paths it cannot exercise. If it lacks enough matched DBs,
  generate a synthetic schema-compliant set with a planted ceiling and a planted decay slope.

## Phases

**0. Setup.** Data is on device storage. Summarize rules and limits (writeup and figure caps, notebook
requirements, external data) in /notes/rules.md. Record schemas and file sizes in /notes/schema.md.

**1. Audit (gate: go/no-go).** Evidence, not assumptions:
a. Are DBs in the Combine tracking? Which drills exist for them, and which are straight-line, backpedal, cut
   or shuffle drills?
b. Attempts per player per drill; Combine class years covered.
c. Matched DBs between Combine and game tracking; plays and snaps per matched DB, by alignment (outside corner,
   slot, safety) if derivable.
d. Game data: are all 22 players tracked per play, or only some? Are coverage assignments, man/zone labels,
   ball location, time of throw, play result, score and game clock available? Which plays are included (all
   pass snaps, or only some)?
e. Snap counts by quarter per DB, to check substitution and survivorship within games.
f. Is the provided Combine distance and acceleration reliable? Compare with values derived from x/y; check
   sampling rate, units, coordinates and Combine vs. game processing differences.
g. Test-retest reliability (ICC) of candidate Combine features: vmax, a0, deceleration and cut metrics.
h. Exceedance rate: how often in-game speed or acceleration exceeds the Combine envelope.
i. Power calculation: smallest load coefficient detectable given matched DBs, efforts per DB and the number of
   games per DB.
Game-side items needing full data: script now, mark PENDING FULL RUN.
Go/no-go: stop if DBs are absent from the Combine tracking, matched DBs or efforts per DB fall below config
minima, key Combine features have ICC below the config threshold, or the power calculation shows only
implausibly large effects are detectable. Log the decision.

**2. Combine ceiling** (run for real if the Combine data fits in RAM).
- Smooth the speed curve and fit v(t) = vmax * (1 - exp(-t/tau)) per attempt on straight-line drills; derive
  a0 = vmax/tau. Do not differentiate raw positions for acceleration.
- For DB-specific drills, compute entry speed, braking distance, exit speed and deceleration rate from smoothed
  speed.
- Pool attempts per player with hierarchical shrinkage. Use shrunken estimates with standard errors everywhere
  downstream; never a best attempt.
- vmax and a0 are strongly negatively correlated within players. Report it, keep at most three features, choose
  by ICC and VIF fixed in prereg.
- Keep original attempt numbers and real clock time. Sanity checks: flag speeds above 12 yd/s, duplicate
  attempts, gaps inside the peak window. Interpolate gaps under 0.5 s only outside peak windows.
- Output a per-player feature table with uncertainty (SE).

**3. Game efforts** (full data only for final; scripted and unit-tested now).
- `00_thresholds.py` writes outputs/thresholds.json (effort cutoffs, minimum snaps, garbage-time rule).
  Provenance is "PLACEHOLDER_SAMPLE" in sample mode and "FULL" in full mode; scripts 03 to 05 assert
  provenance == "FULL" in full mode. Never set thresholds from the sample. Freeze before Phase 4.
- Define a high-intensity effort by speed or acceleration above the thresholds. For reactive movement, also
  define: closing speed after the ball is thrown, acceleration or deceleration at a direction break, and
  sprint start.
- Effort fraction = observed speed (or acceleration) divided by the player's shrunken ceiling at that speed.
  Carry the ceiling's SE forward.
- Cumulative load per effort: distance covered and number of high-intensity efforts so far in the game; also
  snaps in the current drive, time since last rest, and game clock.
- Max-effort subset (primary analysis): efforts with the ball in the air, tackle attempts and pursuit, where
  effort is least discretionary. All-efforts is a secondary analysis.
- Context: play type, alignment, score margin, down and distance, garbage-time flag (rule in prereg), quarter.
  If opposing players are tracked: nearest receiver's speed and route type or depth. If not, state that matchup
  demand is a confound and rely on within-game contrasts.
- Write one row per effort to a summary table; modeling stages read only these.

**4. Models.**
- Primary (max-effort subset): mixed model
  `effort_fraction ~ cumulative_load + snaps_in_drive + time_since_rest + context controls + (1 + cumulative_load | player)`
  Center and rescale load. Estimand: population-level coefficient on cumulative_load.
- Secondary: add cumulative_load x ceiling (shrunken vmax or the pre-registered feature). Handle error in the
  ceiling with posterior SEs and a measurement-error-corrected estimate, reported alongside the naive one.
- Convergence fallbacks, logged in decisions.md: (1) uncorrelated random effects `(1|player) + (0+cumulative_load|player)`
  with an alternate optimizer; (2) per-player slopes with empirical Bayes shrinkage; (3) random intercept only,
  report population-level results and skip per-player slopes.
- Also fit: all-efforts model; within-player contrast version (same DB early vs. late on comparable plays
  matched on play type, alignment and score margin).
- Per-player slopes: report only if model-based reliability = slope variance / (slope variance + mean squared SE)
  is above the prereg threshold. Do not use odd/even split-half.

**5. Validation.**
- Permutation: shuffle effort order within player-game; the load effect should vanish.
- Placebo: load should not predict effort fraction on non-max-effort plays that were already decided, or
  ceiling-free measures such as route depth.
- Substitution and survivorship: report results restricted to DBs playing all four quarters, and a version
  weighting by probability of remaining in the game.
- Sensitivity: effort threshold, load definition, garbage-time rule, alignment subgroups, minimum-snap filters,
  excluding the final two minutes of each half.
- Player-grouped cross-validation for predictive comparisons, with bootstrap CIs.
- State the smallest detectable effect and final sample sizes. Interpret a null as informative only if the
  interval excludes the smallest effect of interest.

**6. Deliverables.** Public Kaggle notebook and writeup within official limits (placeholders for results until
the full run), max 5 figures. Sections: problem, data audit, ceiling model, decay model, results, limitations.
/notes/limitations.md lists every threat and mitigation (opponent-set demand, game script, pacing, substitution
and survivorship, noisy per-player slopes, measurement error in the ceiling, draftee-only selection, short
follow-up for the 2025 class).

## Tests before handoff
1. Schema assertions on columns, dtypes and units; fail loudly on mismatch.
2. Synthetic recovery: simulated DBs with a planted ceiling, planted load coefficient and planted load x
   ceiling interaction. Both mixed models and fallbacks must recover them. Include a null case (the pipeline
   must not find decay) and a case with within-game substitution.
3. Edge cases: one-attempt players, missing frames, ties, DBs with few efforts, games with missing quarters, no
   game data.
4. Scale: run on the sample, then on the sample concatenated several times with renamed IDs. Check roughly
   linear memory growth, extrapolate to full size in /notes/scale.md, and flag stages likely to exceed RAM.
5. Determinism: same seed gives identical outputs.

## Handoff
- README_RUN.md: clone, environment setup, data download, ordered commands, expected runtime and RAM per stage.
- Pinned requirements.txt that works on ARM and x86.
- /notes/pending_full_run.md: questions only full data can answer, with the script for each.
- /notes/assumptions.md: unverified assumptions about full data, each with a runtime assertion.
- .gitignore data; commit only code, configs, notes and synthetic fixtures.

## Stop and write to blockers.md if
DBs are absent from the Combine tracking; a required Combine or game field is missing; matched DBs or efforts
per DB are below config minima; key Combine features are unreliable; thresholds provenance is not "FULL" in a
full run; prereg.md is missing before a full run; or any instruction conflicts with competition rules.

## Layout and run order
/data/raw /data/interim /src /notebooks /notes /outputs/figures
01_audit -> 02_combine_ceiling -> 00_thresholds -> 03_game_efforts -> 04_models -> 05_validate
