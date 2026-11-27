# Turn importance — specification: what a 30–60° DB turn costs, and what it is worth

**Written 2026-11-25, before Test 1 or Test 2 was computed.** Committed to git before the first
band median and before the first outcome coefficient. This is the pre-specification in the only
sense this project uses one: the estimand, sample, controls, estimator, reporting rules and
decision labels are fixed here, and are not edited after the result is seen. If the result differs
from the expectation, the expectation is not rewritten.

Authorising words: Conor, 2026-11-25 — *"Just to clarify, I don't care about separation, I want to
check that 30 to 60 turns are important"* and *"Ok run test 1 and 2"*.

`BDB27/TASK.md` is unchanged by this cycle and remains binding on the project. This is a separate
analysis explicitly authorised by Conor. It does **not** change the band table, the figure set,
`notes/report-draft.md` or any existing note. It proposes nothing for the writeup; if a result
warrants a section, that is proposed to Conor, not inserted.

Decision number: **D223**.

---

## 0. Scope and the constraint that fixes the design

Two questions, asked separately, reported separately:

- **Test 1 — physical cost.** Is a 30–60° turn a demanding movement or a gentle one, and where
  does the band sit on the cost curve across all DB game turns?
- **Test 2 — play-level stakes.** Are plays in which a DB has to turn 30–60° worse for the defence
  than plays in which he does not turn, and how does that compare with sharper turns?

**Not a separation test.** `separation_at_pass_forward` is not an outcome, not an exposure, not a
control, and not read anywhere in this cycle. There is no receiver matching, no attribution
inference, no proximity rule and no use of the 1,731-turn or 2,558-turn attributed sets. Every
Test 2 row is a DB's **own** `player_play` row, joined on `(game_id, play_id, nfl_id)`.

**DB only.** `setting == 'game'`, `position_group == 'DB'`, `valid == TRUE`. The cohort is the 115
DBs in the FULL turn table. No other position is drawn as background.

**Test 2 is not causal.** A turn exists because the route broke, the receiver threatened a
break, or the DB had to close. Turn occurrence is post-snap and endogenous to the play. Test 2
therefore measures **what is at stake when a turn happens** (an association conditional on
controls), never the effect of turning. Every Test 2 number carries the label
`ASSOCIATION (STAKES), NOT CAUSE`. A positive coefficient is not "turning causes worse outcomes";
a null is not "turning does not matter".

---

## 1. Inputs and provenance

| Input | Path | Role |
|---|---|---|
| DB game turns, FULL | `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` | Test 1 population; Test 2 exposure |
| Sample turns (declared, unused) | `outputs/turns/turns.parquet` | provenance only — 2023 sample, not pooled |
| Player-play | `data/interim/parquet/player_play.parquet` | Test 2 outcomes and controls |
| Games | `data/interim/parquet/games.parquet` | season for Test 2 |

Test 1 and Test 2 run on the **FULL** DB turn table: 58,664 valid turns from 115 players, 858
games, seasons 2023–2025. (The file holds 58,665 rows; one fails `valid` and is excluded. Both
counts are printed.)

`player_play` carries rows only for the draft-prospect cohort: 314,197 rows, 114,954 plays, 1,001
games, 2.73 rows per play. It is **not** a full 22-player table. A play has a DB row only when one
of the 115 cohort DBs is on it. That is a property of the file, not a filter, and every Test 2
count prints its denominator so the reader can see it.

The `player_play` fields used here are copied onto every row of a play and are checked unique per
play before use (`expected_points_added`, `pass_result`, `yards_gained`,
`play_nullified_by_penalty`, `down`, `yards_to_go`, `team_coverage_type`, `season`). `"NA"` is a
string in this file (D186); every string control is read with `NULLIF(x, 'NA')` and every numeric
`TRY_CAST`.

No laptop run. No file outside the three named paths is read. A missing required file names every
path tried and raises; nothing else raises (see §7).

---

## 2. Test 1 — the physical cost curve

### 2.1 Unit and population

One row per valid DB game turn (n = 58,664). Band is `abs_turn_angle` in degrees, using the
project's existing bins, which reproduce `bdb27_full_game_bands.csv` exactly:

`[30,60)`, `[60,90)`, `[90,120)`, `[120,150)`, `[150,180)`, `[180,∞)`.

Angles are censored at 30° by the detector. There is exactly one turn below 30° in the raw file
and it is invalid; the 30–60 band is therefore censored at its own floor and its count is a lower
bound (TASK honesty limit 2).

### 2.2 Measures

Reported per band, each with its own non-null count and denominator:

| Measure | Column | Direction of "more costly" |
|---|---|---|
| Entry speed (yd/s) | `entry_speed` | context, not cost |
| Apex speed (yd/s) | `apex_speed` | context |
| Exit speed (yd/s) | `exit_speed` | context |
| Speed retention (exit ÷ entry) | `speed_retention` | lower = costlier |
| Apex retention (apex ÷ entry) | `apex_retention` | lower = costlier |
| Peak turn rate (deg/s) | `peak_omega_deg_s` | higher = costlier |
| Peak lateral acceleration (yd/s²) | `peak_lateral_accel_yd_s2` | higher = costlier |
| Path radius (yd) | `path_radius_yd` | lower = costlier; only where `circle_fit_usable` |
| Time to regain entry speed (s) | `regain_entry_speed_s` | higher = costlier; only where not `regain_exhausted` |
| Turn duration (s) | `duration_s` | context |
| Plant share | `plant` | context (movement family) |

For each band and measure: **n**, share of all valid turns, **n non-null**, median, q25, q75, mean,
sd. `regain_entry_speed_s` prints both the 38,553 turns that regained and the 20,111 that exhausted
the window (34.3%). `path_radius_yd` prints both the 48,929 usable circle fits and the 9,735
flagged unusable.

### 2.3 Estimator

Descriptive only. No model is fitted, nothing is extrapolated. For each cost measure, Spearman's
ρ against `abs_turn_angle` with its n and p, as a monotonicity summary; and a rank table placing
each band 1–6 on each of the five cost measures (1 = gentlest).

Secondary, pre-declared: the 30–60 band split into `[30,45)` and `[45,60)`, because the proposed
drill sits at 45° and is a boundary sample. Same measures, same denominators.

### 2.4 Pre-declared labelling rule

Five cost measures: peak turn rate, peak lateral acceleration, speed retention, apex retention,
path radius. The 30–60 band is labelled:

- **physically mild** if it ranks ≤ 2 on at least 4 of the 5 measures;
- **physically demanding** if it ranks ≥ 5 on at least 4 of the 5;
- **intermediate** otherwise.

This is a labelling convention applied to whatever the table shows. It is not a gate and it cannot
stop the notebook; all rows print regardless of the label.

### 2.5 Smoother sensitivity

The smoother moves the measured angle by about 6° median (TASK honesty limit 1). Band assignment
is therefore recomputed with `abs_turn_angle_smoother_min` and `abs_turn_angle_smoother_max`, and
the 30–60 share plus the band's median peak lateral acceleration and peak turn rate are reported
under all three assignments (as recorded, min, max).

---

## 3. Test 2 — play-level stakes

### 3.1 Unit

**DB player-play.** One row per `(game_id, play_id, nfl_id)` for the 115 cohort DBs, in the 858
games that carry turns. Exposure comes from that DB's own turns on that play; outcome and controls
come from his own `player_play` row. No other player's row is used, and no play outcome is
attributed across players.

Expected size: 84,225 DB player-play rows over 62,503 plays. 58,664 of them carry at least one
turn; 25,561 carry none (30.3%). Both counts print.

### 3.2 Exposure

`max_abs_turn_angle` = the sharpest turn that DB had on that play (null = no turn ≥ 30°). The
player-play is assigned to the band of that angle. This partitions the sample exactly once and
makes "30–60 vs sharper" a like-for-like contrast.

Secondary exposure, pre-declared: one indicator per band for "had at least one turn in this band"
(a player-play can enter more than one band). Reported as a robustness check, never substituted
for the partition.

### 3.3 Outcomes

All outcomes are play-level and are read from the DB's own row (they are copied per play and
verified unique per play). **Positive = worse for the defence.**

| Outcome | Definition | Sample |
|---|---|---|
| `epa_allowed` | `expected_points_added` (offence EPA) | primary sample |
| `pass_success_allowed` | 1 if `pass_result == 'C'`, else 0 | pass plays |
| `yards_allowed` | `yards_gained` | primary sample |
| `explosive_allowed` | 1 if `yards_gained >= 15`, else 0 | primary sample |
| `yac_allowed` | `yards_after_catch` (play-level, from the target's row) | completions where observed |

The **primary sample** is pass plays: `pass_result IN ('C','I','IN','S')` — a dropback that ended
in a completion, incompletion, interception or sack. Designed runs and quarterback scrambles
(`pass_result = 'R'`) and plays with no `pass_result` are excluded from the primary because a
coverage turn is a pass-play phenomenon and run EPA would otherwise dominate the contrast. The
all-plays version of `epa_allowed` and `yards_allowed` is reported as a pre-declared secondary.

**YAC is thin and is labelled as such.** `yards_after_catch` is recorded only on the targeted
receiver's row, and `target` is populated only for cohort players: 6,256 rows in 314,197 carry a
YAC value, covering 6,256 of the 39,135 completions (16.0%). The play-level YAC outcome is
therefore estimated on that observed subset with its denominator printed, and is secondary.

### 3.4 Primary exclusions

One exclusion, pre-declared: `play_nullified_by_penalty == 'Y'` (4,066 DB player-play rows, 4.8%).
A nullified play has no football outcome; its yards are zeroed and its EPA is an artefact of the
penalty. A sensitivity including them is reported.

No other row is dropped. Missing controls become explicit levels (`COVERAGE_MISSING`,
`POSITION_OTHER`) rather than deletions, so every count is visible.

### 3.5 Controls

| Role | Variable | Handling |
|---|---|---|
| Down | `down` (0–4) | categorical |
| Distance | `yards_to_go` | linear |
| Coverage | `team_coverage_type` | categorical; null / `'NA'` / `PREVENT` pooled as `COVERAGE_MISSING` only when null |
| Position | `lined_up_position` | CB / SLOT_CB / SAFETY (HIGH_SAFETY, BOX_SAFETY) / OTHER (OLB, EDGE, MLB, INTERIOR_LINE, null) |
| Season | `season` (2023–2025) | categorical |
| Player | `nfl_id` | fixed effects |

`PREVENT` is kept as its own level (n = 194 rows) rather than pooled. No post-snap variable
(route, target, separation, cushion, pressure, time to throw) enters as a control.

### 3.6 Estimator

OLS for `epa_allowed`, `yards_allowed`, `yac_allowed`; linear probability model for
`pass_success_allowed` and `explosive_allowed` (FE probit would be incidental-parameters
inconsistent; the LPM coefficient is reported as a percentage-point difference, not as a
probability).

Reference category: **no turn**. Coefficients for the five sharper bands are reported alongside,
so the gradient is visible rather than inferred.

Standard errors: **two-way clustered by player and game** (Cameron–Gelman–Miller:
`V = V_player + V_game − V_player×game`), finite-sample corrected. Pre-declared robustness:
game-clustered, player-clustered and play-clustered one-way. Clusters: 115 players, 858 games.
The two-way choice is stated because a player's plays share his ability and a game's plays share
officials, weather, opponent and score state.

### 3.7 Pre-declared sensitivity set

Each is reported whatever it shows; none replaces the primary.

1. **Within-turn sample** — drop the no-turn group, reference = 30–60 band. This holds "some DB
   turned on this play" roughly constant and is the cleanest read of the gradient.
2. All plays (drop the pass-play restriction) for `epa_allowed` and `yards_allowed`.
3. Include penalty-nullified plays.
4. Per-band "any turn" indicators instead of the max-angle partition.
5. One row per play — the DB with the sharpest turn on that play; play-level controls, no player
   FE (the literal "plays carrying a 30–60 turn" estimand).
6. EPA winsorised at the 1st/99th percentile.
7. Player-clustered and play-clustered SEs for the primary coefficient.
8. `epa_allowed` on completions only (removes sack/intentional-incompletion composition).

### 3.8 Pre-declared decision labels

For the 30–60 coefficient against no turn, on `epa_allowed` in the primary model:

- **STAKES CONFIRMED** — β > 0, 95% CI excludes 0, and β ≥ 0.10 EPA. (0.10 EPA is ~7% of the
  play-level standard deviation, 1.45, computed on this panel before any model was fitted.)
- **DIRECTIONAL ONLY** — β > 0 and the CI excludes 0, but β < 0.10 EPA.
- **NO DETECTABLE DIFFERENCE** — the 95% CI includes 0.
- **OPPOSITE SIGN** — β < 0 with a CI excluding 0.

For `pass_success_allowed` the materiality floor is **3 percentage points**. These labels describe
the estimate; none of them upgrades the association to a cause.

A **gradient** claim ("stakes rise with turn angle") requires the 120–150° or 150–180° contrast to
exceed the 30–60° contrast, with the difference's 95% CI (delta method on the joint covariance)
excluding 0. If it does not, the note says the bands are not distinguishable.

---

## 4. What is reported, and how

Every number printed by the notebook is also written to `outputs/turn_importance/`:

| File | Contents |
|---|---|
| `test1_cost_by_band.csv` | long table: measure, band, n, share, n_nonnull, median, q25, q75, mean, sd |
| `test1_bands_wide.csv` | one row per band, the headline measures |
| `test1_smoother_sensitivity.csv` | 30–60 share and medians under recorded/min/max angle |
| `test1_spearman.csv` | ρ vs angle per cost measure |
| `test2_band_counts.csv` | DB player-plays and plays by exposure band, with denominators |
| `test2_band_means.csv` | raw outcome means by band with n |
| `test2_estimates.csv` | every specification: label, outcome, sample, term, coef, se, ci_lo, ci_hi, p, n, clusters |
| `test2_gradient.csv` | band differences and their CIs |
| `summary.json` | provenance: input paths, row counts, file sha256, git HEAD, timestamp, labels |
| `run_log.txt` | the notebook's printed log |

Every table row carries: the estimate, its 95% CI, n, the number of players and games, and the
cluster count. Percentages always print the numerator and denominator. No number appears without
its provenance stamp (`FULL`) and its interpretation label.

---

## 5. What may and may not be claimed

**May be claimed:** the shape of the physical cost curve by band; the position of 30–60 on it;
the conditional association between a 30–60 turn and play outcomes, with its uncertainty; how
that association compares with sharper bands; that the comparison is stakes, not cause.

**May not be claimed:** that turning 30–60 causes anything; that a null means the band does not
matter; any per-player ranking or score; any separation, coverage-success, target or route
statement; any transfer to the Combine drill debate (Test 1 describes game turns, not Combine
turns); that 30–60 is "the most common" band (it is not — it is 13.3% and the modal bands are
60–90 and 90–120).

The frequency fact is reported as frequency, not as severity: 30–60 is 13.3% of valid DB game
turns (7,777 / 58,664) and is the mildest band the detector records. If Test 1 finds the band low
on the cost curve, the honest finding is that the Combine skips the **common mild turn** — an
argument from frequency and from the shape of the curve, not from severity at 30–60.

---

## 6. Proof requirements

- One code path: `src/bdb27/turn_importance.py`, imported by both the notebook and the tests.
  No analysis in a notebook cell that is not reproduced by a tested function.
- The notebook is the deliverable and runs top-to-bottom (`Run All`) on this host with the three
  named inputs; no CLI instructions to Conor, no network.
- Determinism: two consecutive runs produce byte-identical CSVs (checked in the test suite).
- Tests (`tests/test_turn_importance.py`): band boundaries (30.0 in, 60.0 out, 180.0 in the top
  band), max-angle aggregation with multiple turns, no-turn handling, the two-way covariance
  against a direct stacked-cluster computation, coefficient recovery on synthetic data, a
  no-gate test on a tiny synthetic frame, and a static test that `separation_at_pass_forward`
  never appears in the module or the notebook.
- Full suite must be green before commit.
- D223 appended to `notes/decisions.md`; commit **and push**.

---

## 7. Failure rule

The notebook may raise only if: a required input file is absent (it names every path tried and what
to set), a required file is unreadable, or a required column is missing (it prints the columns it
found). It never raises on a small n, a low share, a thin band, an empty outcome subset, a
singular-feeling FE, an unusual diagnostic or a null result. Every such case prints a table and a
"descriptive only" note. No minimum-count constant exists in the code.

---

## Addendum — 2026-11-25, after the first execution

One paragraph of §3.1 was an arithmetic estimate written before the panel existed, and it was
wrong: it said 58,664 of the 84,225 DB player-play rows carry at least one turn and 25,561 carry
none. That conflated **turn rows** with **player-play rows**. The measured counts are printed by
the notebook and are **46,420 rows with at least one turn** and **37,805 with no turn (44.9%)**;
the 30–60° max-angle band is **3,540 rows (4.2%)**, not 13.3% of player-plays.

This is a correction of a pre-computation sample-size expectation, not a result. No estimand,
control, estimator, sensitivity, decision label or materiality floor is changed. The corrected
counts are reported with their denominators in `notes/turn-importance-result.md` and in
`outputs/turn_importance/test2_band_counts.csv`.

Two further wording corrections, recorded for the same reason (the pre-specified rule stands, only
the prose describing it was wrong):

- **§3.8, materiality floor.** The floor is 0.10 EPA. The text justified it as "~7% of the
  play-level standard deviation, 1.45". 1.43 is the all-plays panel SD; the SD of `epa_allowed` in
  the **primary pass sample** — the quantity the coefficient lives on — is **1.653**, so 0.10 is
  ~6% of it. The floor itself is a fixed pre-declared number and is unchanged.
- **§3.5, coverage encoding.** The operative rule is the second sentence: `PREVENT` is kept as its
  own coverage level. The actual pooling is `null` **and** the literal string `'NA'` **and** the
  empty string → `COVERAGE_MISSING`. The first sentence of §3.5, read alone, said the opposite;
  the code follows the rule stated here, which was the pre-specified intent.
