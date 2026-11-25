# Turn importance — result: the 30–60° turn is the mildest movement and the common one

**Status: descriptive result plus one pre-declared stakes estimate. Not a causal finding.**
Written 2026-11-25. Spec: `notes/turn-importance-spec.md` (committed `def977f`, before any band
median or coefficient). Decision: **D223**. Evidence: `outputs/turn_importance/` (10 CSVs +
`summary.json` + `run_log.txt`). Code: `src/bdb27/turn_importance.py`. Notebook:
`notebooks/bdb27_turn_importance.ipynb`. Tests: `tests/test_turn_importance.py`.
`TASK.md`, the band table, the four figures and `notes/report-draft.md` are unchanged.

Every Test 2 number is labelled **`ASSOCIATION (STAKES), NOT CAUSE`**. `separation_at_pass_forward`
is not read anywhere in this cycle.

---

## The short answer

- **Test 1 — the movement is mild.** On all five cost measures the 30–60° band ranks first or
  second gentlest of the six bands. It is not a severe movement; it is the mildest one the
  detector can see.
- **Test 2 — the play is not mild.** A play in which a DB turns 30–60° allows **+0.123 EPA
  (95% CI +0.045 to +0.202)** and **+5.9 completion percentage points (+3.7 to +8.0)** more than
  a play in which that DB does not turn, with player fixed effects and down, distance, coverage,
  position and season controls. That is stakes, not cause: the route broke, so he turned.
- **No sharper band is detectably worse.** None of the five sharper bands' EPA contrasts exceeds
  the 30–60° contrast with a CI excluding zero (0 of 5). The 150–180° band is significantly
  *lower* than 30–60° (p = 0.004, which survives a Bonferroni correction for five comparisons).
- **So the honest "importance" case is frequency plus stakes, not severity.** 30–60° is 13.3% of
  valid DB game turns (7,777 / 58,664) and the band the Combine under-samples 6.01× by share. The
  turn is common, mild, and sits on plays that go badly for the defence.

---

## Test 1 — what the turn costs `[FULL]`

Population: **58,664 valid DB game turns** from **115 players**, **858 games**, seasons 2023–2025.
The file holds 58,665 rows; 1 fails `valid`. Band counts reproduce
`bdb27_full_game_bands.csv` exactly: 30–60° **7,777 (13.26%)**, 60–90° 13,077 (22.29%),
90–120° 13,016 (22.19%), 120–150° 11,444 (19.51%), 150–180° 8,137 (13.87%), >180° 5,213 (8.89%).

Medians by band:

| Band | n | Entry yd/s | Apex yd/s | Peak turn rate °/s | Peak lat. accel yd/s² | Path radius yd | Apex retention | Plant share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **30–60°** | 7,777 | 3.68 | 2.78 | **82.8** | **5.46** | 2.49 | 0.857 | 15.3% |
| 60–90° | 13,077 | 3.63 | 3.08 | 84.0 | 5.50 | 2.38 | 0.934 | 24.3% |
| 90–120° | 13,016 | 4.01 | 2.77 | 110.4 | 7.72 | 2.01 | 0.717 | 51.5% |
| 120–150° | 11,444 | 3.95 | 1.90 | 175.6 | 12.04 | 1.42 | 0.493 | 79.0% |
| 150–180° | 8,137 | 3.81 | 1.04 | 317.7 | 20.79 | 0.99 | 0.279 | 93.4% |
| >180° | 5,213 | 3.68 | 0.58 | 486.1 | 30.20 | 0.40 | 0.156 | 97.7% |

**Rank of 30–60° among the six bands (1 = gentlest):** peak turn rate **1**, peak lateral
acceleration **1**, path radius **1**, apex retention **2**, speed retention **2**. Pre-declared
label: **physically mild** (rank ≤ 2 on 5 of 5; rank ≥ 5 on 0 of 5).

Monotonicity (Spearman ρ against recorded angle): peak turn rate **+0.766**, peak lateral
acceleration **+0.715**, path radius **−0.500** (usable circle fits only, n = 48,929), apex
retention **−0.614**, apex speed −0.595, turn duration +0.520 (each n = 58,664 except radius).
Entry speed is flat (+0.024), so the gradient is not a speed artefact.

**Two honest caveats on the cost measures.**

1. Peak lateral acceleration = v²/r is not physically trustworthy for the reversal classes: the
   >180° median of 30.2 yd/s² is about 2.8 g, which a human cannot generate. Path radius fitted to
   a hairpin does the same. The ordering by **peak turn rate**, a direct angular measurement, is
   unambiguous (82.8 → 486.1 °/s), so the rank survives the caveat.
2. **Speed retention is not monotonic in angle** (ρ = −0.066): plants re-accelerate after the
   apex, so exit speed often exceeds entry speed. It is reported because the spec fixed it in
   advance, but apex retention is the clean retention measure. Neither changes the label.

**The 30–60 band split** (the proposed drill sits at 45°, a boundary value): 30–45° n = 2,225 of
7,777, peak turn rate 89.3 °/s, peak lateral acceleration 5.78 yd/s², speed retention 0.971,
plant share 19.7%; 45–60° n = 5,552, 80.3 °/s, 5.37 yd/s², 1.158, 13.5%. The 30–45 half is slightly
*more* demanding — the gentlest point is not the bottom of the band.

**Regain-entry-speed is uninformative here and is not headlined.** Median is 0.0 s in every band;
2,927 of 7,777 30–60° turns (37.6%) exhaust the regain window without regaining. The measure is
reported with both denominators (4,850 regained / 2,927 exhausted within the band) and is not used
in the severity label.

**Smoother sensitivity** (30–60 band): recorded angle 13.26% of turns; under the minimum-angle
smoother 16.9%; under the maximum-angle smoother 8.6%. The band's median peak lateral acceleration
moves 5.46 → 5.62 → 4.97 yd/s². The rank conclusion is unchanged under all three; the *share* is
the fragile number, which is why the 30° floor makes it a lower bound.

---

## Test 2 — what the turn is worth `[FULL]` `ASSOCIATION (STAKES), NOT CAUSE`

Unit: **DB player-play**. Population: **84,225 rows** over **62,503 plays**, **115 players**,
**858 games**. Exposure: the sharpest turn that DB had on that play, so each row falls in exactly
one band. **37,805 rows (44.9%) carry no turn**; 30–60° carries **3,540 (4.2%)**.

The 4.2% is not the 13.3% band share: a player-play is assigned to its *sharpest* turn, so a play
on which the same DB also plant-cuts at 140° is counted at 140°, and a play with any turn at all
is only 55.1% of DB player-plays. Both denominators are printed; neither is a gate.

Primary sample: pass plays (`pass_result` ∈ C/I/IN/S), penalty-nullified plays excluded.
**46,954 rows over 34,392 plays.**

### Primary estimates, 30–60° vs no turn

| Outcome | β | 95% CI | n rows / plays | Decision label |
|---|---:|---|---:|---|
| **EPA allowed** | **+0.1235** | **+0.0452 to +0.2018** | 46,954 / 34,392 | **STAKES CONFIRMED** |
| **Pass success allowed** (completion) | **+5.85 pp** | **+3.68 to +8.02** | 46,954 / 34,392 | **STAKES CONFIRMED** |
| Yards allowed | +1.11 yd | +0.65 to +1.57 | 46,954 / 34,392 | — |
| Explosive allowed (15+ yd) | +2.99 pp | +1.23 to +4.74 | 46,954 / 34,392 | — |
| YAC allowed (thin subset) | +0.20 yd | −0.59 to +0.99 | 4,937 / 3,465 | — |

Controls: player fixed effects, down, distance, coverage, position, season. SEs two-way clustered
by player and game (115 players, 856 games in the pass sample, 2,228 player-games). p = 0.002 for
EPA, 1.2×10⁻⁷ for completion.

**YAC is thin and selected**: `yards_after_catch` is recorded only on the targeted receiver's row,
and `target` is populated only for cohort players. It is observed on 4,937 of 46,954 pass rows
(10.5%) and is reported descriptively only; the 30–60 coefficient is indistinguishable from zero.

### The gradient `[FULL]`

EPA allowed, relative to no turn (same model). Positive = worse for the defence.

| Band | β | 95% CI |
|---|---:|---|
| 30–60° | +0.1235 | +0.045 to +0.202 |
| 60–90° | +0.1643 | +0.116 to +0.213 |
| 90–120° | +0.2039 | +0.163 to +0.245 |
| 120–150° | +0.0841 | +0.031 to +0.137 |
| 150–180° | −0.0126 | −0.073 to +0.048 |
| >180° | +0.1313 | +0.061 to +0.201 |

Within-turn differences vs 30–60° (delta method): 60–90° **+0.042** (−0.036 to +0.120); 90–120°
**+0.080** (−0.004 to +0.164); 120–150° **−0.040** (−0.120 to +0.041); 150–180° **−0.136**
(−0.228 to −0.044, p = 0.004); >180° **+0.006** (−0.098 to +0.110). Sample 28,640 rows /
19,675 plays / 115 players / 854 games; two-way clustered on player × game
(115 / 854 / 2,179 clusters).

**The pre-declared gradient claim fails: 0 of 5 sharper bands is significantly worse than 30–60°.**
The pattern is not monotone — it peaks at 90–120° and falls away at the deepest plants. The one
significant difference runs the other way: the 150–180° band is **0.14 EPA better** for the defence
than 30–60°.

### Sensitivity set (all reported, none substituted)

| Specification | 30–60° β (EPA) | 95% CI | n rows |
|---|---:|---|---:|
| **Primary** (two-way cluster) | **+0.1235** | +0.045 to +0.202 | 46,954 |
| All plays | +0.0945 | +0.039 to +0.150 | 80,159 |
| Include penalty-nullified plays | +0.1171 | +0.042 to +0.192 | 49,654 |
| Any-turn-in-band indicators | +0.0927 | +0.041 to +0.144 | 46,954 |
| One row per play (sharpest DB turn) | +0.1227 | +0.024 to +0.221 | 34,392 |
| EPA winsorised 1st/99th | +0.1264 | +0.050 to +0.203 | 46,954 |
| Cluster by player | +0.1235 | +0.046 to +0.201 | 46,954 |
| Cluster by play | +0.1235 | +0.044 to +0.203 | 46,954 |
| Cluster by game | +0.1235 | +0.041 to +0.206 | 46,954 |
| Completions only (composition check) | +0.0754 | −0.016 to +0.167 | 27,905 |

The estimate is stable at **+0.09 to +0.13 EPA** across every specification except the
completions-only composition check, which conditions on a post-treatment event and loses
significance. That check is not a robustness failure; it changes the estimand.

The within-turn specification is reported as its own table (above), not as a row here: its
reference category is 30–60°, so it has no single 30–60 coefficient.

---

## What this says, and what it does not

**It says** the 30–60° band is the mildest movement the detector records, and that plays on which
a DB turns that far are associated with materially worse defensive outcomes than plays on which he
does not turn — about the same as 60–90° and 90–120°, not worse, and better than the deepest
plants. The case for the band's importance is **frequency (13.3% of game turns) plus stakes**, not
severity.

**It does not say** that turning 30–60° causes a worse outcome. The turn is post-snap and
endogenous: the receiver broke, the DB turned, the ball often went there. The comparison group —
DB player-plays with no turn — is also selected: quick throws, routes without a break, and deep
safeties far from the ball all live there. Player fixed effects and the position control narrow
that gap but do not close it. **This is a stakes measurement, not an effect of turning.**

**It does not say** a null would mean the band is unimportant, and it does not promote the
positive result into a causal claim. The pre-declared labels are applied in §3.8 of the spec and
reported verbatim above.

**It does not touch separation.** No `separation_at_pass_forward`, no receiver matching, no
attribution set, no 3-yd rule. Every Test 2 row is the DB's own `player_play` row.

**On frequency.** 30–60° is 7,777 / 58,664 = 13.26% of game turns; no turn in the file falls below
30° at all — the detector applies that floor upstream — so the band is the mildest one it can see
and is censored at its own floor (TASK.md honesty limit 2). If the
Combine is to add a drill, the argument is that the common mild break is under-sampled 6.01× by
share and sits on plays where the defence does worse — not that 30–60° is physically punishing.

---

## Provenance

| Item | Value |
|---|---|
| Turn table | `data/interim/full_run_20261111/bdb27_full_db_turns.parquet`, sha256 `f2e7cd01…` |
| Player-play | `data/interim/parquet/player_play.parquet`, sha256 `a38352ac…` |
| Games | `data/interim/parquet/games.parquet`, sha256 `60ee8162…` |
| Sample turns (unused) | `outputs/turns/turns.parquet`, sha256 `89a41609…` |
| Provenance stamp | `FULL` on every row of every artefact |
| Determinism | verified, not asserted: a fresh run of this code reproduced all 10 on-disk CSVs byte-for-byte and `summary.json` field-for-field apart from its timestamp; the two-run check is also encoded in `test_two_full_runs_write_byte_identical_csvs` and passes |
| Notebook | `notebooks/bdb27_turn_importance.ipynb`, Run All on this host, no outputs committed |
| Tests | `tests/test_turn_importance.py` — 20 tests, 19 run by default (the two-full-run CSV comparison needs `BDB27_DETERMINISM=1`) |

`summary.json` records the full input hashes, counts, git HEAD and the decision labels. In
`test2_estimates.csv` and `test2_gradient.csv`, `n_clusters` is a JSON object keyed by cluster
variable. Two artefacts are outside the spec §4 list and are kept as extras:
`test1_rank_table.csv` (the rank table in machine-readable form) and `test2_position_mix.csv`
(the position diagnostic that shows the no-turn group is not a position artefact).

| Band | CB | SLOT_CB | SAFETY | POSITION_OTHER |
|---|---:|---:|---:|---:|
| No turn | 45.9% | 15.3% | 26.3% | 12.5% |
| 30–60° | 42.7% | 16.4% | 30.2% | 10.8% |
| 60–90° | 44.7% | 15.0% | 31.0% | 9.3% |
| 90–120° | 41.4% | 15.9% | 33.1% | 9.6% |
| 120–150° | 34.5% | 18.3% | 37.3% | 9.9% |
| 150–180° | 31.0% | 19.8% | 38.0% | 11.3% |
| >180° | 33.4% | 20.1% | 33.7% | 12.8% |

Position mix is broadly similar across the six bands; the sharp bands are noticeably
safety-heavy and corner-light, so `C(pos_group)` matters most for the deepest plants. The
30–60° group differs from no-turn rows by 3 pp of CB and 4 pp of safety — small enough that
position is a control, not the explanation.

---

## Review and validation

**Construction review.** The findings below were raised and fixed before this commit. They are
this cycle's own review pass — the author's, not an independent one; the independent check is the
closeout pass further down. Each finding is verifiable in the tree as it now stands:

1. **MAJOR — path-radius Spearman used all rows, not the usable circle fits the spec fixes.**
   Fixed: the mask now applies `circle_fit_usable`. ρ moves **−0.647 → −0.5003 (n = 48,929)**,
   and `test_spearman_uses_the_same_denominator_as_the_radius_table` pins the denominator. The
   rank table always used the correct subset, so no conclusion changes.
2. **MAJOR — the spec-mandated stacked covariance test was missing.** Added
   `test_two_way_cov_matches_stacked_one_way_statsmodels`, which compares the CGM estimate with
   `Va + Vb − Vab` built from statsmodels' own one-way cluster covariances. A dropped `−Vab` term
   now fails the suite.
3. **MINOR — a band absent from an input would raise a `KeyError`.** Both the primary labels and
   the gradient now report `NOT ESTIMABLE (30-60 band absent from input)` and skip the contrast.
4. **MINOR — `n_turns_in_band` in `test2_band_counts.csv` was the row count by construction.**
   Column removed; the turn-level counts live in Test 1.
5. **MINOR — `summary.json` and `run_log.txt` came from different executions.** One clean run now
   writes every artefact under a single timestamp.
6. **MINOR — the gradient table lacked game and cluster counts.** Added (`854` games;
   `115 / 854 / 2,179` clusters), and the JSON `n_clusters` format is documented above.
7. **MINOR — determinism was checked in memory, not on written CSVs.** Added
   `test_two_full_runs_write_byte_identical_csvs`, gated behind `BDB27_DETERMINISM=1` because it
   runs the pipeline twice; the two-run comparison is also in the provenance table.
8. **MINOR — "Sharper is not worse" read as an affirmative claim.** Reworded to "no sharper band
   is detectably worse", with the 150–180° p-value (0.004, survives Bonferroni over five
   comparisons) stated.
9. **MINOR — the note was not commit-ready.** This section plus the commit close it.
10. **NIT — CSV count and two undocumented extras.** Corrected to 10, and
    `test1_rank_table.csv` / `test2_position_mix.csv` are now named.
11. **NIT — materiality SD was quoted from the wrong sample.** Correction recorded in the spec
    addendum (0.10 = 6% of the primary-sample SD 1.653, not 7% of 1.45); the floor is unchanged.
12. **NIT — a missing `yards_gained` would read as "not explosive".** Now propagates as missing.
13. **NIT — the spec's coverage sentence contradicted itself.** Correction recorded in the
    addendum; the code follows the pre-specified rule (PREVENT kept as its own level).
14. **NIT — internal process voice in a delivered note.** Removed, including the placeholder
    sensitivity row.

**Mechanical validation — closeout pass, 2026-11-25.** A separate closeout session re-checked the
work against the spec. It did not re-derive the analysis; where it recomputed anything it is named
below.

*Pre-declaration held.* `git show def977f:notes/turn-importance-spec.md` already carries §2.4 (the
five-measure labelling rule) and §3.8 (STAKES CONFIRMED at β > 0 with a CI excluding 0 and
β ≥ 0.10 EPA; 3 pp for pass success). The only working-tree change to the spec is the appended
addendum, and its three parts are corrections of prose about *pre-computation expectations* — the
§3.1 sample-size arithmetic, the §3.8 SD the floor was expressed against, and a §3.5 sentence that
contradicted the next one. No estimand, exposure, reference group, control, estimator, sensitivity
set, decision label or materiality floor is changed. Nothing else in the spec moved.

*Code against spec.* Unit is the DB player-play (each cohort DB's own `player_play` row, joined on
`(game_id, play_id, nfl_id)`, restricted to the 858 games that carry turns); exposure is that DB's
`max_abs_turn_angle` on the play, so a row falls in exactly one band and never in two; the
reference is DB player-plays with no turn ≥ 30°; the primary sample is pass plays
(`pass_result` ∈ C/I/IN/S) with penalty-nullified plays excluded; controls are player fixed
effects, down, `yards_to_go`, coverage, position and season; SEs are two-way clustered by player
and game (Cameron–Gelman–Miller), with the three one-way alternatives reported. `PREVENT` is kept
as its own coverage level; null, `'NA'` and the empty string pool to `COVERAGE_MISSING`.
`separation_at_pass_forward` appears nowhere in the module or in any notebook code cell (checked by
reading and by the static AST test); there is no receiver matching, attribution, 3-yd proximity
rule or use of the attributed turn sets. The module raises only on a missing or unreadable file, or
a missing required column, and prints a path-and-column inventory before it does; there is no
minimum-count constant and no branch that stops on a small n, a thin band or a null result — a band
absent from an input is reported as `NOT ESTIMABLE`, not raised.

*Numbers against artefacts.* Every headline in this note was re-read from `outputs/turn_importance/`:
the band counts and shares (7,777 / 58,664 = 13.2569%), the medians and rank table, the Spearman
ρ values with their own denominators, the 30–45 / 45–60 split (2,225 / 5,552), the 2,927 exhausted
regain windows, the 84,225 / 62,503 / 37,805 exposure counts, the 30–60° 3,540 (4.20%) player-play
count, the primary coefficients and both CIs, all ten sensitivity rows, the six-band gradient, the
five within-turn differences and their cluster counts. The `6.01×` share shortfall is quoted from
`TASK.md` (`26` Combine turns, 2.21% of Combine turns, against `7,777` / 13.26% of game turns), not
re-derived here. Every figure traced; no denominator is missing.

*Suite.* `pytest tests/` in the repository `.venv`: **779 collected, 777 passed, 2 skipped, 0
failed, 0 errors**, 19 min 55 s. One skip is `test_turn_context.py`'s local-parquet check (the
artefact is gitignored); the other is the determinism test below. Run separately with
`BDB27_DETERMINISM=1`, that test **passes** (79 s, two full pipeline runs compared byte-for-byte).

*Determinism, measured.* A fresh run of the current code reproduced all ten on-disk CSVs
byte-identically and `summary.json` field-for-field except its timestamp. (Note: running the same
code under the other local virtualenv, numpy 2.5.3 rather than 2.5.1, moves the last three digits
of some coefficients — ≤ 4×10⁻¹², far below every printed figure. The artefacts of record were
produced in the repository `.venv` and reproduce exactly there.)

*Untouched.* `git status` shows only the six intended paths. `TASK.md`, `notes/report-draft.md`,
the band table, the four figures and their CSVs carry no diff.

**Two corrections made in this pass, both disclosed rather than silent.**

1. **A data claim that did not reproduce.** The note, the notebook's closing cell and the D223 entry
in `notes/decisions.md` all said "exactly one turn in the entire file sits below 30° and it is
invalid".
That is wrong: the FULL turn table contains **no row below 30°** (queried directly; minimum
`abs_turn_angle` in the sample table is 30.02°). The single invalid row
(`game_id 2023123103`, `play_id 2174`) has an angle of 38.0° and is invalid on the speed/acceleration
caps, not on angle. The text now says what is true — the floor is applied upstream, so the band is
censored at its own floor — which is the same point, more strongly. No number in any table changes.
The committed spec's §2.1 still carries the original sentence; the pre-declaration is left as
committed and is not rewritten.
2. **A stale test count.** The D223 entry said 17 tests; the file now holds 20 (19 by default plus
the gated two-run check). Corrected to match the file.

**Residual limitations.**

- **The comparison group is selected.** No-turn DB player-plays are quick throws, routes without a
  break and deep safeties away from the ball. Player fixed effects and the position control narrow
  the gap; they do not close it. The estimate is stakes, not the effect of turning.
- **Coverage encoding is a judgement.** §3.5 as committed contains two sentences that disagree; the
  code follows the second (`PREVENT` kept, null / `'NA'` / `''` pooled). The addendum records this
  reading. It was not re-estimated under the other reading, and the note does not claim robustness
  to it. PREVENT is 594 rows of the 46,954-row primary sample; the committed spec's parenthetical
  `n = 194` does not reproduce and is left as committed.
- **YAC is thin and selected** (4,937 of 46,954 rows), reported descriptively only.
- **v²/r is not physical for the reversal classes**; the severity rank rests on peak turn rate, a
  direct angular measurement.
- **The band share is the fragile number**, moving 8.6%–16.9% under the two smoothers; the rank
  conclusion is stable across all three assignments.
- **Title wording.** "The common one" is frequency language: 13.26% of game turns and the 6.01×
  share shortfall in `TASK.md`. It is not a claim that the band is the modal band (60–90° and
  90–120° are), which §5 forbids.
- **Process.** The working tree was still being written while this pass began (module, tests, spec,
  decisions and note all changed between 15:23 and 15:33, and the artefacts were regenerated at
  15:31–15:32). This review is of that final state: code mtime 15:23, artefacts 15:31–15:32, and the
  fresh-run comparison above confirms the artefacts and the code on disk agree.
