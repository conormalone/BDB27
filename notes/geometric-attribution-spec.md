# Geometric attribution — specification: the misattribution rate, band coverage, and the re-run estimator

**Written 2026-11-14, before any coefficient is computed on a geometrically matched set.** This is
the pre-registered specification for the full-tracking notebook: the estimand, the match rule, the
sample, the estimator, the gate and the decision rule, committed to git *before* the notebook is
run on Conor's laptop. If a result differs from the expectation, the expectation is not edited
afterwards.

Authorising words: Conor, 2026-11-14 15:37 — *"It's the only way, go for it"* (geometric proximity
matching), and 21:45 — the request for the notebook and for a statement of what it identifies.

Supersedes nothing. `notes/outcome-test-spec.md` (the D199 estimator) is reused **unchanged**;
this file adds the attribution measurement in front of it. `TASK.md`, the band table, the four
figures and `notes/report-draft.md` are untouched by this cycle.

---

## 0. Why this cycle exists

D199 estimated the break-to-separation link on 1,731 turns where the play carried **exactly one**
visible opposing route-runner. That is attribution by elimination. It is a weaker rule than the
frozen 3-yd proximity rule (D197): the single visible opponent need not be the man the DB covered.
A turn attached to the wrong receiver is classical measurement error in the exposure — his
retention is unrelated to that receiver's separation by construction — and classical measurement
error **attenuates the coefficient toward zero**. The D199 estimate was null (+0.05 yd per unit
retention, 95% CI −0.36 to +0.46) and, because attribution was unverified, **uninformative**:
"no effect" and "real effect diluted by wrong-receiver attribution" both survive it.

Full game tracking is on Conor's laptop. This cycle ships the notebook that measures the
misattribution directly and re-runs the same estimator on the geometrically matched set.

## 1. What the notebook must identify, in priority order

1. **The misattribution rate of the elimination rule** — on turns where both the elimination
   candidate and full tracking are available, how often the geometric match names the same player.
   This is the sharpest single output and is reported even if everything downstream fails (§6).
2. **Coverage of the band** by the geometric rule at 2, 3 and 5 yd — unique / ambiguous / none, as
   counts and shares of the band, with the attributed fraction carried beside every downstream
   number (§7).
3. **The de-attenuated estimate** — the D199 estimator, unchanged, on the geometric unique-match
   set, reported side by side with the D199 elimination coefficient so the change attributable to
   attribution is visible (§8).
4. **The MAN-only slice** under geometric matching, as the strongest-attribution sensitivity (§9).

## 2. Inputs and provenance

| Input | Role | Stamp |
|---|---|---|
| `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` (or the path Conor sets) | band turns, break-frame centre coordinates `(x_centre, y_centre)`, exposure `apex_retention` | `[FULL]` |
| `data/raw/player_play.csv` | team labels, `route_ran`, `coverage_assignment`, `separation_at_pass_forward` | `[FULL]` |
| full game tracking (`game_tracking_2023.csv`, `_2024`, `_2025`, or `game_tracking.csv`) | `(x, y)` per player per frame, and `event` for the pass-forward frame | `[FULL]` |

Every input is hashed (sha256) into `outputs/geometric_attribution/manifest.json` before it is
read, and every output after it is written. The manifest also records the notebook's git HEAD, the
config cell verbatim, and a UTC timestamp. No number in the notebook is typed in: every count and
coefficient is printed from an artefact the run wrote.

**What is measurable on this machine, stated before the spec was fixed.** Only the 1,048,575-row
tracking truncation exists here (333 games, 12,638 of 31,720 plays, 154 of 497 players, never more
than four players on a play). Of the 1,731 elimination-attributed turns, 126 lie on a tracked play
and 58 have the turning DB tracked; on those 58, the break frame is recoverable within the 0.25-yd
gate on 58 (measured 2026-11-14, before this file was written — a data-availability count, not an
estimate). Three of the 58 are `unique` at 3 yd, 17 `none_observed`, 38 `unobservable`, none
`multiple`. **The misattribution rate and the coefficient are therefore not measurable here**; they
are measured on Conor's laptop. The sample run exists only to prove the code path end to end and to
supply the stop condition of §12.

## 3. Pre-flight — is the tracking file the full one?

This is a check on **game tracking**, not on `player_play`. It must not repeat the D198 error:
`player_play` has 2.7 rows per play **by design** (it holds only the 497-prospect cohort), and a
low rows-per-play there says nothing about data availability. Tracking is the opposite case: the
full file holds every player on the field, so a tracking file with four players per play is a
truncation, not a cohort.

For each tracking input the notebook prints, and tests against these stop conditions:

| Check | Full-file expectation | **Stop** message if violated |
|---|---|---|
| exact row count | not `1,048,575` (2²⁰ − 1) | `STOP — this is the 1,048,575-row truncation` |
| distinct `nfl_id` | ≥ 300 | `STOP — truncation: fewer than 300 players tracked` |
| median distinct players per play | ≥ 15 | `STOP — truncation: median players per play below 15` |
| plays tracked vs band plays | ≥ 90% of the band's plays present | `STOP — tracking covers less than 90% of the band` |

The stop message names the file, the measured value and the threshold. **No sample file is ever
substituted.** A stop ends the notebook before extraction; nothing is written except the pre-flight
record.

## 4. Break frame and the candidate rule — frozen (D197, unchanged)

- **Break frame.** For each band turn, take the turning DB's own rows on that play and select the
  row whose `(x, y)` is nearest to the turn table's `(x_centre, y_centre)`; require the distance to
  be **≤ 0.25 yd**. The timestamp of that row is the break timestamp. A turn whose nearest row
  exceeds 0.25 yd is `break_frame_unrecovered` and is dropped, counted and named by turn key.
- **Frame slice.** All tracked players on the play with `|time − break_timestamp| ≤ 0.05 s` (half a
  frame at 10 Hz). No interpolation is performed.
- **Team.** From `player_play.team_abbr` on the same `(game_id, play_id, nfl_id)`. A player in the
  tracking frame with no `player_play` row, or with no team label, is **not a candidate** and is
  counted as `untagged` — the rule never guesses a side.
- **Candidate (primary).** An opposing-side player (`team_abbr <> ` the DB's own team) with a
  non-`NA` `route_ran`.
- **Distance.** Euclidean in the tracking frame, `sqrt(dx² + dy²)`, yards.
- **Decision.** Exactly one candidate within **3 yd** → `unique` (attributed). Two or more →
  `multiple` (ambiguous, dropped). None inside the radius → `none` (dropped). **No tie is ever
  broken by radius choice, distance order or row order.** Radii 2 and 5 yd are reported as
  sensitivity; 3 yd decides.
- **Fallback candidate set.** Any opposing player in the frame, regardless of `route_ran`, is
  computed and reported as a sensitivity only. It never enters the primary.

Per turn the notebook records: `match_status`, `n_candidates_in_frame`, `n_candidates_in_radius`,
`min_distance_yd`, `second_distance_yd`, `nearest_gap_yd`, `matched_receiver_nfl_id`,
`is_geometric_match = True`, and the rule string.

**This match is an INFERENCE.** The dataset records no opponent identifier and no assignment. A
receiver within 3 yd at the break is *plausibly* the man covered; rub routes, picks, zone-match and
a DB closing on a different receiver all break that. Every artefact, table and sentence carries the
label.

## 5. Matching the elimination set, for the misattribution rate

The elimination candidate set is rebuilt from raw `player_play.csv` by
`outcome_test.build_sample` — the same code path as D199, so the 1,731 turns and the receiver ids
are identical to the committed `outputs/outcome_test/attributed_turns.csv`.

For each of those turns, the geometric status and matched id are attached. The comparison is:

| Case | Label |
|---|---|
| geometric `unique`, same `nfl_id` as the elimination candidate | `agree` |
| geometric `unique`, different `nfl_id` | `disagree` — **the elimination rule misattributed this turn** |
| geometric `multiple` | `ambiguous` — geometry cannot decide; excluded from the rate |
| geometric `none` | `no_receiver_in_radius` — excluded from the rate |
| `unobservable` / `untracked` / `break_frame_unrecovered` | `unmeasurable` — excluded, counted |

**Pre-stated rate:**

```
misattribution_rate = disagree / (agree + disagree)
```

over turns where the geometric rule yields a unique receiver. The denominator is printed. Below
**50 judgeable turns** the notebook prints `TOO FEW JUDGEABLE TURNS FOR A RATE` and reports the
counts only. Two companion shares are always printed beside it: `ambiguous` and `none` as shares of
the elimination set, because a rate over unique-geometry turns conditions on geometry being able to
decide, and that conditioning must be visible.

## 6. What the rate does and does not license

- It measures how often elimination names a *different player* from the geometric nearest
  route-runner within 3 yd. It **does not** prove the geometric match is correct — only that the
  two rules disagree, which is a lower bound on elimination's error if the geometric rule is
  better, and no bound at all if it is worse.
- It is computed only on turns where the elimination candidate, the DB and the opposing players are
  all tracked. Those are not a random subset; the share is printed.
- It is the number that says whether attenuation is a live explanation for the D199 null. It is
  **not** itself an estimate of the attenuation factor, which would need a model of the error.

## 7. Coverage of the band

Over all band turns in the tracking-covered plays, at 2, 3 and 5 yd: `unique`, `multiple`, `none`,
`unobservable` (no opposing player tracked in the frame at all), `untracked` (play absent from
tracking), `break_frame_unrecovered`. Shares are of the band (7,777). The attributed fraction is
carried beside every downstream number:

```
attributed_fraction = unique_at_3yd / band_turns
```

The candidate ceiling from `player_play` (3,009 turns with ≥1 opposing route-runner, 38.7%) is
printed beside the geometric counts, so the reader can see how much geometry removes.

## 8. The estimator — D199's, unchanged

Sample: band turns with `match_status == unique` at 3 yd, whose matched receiver has a
`separation_at_pass_forward` value. Ambiguous, none, unobservable and untracked turns are dropped,
never imputed.

| Role | Variable | Handling |
|---|---|---|
| Outcome `y` | `separation_at_pass_forward` (yd) on the **matched receiver's** row | as recorded |
| Exposure `x` | `apex_retention` | as recorded |
| Controls | `entry_speed` (linear + square), `position` FE, `season` FE, `coverage_family` FE (MAN / ZONE / MISSING) | unchanged from D199 |

Estimator: OLS, standard errors clustered by player (CR1, G = number of distinct players). Also
reported: player fixed effects; the nine D199 sensitivities, each computed on the geometric set
with the same definitions; and the wild cluster bootstrap (999 Rademacher replications, seed
20261114). The implementation is `outcome_test.fit` / `run_all` / `verdict`, called with the
geometric frame — **the same functions, not a re-implementation**, so a difference between the two
coefficients cannot come from the estimator.

Reported side by side: the D199 elimination coefficient (read from
`outputs/outcome_test/estimates.csv`) and the geometric coefficient, with n, players, clusters and
the attributed fraction for each.

## 9. MAN-only

Same estimator on `coverage_assignment == 'MAN'`. It is the strongest prior for one-to-one coverage
and is reported whatever its size; below 40 turns the notebook refuses to fit it and says so
(the D199 rule).

## 10. Gate and labelling — stated before the result

The frozen gate (D197) is copied unchanged: **≥ 500 attributed turns after matching**, and
**≥ 30% of band turns unique at 3 yd**. It is applied as a *labelling* rule, not a suppression rule:

| Condition | Label on the estimate | What may be written |
|---|---|---|
| both gate arms met | `GATE MET` | the estimate, with the decision rule of §11 applied |
| either arm fails | `DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE` | the number and interval, described as descriptive; **no finding, no claim either way** |

The number is always reported. What changes at the gate is what it is allowed to be called.

## 11. Decision rule — copied from D199 §6, unchanged

A **finding** requires all five:

- **(a)** primary β₁ < 0 — more retention, less separation;
- **(b)** the 95% player-clustered interval excludes zero;
- **(c)** the player-FE specification has the same sign with an interval excluding zero;
- **(d)** the sign holds in MAN-only and one-turn-per-play;
- **(e)** |β₁| ≥ 2.0 yd per unit retention (0.10 more retention ≈ 0.20 yd less separation).

| Result | Label |
|---|---|
| (a)–(e) all hold, and the gate is met | **finding** |
| (a)–(d) hold, (e) fails | **detectable but too small to be a finding** |
| (b) or (c) fails | **estimate only, no claim** |
| β₁ > 0 | **estimate only, opposite to the stated expectation** |
| gate not met | **descriptive only — below pre-declared gate** |

A null is never upgraded, and a number is never withheld because it is inconvenient. The notebook's
final cell prints exactly one of these labels plus a plain-English sentence, and refuses to print a
claim the label does not license.

## 12. Legitimate stop conditions

Each stops the estimator, prints the counts and names what it found; none is a failure to be worked
around.

| Condition | What is printed |
|---|---|
| pre-flight: a tracking file is a truncation (§3) | the file, the measured value, the threshold |
| `unobservable` + `untracked` > 50% of band turns | `TRACKING DOES NOT OBSERVE OPPONENTS ON MOST PLAYS` with the shares — do not fit |
| `unique` @3yd = 0 | no match at any radius; the coverage table only |
| judgeable turns < 50 | the misattribution counts, no rate |
| matched-receiver separation recorded < 200 | coverage and misattribution only; no fit |

## 13. Determinism, resumability and the manifest

- **Deterministic.** Sorted iteration everywhere; the only randomness is the seeded bootstrap. Two
  runs on the same inputs produce byte-identical CSVs (tested).
- **Chunked and resumable.** Tracking is streamed in chunks of 2,000,000 rows; the rows for the
  band's plays are written to a per-input-file Parquet shard under
  `outputs/geometric_attribution/shards/`. A shard is reused when its recorded `(path, size,
  mtime, sha256-of-first-64KB, row_count)` matches, so a run that dies resumes at the next file
  rather than from zero. A progress line per chunk gives rows read, rows kept, elapsed and ETA.
- **Manifest.** `outputs/geometric_attribution/manifest.json` carries sha256 for every input and
  every output, the row counts, the git HEAD, the config cell and the rule strings. A run that
  finds a stale manifest rewrites it; it never appends silently.

## 14. Limits that ship beside the numbers

1. The geometric match is an **inference**, not an observation (§4).
2. Prospect-versus-prospect selection persists: the matched receiver needs a `player_play` row for
   the outcome, so the estimate's sample is the 497-player cohort on both sides.
3. Zone turns remain in the primary; MAN-only is the sensitivity.
4. 102-ish players is the independent unit; precision is player-clustered.
5. `separation_at_pass_forward` is measured after the route continues; the break-to-pass-forward
   interval is uncontrolled.
6. No causal reading: the break is not randomised.
7. The persistence check at pass-forward is a **diagnostic**, not a sample filter in the primary —
   the pre-registered estimator has no persistence step, and adding one would change the estimand.
   Where a `pass_forward` event exists, the notebook reports the nearest-defender rank of the
   matched DB and a persistence-restricted sensitivity; where it does not, it prints
   `PERSISTENCE NOT EVALUABLE` and the primary is unchanged.
8. Only one outcome: `separation_at_pass_forward`.

## 15. What ships

`src/bdb27/geometric_match.py` (pre-flight, extraction, match, comparison), `scripts/geometric_attribution.py`
(the same pipeline headless, used to prove the path on the sample), `notebooks/bdb27_geometric_attribution.ipynb`
(Run All, no CLI), `tests/test_geometric_match.py`, `notes/geometric-attribution-result.md`, and the
sample-run artefacts under `outputs/geometric_attribution_sample/`. No new figure. `TASK.md`, the
band table, the figures and `notes/report-draft.md` are untouched. A report section, if the laptop
run warrants one, is **proposed** to Conor, never inserted.
