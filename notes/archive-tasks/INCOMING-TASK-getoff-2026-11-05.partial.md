# INCOMING TASK (PARTIAL CAPTURE) — Get-off: burst vs. jump

> Status: **INCOMPLETE**. Pasted by Conor via Telegram, 2026-11-05 06:59 GMT, in two messages.
> Phases 4 and 5 have not been received. Do not treat as the binding spec until complete and
> promoted to `TASK.md`. Captured verbatim; separators normalised.

---

## Header message

# TASK: Burst vs. jump: how much of a pass rusher's get-off is legs, and how much is reading the snap?
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
Game: `player_get_off` (seconds from snap until the rusher crosses the line of scrimmage), `quick_pressure` (1.0 if pressured within 2.50 s; may be null), `time_to_pressure`, `time_to_qb_hurry`, `sack`, `blitzing`, `unblocked_pressure` (may be null), `dropback_duration`, plus x/y/speed/time tracking, pre-snap alignment, snap time, line-of-scrimmage location, rusher-blocker assignments.
Combine: x/y/time tracking per drill attempt for DL.

## Engineering
- Scripts take `--mode sample|full` and `--force`, one code path for both. Paths, thresholds and sizes live in config.yaml; no magic numbers.
- Never load full CSVs into pandas. Convert to Parquet, then query with DuckDB or lazy Polars with explicit column selection. Process game data per game or player in chunks and write small per-snap summary tables; modeling stages read only summaries.
- Downcast dtypes. Log wall time and peak RAM per stage to outputs/run_log.csv.
- Stages checkpoint to disk and skip finished work unless `--force`.
- Check how the sample was drawn and note which code paths it cannot exercise. If it lacks enough DL, generate a synthetic schema-compliant set with planted burst and jump effects.

## Phases

---

## Phases message (0-3 received)

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
- Primary snap set: pass-rush snaps by DL (non-blitz). Blitzers and non-DL are excluded from primary analyses.
- Per snap: `player_get_off`, pre-snap depth to the line, alignment (edge vs. interior, shade), stance (if labeled or inferable), assigned blocker(s) and blocker count, blitz flag, down and distance, score margin, quarter, game, team, opponent.
