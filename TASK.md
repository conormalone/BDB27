# TASK: Intra-Drive Regeneration, Inter-Snap Rest, and Pass-Rush Kinematic Deficits

> **Provenance.** Supplied by Conor via Telegram, 2026-11-04 10:12 GMT, in two messages.
> Transcribed verbatim; only the message separators and markdown heading levels were normalised.
>
> **Supersedes** the 2026-10-30 DL acceleration-ceiling spec, archived at
> `notes/archive-tasks/TASK-dl-acceleration-ceiling-2026-10-30.md`, together with its prereg
> and decisions D68-D81 where they conflict with this spec.
>
> **Design change in one line:** a_0 stops being the predictor and becomes the baseline.
> The modelled exposure is now the per-snap kinematic deficit — which varies on every snap,
> instead of once per player.

NFL Big Data Bowl 2027 (Kaggle), deadline Jan 6, 2027 23:59 UTC.

## Goal
Use a defensive lineman's (DL) Combine-derived acceleration (a_0) not as a static predictor, but as a **baseline** to measure snap-level kinematic deficits. Test whether exact inter-snap rest and rolling kinematic load predict transient drops in pass-rush performance, bypassing the small-N constraint by treating every snap as an independent exposure.

Questions:
1. **Primary (confirmatory):** Does the interaction between rolling kinematic load and inter-snap rest predict a player's acceleration deficit on the current snap?
2. **Primary (confirmatory, second):** Do these transient, snap-level kinematic deficits predict drops in pass-rush win rate and time-to-pressure better than absolute cumulative game load?
3. **Exploratory:** At what specific rest-to-load ratio does an elite starter's pressure probability drop below that of a fresh rotational player?

_Note:_ Claims are limited to draftee DL with game tracking. The ceiling (a_0) is a player-level baseline, but the modeled _exposure_ (deficit) varies on every snap.

## Environment
Runs on a Raspberry Pi (limited RAM) using a ~100 MB SAMPLE of game tracking. Produce memory-safe code that runs unchanged on full data. Scripts 01-05 must run end-to-end via --mode sample. Never report or tune against sample results; tag all sample output UNVALIDATED SAMPLE OUTPUT.

## Rules
- Never invent data. Log ambiguities in /notes/blockers.md and schema mismatches in /notes/schema.md.
- Competition docs are the ultimate truth. No external data.
- Log decisions in /notes/decisions.md.
- Freeze variables, outcome definitions, load calculations, and smallest effect of interest in /notes/prereg.md before the full-data run.

## Variables to Expect
**Defender-side:** player_get_off, time_to_pressure, quick_pressure (null handling critical), sack, blitzing, alignment.
**Offensive-side:** dropback_duration (snap to release/sack/pressure - _censoring only_), blockers assigned.
**Derived (Phase 3):** inter_snap_rest (seconds between prior play end and current snap), rolling_kinematic_load (integral of acceleration work over prior n snaps).

## Engineering
- CLI takes --mode sample|full and --force. Thresholds live in config.yaml.
- Convert CSVs to Parquet immediately. Use DuckDB/lazy Polars for rolling window aggregations (rest and load).
- Downcast dtypes. Output per-snap summary tables; models read only these summaries. Log RAM and wall time.

## Phases

**0. Setup:** Document rules, schemas, and limits.

**1. Audit (Gate: go/no-go):**
- Verify matched Combine-to-NFL DL and tracking completeness. Snap coverage must be continuous to calculate inter-snap rest; flag rusher-games with missing intermediate snaps.
- Resolve null semantics for pressure events.
- Test-retest reliability (ICC) of the Combine baseline a_0. Stop if ICC is below config thresholds or snap coverage breaks rest calculations.

**2. Baseline Ceiling (Combine):**
- Fit v(t) = v_max * (1 - exp(-t/tau)) to straight-line drills to derive a_0 = v_max/tau.
- This establishes the player's 100% fresh baseline. Shrink estimates hierarchically. Output player-level feature table with standard errors.

**3. Game Snaps, Rest & Load:**
- **Inter-Snap Rest:** Use window functions (lag) to calculate the real-world seconds between the terminal event of play N-1 and the snap of play N.
- **Absolute Load:** Cumulative distance/time above a speed threshold over the game.
- **Dynamic Deficit (The Exposure):** Current snap peak acceleration as a fraction of Combine baseline a_0, modeled as a function of rolling_kinematic_load and inter_snap_rest.
- Output one row per snap. _The variance occurs here, scaling N to the tens of thousands._

**4. Models:**
- **Part 1 (Load/Rest to Deficit):** Mixed-effects model predicting current snap acceleration based on prior load and rest duration.

- **Part 2 (Deficit to Pressure):** Time-to-pressure survival model (Cox or AFT). Include frailties by rusher, crossed effects for blocker/team, and fixed controls (alignment, blitz, down/distance, score margin).
- **Comparison:** Hold-out log score comparing the dynamic deficit model vs. absolute cumulative load model via player-grouped cross-validation.

**5. Validation:**
- **Within-Player Permutation:** Shuffle inter-snap rest times within a player-game; the deficit effect should vanish.
- **Context Controls:** Exclude garbage time, 2-minute drills (systematically different rest mechanics), and obvious passing situations.

**6. Deliverables:** Kaggle notebook (max 5 figures). Include problem framing, IDR methodology, results, and strict limitations (e.g., partial snap coverage breaking rest sequences).

## Tests Before Handoff
- Schema and dtype assertions.
- Synthetic recovery: Pipeline must recover a planted rest-decay interaction.
- Memory scaling: Extrapolate RAM from sample to full data in /notes/scale.md.
- Determinism check.

## Handoff & Run Order
README_RUN.md, requirements.txt, /notes/pending_full_run.md.
Run order: 01_audit -> 02_combine_ceiling -> 00_thresholds -> 03_game_snaps -> 04_models -> 05_validate
