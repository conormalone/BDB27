# Blockers — burst vs. jump

A blocker is something that **stops a specified analysis from being done
correctly**, not something that is merely hard. Each entry states what is blocked,
the evidence, and exactly what would clear it. Nothing here has been worked
around with a proxy.

Maintained from 2026-11-05 (Phase 0+1 run). Previous blockers file (leverage ×
ceiling) is archived at
`archive/legacy-2026-11-05/notes/blockers-legacy.md`.

---

## B1 — `quick_pressure` cannot be used as a binary outcome  · **OPEN**

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
prereg. **Do not choose (b) silently.**

**Interim rule in the code.** No stage reads `quick_pressure` yet (Phase 3-4 are
stubs), so nothing is currently wrong. The audit records the null counts and the
`FAIL` row so this cannot be forgotten.

---

## B2 — rusher–blocker assignments do not exist as data  · **OPEN**

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

## B3 — position vocabularies disagree, and one is noisy  · **OPEN**

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

## B4 — no official reproduction of `player_get_off`'s definition  · **OPEN**

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

## B6 — prereg is not written  · **OPEN (blocks every full run)**

**Blocks:** `--mode full`. The gate in `src/common.py` refuses to run in full mode
unless `notes/prereg.md` exists and declares `**Status: FROZEN.**`.

**Evidence.** The file does not exist. The previous prereg belonged to the retired
intra-drive-regeneration spec and has been archived at
`archive/legacy-2026-11-05/notes/prereg.md`.

**What would clear it.** Write `notes/prereg.md` fixing, per `TASK.md`: onset
definition, distance definition, stance handling, variance-decomposition model,
reliability thresholds, outcome definitions, minimum snaps per player, and the
smallest effect of interest. Then set `**Status: FROZEN.**`. This is deliberate
work that must happen **before** the first full-data run and after Q1 is answered.

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
