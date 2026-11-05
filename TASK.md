# TASK: Burst vs. jump: how much of a pass rusher's get-off is legs, and how much is reading the snap?

> **Provenance.** Supplied by Conor via Telegram, 2026-11-05 06:59–07:00 GMT, in five messages.
> Transcribed verbatim; only message separators and markdown heading levels were normalised.
>
> **Supersedes** the 2026-11-04 intra-drive-regeneration (IDR) spec, archived at
> `notes/archive-tasks/TASK-intra-drive-regeneration-2026-11-04.md`, together with its prereg
> and decisions where they conflict with this spec.
>
> **Design change in one line:** the exposure is no longer a per-snap kinematic deficit; it is a
> **decomposition of between-player get-off variance** into a Combine-predicted physical part and
> a residual player effect ("jump"), with reliability and outcome tests on the residual.

NFL Big Data Bowl 2027 (Kaggle), deadline Jan 6, 2027 23:59 UTC.

## Goal
Split between-player differences in defensive-line get-off into a **physical** part (measured at the Combine, where there is no snap to react to) and a **residual "jump"** part (everything the legs don't explain), then test whether the residual is a stable trait that matters on the field.

Questions:
1. **Primary (confirmatory):** what share of between-player variance in `player_get_off` is explained by Combine-predicted physical time versus a residual player effect? Report with intervals, for DL pooled and edge vs. interior.
2. **Primary (second):** is the residual ("jump") reliable across games, and does it agree with a direct measure of reaction latency (snap to first movement) where motion onset is detectable?
3. **Secondary:** does the residual predict quick pressure beyond physical burst, in held-out games?
4. **Exploratory:** within a player, does get-off improve year to year through the residual while physical time stays flat?

Claims are limited to DL who reached the NFL (only draftees have game tracking). "Jump" is a residual, not a measurement of anticipation or football IQ. It also contains stance, technique, alignment and onset-definition differences. Never claim more than the data support.

## Environment
You run on a Raspberry Pi with limited RAM. Game tracking here is a ~100 MB SAMPLE; the final run happens on a bigger machine. Produce correct, memory-safe, tested code that runs unchanged on full data. Scripts 01-05 must run end-to-end in `--mode sample` on sample or synthetic data. Label every sample output `UNVALIDATED SAMPLE OUTPUT`. Never interpret, report or tune against sample results.

## Rules
- Never invent data, columns or results. If something is missing or ambiguous, stop and write to /notes/blockers.md.
- Official competition docs and rules are the source of truth; third-party repos are unverified hints. No external data.
- Column names below come from a data dictionary and must be verified against the actual files; log mismatches in /notes/schema.md.
- Log decisions and reasons in /notes/decisions.md. Report null results honestly.
- Fixed seeds, scripts not ad-hoc cells, strict sequential gating.
- Before the first full-data run, write /notes/prereg.md fixing: onset definition, distance definition, stance handling, variance-decomposition model, reliability thresholds, outcome definitions, minimum snaps per player, smallest effect of interest. Do not change it after seeing full-data results; log any deviation as exploratory.

## Variables to expect (verify in Phase 1)
Game: `player_get_off` (seconds from snap until the rusher crosses the line of scrimmage), `quick_pressure` (1.0 if pressured within 2.50 s; may be null), `time_to_pressure`, `time_to_qb_hurry`, `sack`, `blitzing`, `unblocked_pressure` (may be null), `dropback_duration`, plus x/y/speed/time tracking, pre-snap alignment, snap time, line-of-scrimmage location. (Rusher–blocker assignments were expected but do not exist; removed by the Q4 amendment.)
Combine: x/y/time tracking per drill attempt for DL.

## Engineering
- Scripts take `--mode sample|full` and `--force`, one code path for both. Paths, thresholds and sizes live in config.yaml; no magic numbers.
- Never load full CSVs into pandas. Convert to Parquet, then query with DuckDB or lazy Polars with explicit column selection. Process game data per game or player in chunks and write small per-snap summary tables; modeling stages read only summaries.
- Downcast dtypes. Log wall time and peak RAM per stage to outputs/run_log.csv.
- Stages checkpoint to disk and skip finished work unless `--force`.
- Check how the sample was drawn and note which code paths it cannot exercise. If it lacks enough DL, generate a synthetic schema-compliant set with planted burst and jump effects.

## Phases

**0. Setup.** Download data via Kaggle API. Summarize rules and limits (writeup and figure caps, notebook requirements, external data) in /notes/rules.md. Record schemas and file sizes in /notes/schema.md.

**1. Audit (gate: kill test).** Evidence, not assumptions:
a. Combine tracking for DL: which drills exist; is a straight-line sprint from a stance present; attempts per player; class years covered.
b. Matched Combine-to-NFL DL; pass-rush snaps per matched rusher; games per rusher; edge vs. interior split.
c. Can pre-snap depth (distance from the rusher to the line of scrimmage along the direction of travel) and snap time be derived from the tracking? Is stance labeled (three-point vs. stand-up), or inferable from pre-snap speed and position? Record how each is defined.
d. Exact definition of `player_get_off` in the documentation. Is it crossing the line, and is it measured from the snap?
e. **Frames in the first second:** how many frames cover the first yard and first second of a Combine run and of a game snap? Report onset detectability for both: can motion onset be defined by the same speed or displacement threshold in both settings?
f. Null semantics and counts for `quick_pressure`, `unblocked_pressure`, `time_to_pressure`. Provenance of pressure labels: record whether any label is derived from the rusher's own tracking.
g. **Kill-test statistics (full data only; script now, mark PENDING FULL RUN):**
 - Split-half reliability (ICC) of mean get-off across games, per rusher.
 - Correlation between Combine-predicted physical time and mean get-off.
 - Between-player SD of mean get-off vs. within-player SD.
h. Test-retest reliability (ICC) of Combine start-to-distance times across attempts.
i. Power: smallest detectable between-player variance share given players and snaps per player.
Go/no-go: stop if a Combine sprint is absent, pre-snap depth or line of scrimmage cannot be derived, onset cannot be defined consistently across settings, mean get-off split-half ICC is below the config threshold, Combine-predicted time shows no relation to mean get-off at the player level, or snaps per rusher fall below config minima. Log the decision.

**2. Combine physical time** (run for real if the Combine data fits in RAM).
- Define motion onset in the Combine run with the prereg threshold (speed or displacement), identical to the in-game definition. Measure time from onset to reaching distance *d* along the direction of travel by interpolating the tracked distance curve (model-free). Use a parametric speed-time fit (v(t) = vmax(1 - exp(-t/tau))) only as a cross-check; it is poorly constrained in the first yard.
- Compute physical time for each in-game pre-snap depth *d* (the distance the rusher must cover to cross the line), per attempt, then pool attempts per player with hierarchical shrinkage. Carry posterior SEs forward; never use a best attempt.
- Global (not per-player) calibration offsets for stance and start type, estimated in the full run and logged in outputs/calibration.json (provenance "PLACEHOLDER_SAMPLE" in sample mode, "FULL" in full mode; later scripts assert "FULL" in full mode). Never calibrate from the sample.
- Keep original attempt numbers and real clock time. Sanity checks: flag speeds above 12 yd/s, duplicate attempts, gaps inside the first second. Interpolate gaps under 0.5 s only outside the first second.
- Output a per-player table: physical time as a function of distance, with uncertainty.

**3. Game snaps** (full data only for final; scripted and unit-tested now).

> **Spec amendment (Conor, 2026-11-05, Q4).** Rusher–blocker assignments do not
> exist in the data, are not to be inferred from tracking geometry, and are
> removed from the Phase 3 field list and from every Phase 4 model. Blocker
> open questions are resolved-not-applicable. Logged as D98; consequence in
> `notes/limitations.md` §14.

- Primary snap set: pass-rush snaps by DL (non-blitz). Blitzers and non-DL are excluded from primary analyses.
- Per snap: `player_get_off`, pre-snap depth to the line, alignment (edge vs. interior, shade), stance (if labeled or inferable), blitz flag, down and distance, score margin, quarter, game, team, opponent.
- **Direct reaction measure:** snap-to-onset latency from tracking, with the Phase 1 onset definition. Also onset-to-cross time (the in-game counterpart of Combine physical time).
- Outcomes: `quick_pressure` (null -> false per Q2/D101, with the assumption stated in the prereg; offence-row family as a pre-specified sensitivity), time to pressure after crossing the line (time_to_pressure minus get-off, censored at dropback end, using `dropback_duration` only for censoring), sack (exploratory). Never analyze `time_to_pressure` only among pressured snaps.
- Write one row per snap; modeling stages read only these tables.

**4. Models.**
- **Variance decomposition (primary):** hierarchical model for rep-level get-off:
 `get_off_ij = a + b * phys_i(d_ij) + u_i + alignment/stance/depth effects + team-game random effects + e_ij`
 with u_i ~ N(0, sigma_u^2). Treat phys_i as latent with its posterior SE (measurement-error model, Bayesian or regression calibration). Physics predicts b near 1; report b and its interval, since b far from 1 means systematic differences between settings.
 Report: share of between-player variance explained by b * phys, by u, and the within-player remainder, all with posterior intervals. Edge, interior and pooled. Fit with Stan or PyMC on the bigger machine.
- **Reliability of jump:** split-half across games (odd vs. even games) of player-level u, and model-based reliability (sigma_u^2 / (sigma_u^2 + mean SE^2)).
- **Agreement with direct latency:** correlation between player-level u and mean snap-to-onset latency; and between phys and mean onset-to-cross time. Report both; agreement supports the interpretation, disagreement is a finding.
- **Outcome test (secondary):** estimate u and phys from odd-numbered games; predict `quick_pressure` (logistic mixed model) and post-crossing pressure time (censored survival model) in even-numbered games, with team-game random effects and alignment controls. Compare models with phys only, with u added, and with both. Player-grouped cross-validation; held-out log score with bootstrap CIs over players. This split prevents get-off from mechanically contributing to the outcome it predicts.
- **Born or learned (exploratory):** within player, year-to-year change in get-off decomposed into change in u and change in phys-related terms (weight and role changes noted as confounds).
- Convergence fallbacks, logged in decisions.md: (1) simpler variance model with phys as a fixed covariate and no measurement-error correction (report both); (2) per-player estimates with empirical Bayes shrinkage; (3) random intercept only.

**5. Validation.**
- **Permutation:** shuffle Combine physical time across players within position; the explained variance share should fall to near zero.
- **Placebo:** Combine height and weight alone should not reproduce the physical-time effect after controlling for pre-snap depth.
- **Mechanical checks:** re-run with depth and alignment effects removed and with stance-specific subsets; vary the onset threshold (config grid) and report sensitivity of the variance shares.
- **Snap quality:** restrict to snaps with clean onset detection; report share dropped.
- **Selection:** note that only drafted DL appear; report snap counts by player and results weighted to equalize snap counts per player.
- State final sample sizes and the smallest detectable variance share. Interpret a small jump share as informative only if the interval excludes the smallest effect of interest.

**6. Deliverables.** Public Kaggle notebook and writeup within official limits (placeholders for results until the full run), max 5 figures. Suggested figures: (1) the variance split with intervals, edge vs. interior; (2) Combine-predicted physical time versus observed get-off, with named players off the diagonal; (3) reliability and agreement of jump; (4) outcome comparison of the three models; (5) one rusher's rep-level story. Sections: question, data audit, physical-time construction, decomposition, reliability, outcomes, limitations.

`/notes/limitations.md` lists every threat and mitigation (stance and start-definition differences between Combine and snap, alignment effects, 10 Hz resolution in the first second, shared baseline error across a player's reps, model-derived pressure labels, rotation, draftee-only selection, short follow-up for the 2025 class).

## Tests before handoff
1. Schema assertions on columns, dtypes and units; fail loudly on mismatch.
2. Synthetic recovery: simulated rushers with planted physical times, planted jump variance and known b. The decomposition must recover the planted variance shares. Include a null world (no jump variance) and a world with a shared baseline error to confirm the measurement-error model corrects the inflation.
3. Edge cases: rushers with few snaps, missing onset, null labels, one-attempt players, missing frames, snaps with depth near zero, no game data.
4. Scale: run on the sample, then on the sample concatenated several times with renamed IDs. Check roughly linear memory growth, extrapolate to full size in /notes/scale.md, and flag stages likely to exceed RAM.
5. Determinism: same seed gives identical outputs.

## Handoff
- README_RUN.md: clone, environment setup, data download, ordered commands, expected runtime and RAM per stage.
- Pinned requirements.txt that works on ARM and x86.
- /notes/pending_full_run.md: questions only full data can answer, with the script for each.
- /notes/assumptions.md: unverified assumptions about full data, each with a runtime assertion.
- .gitignore data; commit only code, configs, notes and synthetic fixtures.

## Stop and write to blockers.md if
A Combine sprint is absent; pre-snap depth or the line of scrimmage cannot be derived; onset cannot be defined consistently in both settings; mean get-off reliability is below threshold; Combine-predicted time shows no relation to get-off at the player level; calibration provenance is not "FULL" in a full run; prereg.md is missing before a full run; or any instruction conflicts with competition rules.

## Layout and run order
/data/raw /data/interim /src /notebooks /notes /outputs/figures
01_audit -> 02_combine_physical_time -> 03_game_snaps -> 04_models -> 05_validate
