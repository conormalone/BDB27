# Blockers — burst vs. jump

A blocker is something that **stops a specified analysis from being done
correctly**, not something that is merely hard. Each entry states what is blocked,
the evidence, and exactly what would clear it. Nothing here has been worked
around with a proxy.

Maintained from 2026-11-05 (Phase 0+1 run). Previous blockers file (leverage ×
ceiling) is archived at
`archive/legacy-2026-11-05/notes/blockers-legacy.md`.

---

## B1 — `quick_pressure` null semantics  · **RESOLVED BY DECISION (Q2, Conor 2026-11-05)**

**Resolution.** Conor decided (Q2, D101): null -> **false**. The secondary
outcome is `(quick_pressure IS TRUE)` over all non-blitz DL pass-rush snaps, with
the null->false assumption stated explicitly in the prereg when it is written.
The offence-row family (`pressure_allowed`, `time_to_pressure_allowed`,
`sack_allowed`) is a **pre-specified sensitivity comparison**, not an alternative
primary. This manufactures 40,184 negative labels from nulls; that risk is now
recorded in `notes/limitations.md` §5, not treated as resolved evidence. No
implemented stage reads `quick_pressure` yet (Phase 3-4 are stubs).

**Blocks:** `TASK.md` Phase 4 secondary outcome test ("predict `quick_pressure` …
in held-out games"), and the `quick_pressure` half of Phase 3's outcome table.

**Evidence.** On the 43,562 DL rows that carry `player_get_off`:

* `quick_pressure` is non-null on 3,378 rows and **null on 40,184 (92.2%)**;
* `time_to_pressure` has exactly the same null pattern (3,418 non-null);
* the 3,418 rows with a pressure time split into 2,334 `false`, 1,044 `true`,
  and **40 with a null flag**.

`quick_pressure` and `time_to_pressure` behave like an **event record**: they are
populated together, and a null almost certainly means "no pressure on this rep".
But that is an inference. Treating null as `false` would manufacture 40,184
negative labels; treating null as missing makes the outcome available on 7.8% of
snaps only.

**What would clear it.** Either (a) an official data dictionary stating that null
means "no pressure", or (b) a documented decision by Conor to define the outcome
as `pressure_allowed`/`sack_allowed` on the offensive row, or as
`(quick_pressure IS TRUE)` with null→false and the assumption stated in the
prereg. **(b) was chosen explicitly on 2026-11-05; (a) remains the only route to
verified semantics.**

**Interim rule in the code.** No stage reads `quick_pressure` yet (Phase 3-4 are
stubs), so nothing is currently wrong. The audit records the null counts and the
`FAIL` row so this cannot be forgotten.

---

## B2 — rusher–blocker assignments do not exist as data  · **RESOLVED — NOT APPLICABLE (Q4, Conor 2026-11-05)**

**Resolution.** Conor decided that the blocker term is dropped from every model,
that assignments are **not** to be inferred from tracking geometry, and that the
blocker / blocker-count fields are removed from the Phase 3 field list. The gap
is no longer a question about a needed input: there is no blocker term to feed.
`TASK.md` Phase 3/4 were amended (D98). The consequence — the physical/jump split
is unadjusted for opponent quality — is recorded in `notes/limitations.md` §14.
The historical evidence below is kept for provenance.

**Blocks:** `TASK.md` Phase 3's per-snap fields "assigned blocker(s) and blocker
count", and the `+ blocker` term in the Phase 4 model.

**Evidence.** No column in `player_play.csv` or any tracking file names a blocker
for a rusher. The nearest fields are `pass_rushers_encountered` (an integer count
on the play), `unblocked_pressure` (a flag) and `pressure_allowed` /
`sack_allowed` (on the offensive player's row).

The blocker could in principle be *inferred* from tracking geometry
(nearest offensive lineman in front of the rusher at the snap), but:

* the provided tracking sample carries **1–4 players per play** (median 1), so the
  linemen are usually not present;
* `TASK.md` does not authorise an inference method, and inventing one would breach
  the "if the task is silent, that is a question" rule.

**What would clear it.** Full game tracking (22 players per play) plus a documented
matching rule — nearest opponent within *x* yards and in front along the rush
direction — approved by Conor and written into the prereg.

---

## B3 — position vocabularies disagree, and one is noisy  · **RESOLVED (Q3, Conor 2026-11-05)**

**Resolution.** Trust `player_play.lined_up_position` as the in-game
position/alignment authority; do **not** require `combine_position` to agree; do
not add a second rule. Players whose only get-off reps are on `CB`/`SLOT_CB` rows
are excluded from the primary set and listed in
`outputs/phase2/position_conflicts.csv` with their rep counts, so the exclusion is
documented rather than silent (D97). Recomputed counts match the evidence below:
53 EDGE-attended-as-DB, 9 INTERIOR_LINE-attended-as-DB, 25 CB/SLOT-attended-as-DL,
440 CB/SLOT get-off reps. The split remains *noisy* — that is a stated limitation,
not an unresolved question.

**Blocks:** the edge vs. interior split's *validity* (not its computability).

**Evidence** (`notes/schema.md` §5a):

* `player_play.lined_up_position` uses `EDGE` / `INTERIOR_LINE`, never `DE`/`DT`/`NT`.
* `players.csv` and `combine_results.csv` use `DE`/`DT`/`NT`/`DL`/`DB`.
* Cross-tab: **53** players labelled `EDGE` in games attended the Combine as `DB`;
  **9** `INTERIOR_LINE` as `DB`; **7** `CB` and **25** `SLOT_CB` as `DL`.
* **440** `player_get_off` reps sit on rows labelled `CB` or `SLOT_CB`.

At most one of the two labels is right for those players, and the data cannot say
which.

**What would clear it.** A rule from Conor, e.g. "trust `lined_up_position` because
it is the in-game alignment and drop the conflicting players", or "require both
labels to agree and report the excluded count". **Neither has been chosen.**
The audit reports the split using `lined_up_position` and states the conflict.

---

## B4 — no official reproduction of `player_get_off`'s definition  · **OPEN (accepted, wording fixed)**

**Status update (2026-11-05).** No data dictionary has appeared. Phase 2 does not
read `player_get_off`; it measures Combine kinematics from tracking only. The
working definition remains "as defined in the competition data", and the writeup
must not quote an official wording (see `notes/limitations.md`). This is a
standing interpretive limit, not a computational blocker.

**Blocks:** nothing computationally; it limits how strongly the label can be
interpreted.

**Evidence.** No data dictionary ships with the files, and the Kaggle pages are
client-rendered shells (`notes/rules.md` §1). The empirical check in the audit
(`notes/phase1-audit.md` item d) shows an x-axis crossing reproduces the label
better than an along-travel crossing, which is *consistent* with "crosses the
line of scrimmage" but does not prove the official wording.

**What would clear it.** A readable copy of the competition data dictionary.
Until then, the writeup must say "as defined in the competition data" rather than
quoting a definition as official.

---

## B5 — the provided dataset is pre-filtered to 510 Combine attendees  · **OPEN (structural)**

**Blocks:** any estimate of selection effects, and any claim about "NFL defensive
linemen" as a population.

**Evidence.** `players.csv`, `combine_results.csv` and the `nfl_id` set in
`player_play.csv` are the same 497–510 players (join is 100% both ways; there are
zero unmatched rows). Only 1,001 of 1,002 games appear in `player_play`.

**What would clear it.** Nothing, in this dataset. It is a property of the release.
It must be stated in the writeup as the study population, not buried as a
limitation.

---

## B6 — prereg is not written  · **OPEN (blocks every full run; Q5 answered, file still absent)**

**Status update (2026-11-05).** Q1 (smallest effect of interest) is now answered
and in `config.yaml` (D96). Q5 is now answered as **option D, a two-part freeze**
(D102): the confirmatory core is frozen next cycle before Phase 3 results, and an
operational annexe (onset threshold, distance grid, minimum snaps) is frozen
immediately after Phase 3. `notes/prereg.md` still does not exist during this
cycle. Phase 2 is therefore implemented and unit-tested but driven only in
`--mode sample` plus synthetic data. The full-mode gate was not weakened:
`python src/run.py 02_combine_physical_time --mode full` exits 4 with the prereg
refusal.

**Tension to resolve before Phase 3 (flagged in D102).** `TASK.md` requires the
prereg to fix the operational constants *before* the first full-data run; option
D freezes them *after* Phase 3. Either the annexe must be frozen before that run
too, or `TASK.md` must say the Phase 3 run is operational and only Phase 4 is
confirmatory. Not reconciled here.

**Blocks:** `--mode full`. The gate in `src/common.py` refuses to run in full mode
unless `notes/prereg.md` exists and declares `**Status: FROZEN.**`.

**Evidence.** The file does not exist. The previous prereg belonged to the retired
intra-drive-regeneration spec and has been archived at
`archive/legacy-2026-11-05/notes/prereg.md`.

**What would clear it.** Write and freeze the confirmatory core first, then the
operational annexe per Q5/D102. The gate only reads `notes/prereg.md` for
`**Status: FROZEN.**`; whichever document is authoritative for a given full run
must exist and be frozen at that moment.

---

## B7 — no full-data game tracking on this machine  · **OPEN (environmental)**

**Blocks:** every `PENDING_FULL_RUN` item.

**Evidence.** `data/raw` contains `game_tracking_2023_sample.csv` only. The three
full-season files named in `config.yaml` (`game_tracking_2023.csv`,
`game_tracking_2024.csv`, `game_tracking_2025.csv`) do not exist; there are no
Kaggle credentials at `~/.kaggle`.

**What would clear it.** The full tracking files on the larger machine. The code
path is already shared: `--mode full` selects them by config key and refuses to
run if they are missing.

**Not a blocker for this run:** Phase 1 is answerable because the Combine data and
the label file are complete; only tracking-derived checks are limited.

---

## B8 — Phase 2 calibration offsets are not estimable from the released files  · **OPEN (blocks a calibrated full run)**

**Blocks:** `TASK.md` Phase 2's "global (not per-player) calibration offsets for
stance and start type", and therefore any Phase 4 model that is supposed to use
calibrated physical times.

**Evidence.**

* No column in any provided file labels stance (three-point vs stand-up). Phase 1
  §c recorded this and did not test inferability.
* No column labels the Combine start type.
* The global Combine-to-game offset needs in-game onset-to-cross times, which are
  produced by Phase 3 — a stage that does not exist yet.
* `TASK.md` forbids calibrating from the sample, so nothing can be estimated in
  this run.

**What the code does.** Phase 2 writes `outputs/calibration.json` with
`status = "PLACEHOLDER"` (sample) and `estimates = null`, plus the exact
estimator specification. `common.require_calibration_full` refuses a placeholder
or non-estimable record in full mode. Phase 2 itself refuses to complete a full
run when calibration is not `ESTIMATED` (TASK.md stop condition).

**What would clear it.** A stance label (or a Conor-approved inference rule), a
recorded start type, and Phase 3's in-game times; then estimate the global
offsets on full data and re-run Phase 2 to write `status = "ESTIMATED"` with
`FULL` provenance.
