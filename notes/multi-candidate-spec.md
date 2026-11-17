# Multi-candidate attribution — specification: what can be said about the 870, and what cannot

**Written 2026-11-17, before the first run of the new analysis. Committed alone, ahead of any
code, any coefficient and any count the analysis produces.** If a result disagrees with a sentence
below, the sentence is not edited afterwards; the disagreement is reported as a disagreement.

Authorising words: Conor, 2026-11-17 14:04 GMT — *"Ok well we're going to have to do our best to
assess the 2 or more on a play"*, said immediately after being told that the 870 band turns with two
or more opposing route-runners are dropped whole by the frozen match rule.

Supersedes nothing. `TASK.md`, the band table, the four figures, `notes/report-draft.md` and every
existing artefact under `outputs/` are untouched by this cycle.

---

## 0. The question, stated so that it has an answer

The frozen rule (`src/bdb27/outcome_link.py`, `MATCH_RULE`; D197) attaches a turn to **the single**
opposing route-runner within 3 yd at the break frame. Two or more candidates inside 3 yd → ambiguous,
dropped, no tie broken by radius, distance order or row order. On the FULL band that is **870 turns**
— 11.2% of 7,777, and 33.4% of the 2,601 turns that carry a separation value.

The question is not "which man was he covering". The dataset has **no opponent identifier** and no
assignment field, so on these turns that is not knowable from the file; a single name would be a
guess dressed as a measurement. The question is the one that has an answer:

> **How much can the choice among the visible candidates move the D199 estimate — and is that
> movement smaller or larger than the sampling error already reported?**

If the movement is small, the multi-candidate turns are not the reason D199 is null, and the null
stands on better ground than it did. If the movement is large, the null is **not identified** by
these data and the honest output is a bound, not a coefficient. Both are results. Neither is a
finding in the sense of `TASK.md`, and no label below upgrades to one.

## 1. What is already on the record (to be reproduced, not re-derived)

`[FULL]`, `outputs/outcome_link/feasibility_summary.json`, 2026-11-14:

| Quantity | Value |
|---|---:|
| band turns | 7,777 |
| no opposing `player_play` row | 1,462 (18.8%) |
| opposing rows, no route-runner among them | 3,306 (42.5%) |
| ≥ 1 opposing route-runner | 3,009 |
| with a separation value | 2,601 |
| exactly one candidate | 1,731 |
| **two or more candidates** | **870** |

`outputs/outcome_link/feasibility_ambiguity_candidates.csv` `[FULL]`, candidate-level: 815 turns with
two candidates, 185 with three, 5 with four, 1 with six (191 at three or more); 270 of the 815 carry
`target` on exactly one candidate; 704 of the 815 carry a separation for both.

D199 (`notes/outcome-test-result.md`): separation on `apex_retention`, controls `entry_speed`,
`entry_speed²`, position, season, coverage family, player-clustered — **β = +0.05 yd per unit
retention, 95% CI −0.36 to +0.46, n = 1,731, 102 clusters**.

D204 geometric run: 471 attributed, 6.1% of the band, `DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE`.
Those artefacts are **not on this machine**; every mention below carries that dagger and nothing here
depends on them.

**One correction carried forward.** `two_candidate_separation_gap_yd` is MAX(sep) − MIN(sep) of the
candidates' `separation_at_pass_forward` values. It is **not** the distance between the two men and
must never be described as one.

## 2. What this cycle is not allowed to become

- **It does not replace the frozen rule.** The frozen rule stays the primary attribution, its numbers
  are produced and printed by the new script on every run, and every new number is printed **beside**
  it. Where a table has a frozen column, the frozen column comes first.
- **It does not name a man.** No output asserts "the DB was covering candidate X". Every pairing is
  prefixed `INFERENCE`, in the artefact, in every table header and in every sentence.
- **It does not enter `TASK.md`.** `TASK.md` forbids outcome tests and per-prospect claims; this is a
  diagnostic on an estimator that `TASK.md` already excludes. Nothing here is written into the claim,
  the band table, the four figures or the report draft without Conor's word.
- **It does not add a gate.** No minimum n, no minimum attributable share, no abort. A small n prints
  a wide interval and a `DESCRIPTIVE ONLY` label. Only a missing or unreadable required file, or a
  missing required column, may raise — and it names every path it tried first.
- **It does not tune a threshold.** 3 yd, 30–60°, the control set and the clustering are inherited
  from D197/D199 unchanged. The new margins (§6) are *reported*, never used to select.

## 3. The three objects, and what licences each

### O1 — The ambiguity funnel, re-derived `[FULL]`

Every count from §1 recomputed from `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` and
`data/raw/player_play.csv`, each printed with its denominator. This is a reconciliation, not a result:
it exists so that the objects below are demonstrably about the same 870 turns everyone has been
quoting.

### O2 — The identified set: the measurement this cycle exists for

**This is exact, not an estimate, and it makes no choice among candidates.**

For a specification whose design matrix `D` is built only from the DB's own turn and the DB's own
`player_play` row — the D199 primary set, the player-fixed-effects set, the pass-only set, the
MAN-only set — `D` does not depend on which candidate is attached. Write `c = row 1 of (D'D)⁻¹D'`.
Then

```
β(pairing) = c · y(pairing) = const + Σ_{i ∈ the 870} c_i · y_i(pairing)
```

is **linear in the per-turn choice**, with `y_i ∈ {separations of turn i's visible candidates}`. So

```
β_min = const + Σ_i min_{j ∈ C_i} ( c_i · y_ij )      β_max = const + Σ_i max_{j ∈ C_i} ( c_i · y_ij )
```

encloses the coefficient under **every** admissible pairing of the 870, at once, with no search. The
interval `[β_min, β_max]` is the identified set. It is reported for each of the four specifications
above, beside that specification's frozen-only coefficient and its 95% CI.

**Three things this set is and is not.**

1. It is **conditional on the true receiver being among the visible candidates.** Because the cohort
   hides candidates (1,462 turns with no opposing row, 3,306 with no visible route-runner), the true
   candidate set can only be larger than the visible one, so the true identified set can only be
   **wider**. This bound is optimistic in the direction of precision. Said in the output, not buried.
2. It is **not a confidence interval.** It contains no sampling error. It is reported next to the CI,
   never combined with it, and its width is compared to the CI width as an arithmetic statement about
   which source of uncertainty dominates.
3. It is **a statement about this estimator**, not about coverage. It says nothing about which man
   was covered; it says which numbers the data will and will not support.

Where a specification's `D` depends on the attached candidate (the route fixed effects), `c` is not
fixed and the closed form does not apply. Route-FE specifications are reported as **variants only**
and carry no bound; the output says so on the row.

### O3 — Three resolution variants, each labelled `INFERENCE`, each with its selection stated

| Variant | Rule | What changes |
|---|---|---|
| V1 `INFERENCE — target` | on turns where `target` is True on exactly one candidate, pair to that candidate | **population changes**: only turns where the ball went to one of the visible candidates |
| V2 `INFERENCE — smallest separation` | pair to the candidate with the smaller `separation_at_pass_forward` | no population change; a preference, not a measurement |
| V3 `INFERENCE — largest separation` | the other extreme of V2 | as V2 |

V2 and V3 are the two extreme *realisable* separation-order rules. They are reported because they
bracket every separation-order rule, and because **an invariant test requires them to land inside
O2's interval** (§5, AC3) — if they do not, the bound is wrong.

V1 is **not** allowed to set the label. `target` records where the ball went, not whom the DB
covered; a DB breaking on his own man while the ball goes elsewhere is ordinary football, and V1
silently selects targeted passes. It is reported beside the others with its selection printed.

## 4. The reading rule, fixed now

Only O2 sets the label. Three branches, chosen by arithmetic:

| Branch | Condition | Label | What may then be said |
|---|---|---|---|
| A | `[β_min, β_max]` ⊆ the frozen-only 95% CI | `BOUND CONTAINED IN CI` | the multi-candidate turns cannot move the estimate beyond its own sampling error; D199's null is not an artefact of dropping them |
| B | the interval straddles zero and is **wider** than the CI | `BOUND — NOT IDENTIFIED` | the coefficient is bounded, not identified; the null is neither established nor overturned on this population |
| C | `[β_min, β_max]` excludes zero | `SIGN IDENTIFIED` | under every admissible pairing the sign is the same |

Branch C is not expected. It is written down so that if it happens it is a result fixed in advance,
not a surprise to be narrated. No branch upgrades to a claim in `TASK.md`.

## 5. Acceptance criteria — mechanical, checked by test

- **AC1 — the frozen number reproduces.** The single-candidate frame is byte-identical, on the 25
  shared columns and all 1,731 rows, to `outputs/outcome_test/attributed_turns.csv`; its fit gives
  β `+0.05` yd/unit, 95% CI `[−0.36, +0.46]`, n = 1,731, 102 clusters. Any divergence fails the run.
- **AC2 — the 870 is the same 870.** 1,731 + 870 = 2,601, and the per-turn candidate counts
  (815 / 185 / 5 / 1) match the committed artefact.
- **AC3 — the bound is a bound.** 20 fixed-seed random pairings of the 870, plus V2 and V3, all give
  β inside `[β_min, β_max]`, for every bounded specification.
- **AC4 — the closed form is the estimator.** For a fixed assignment, the closed-form `c · y` equals
  `outcome_test.fit`'s β to 1e-9. The bound is not a different model.
- **AC5 — no bare counts.** Every printed count carries its denominator; every share is of a named
  total.
- **AC6 — no gates.** Zero ambiguous turns, zero candidate separations, an empty band: each returns
  zeros and `NOT FITTED`/`DESCRIPTIVE ONLY`, and nothing raises (pinned by `tests/test_gate_free.py`
  style tests).
- **AC7 — the notebook is portable and gate-free.** `notebooks/bdb27_multi_candidate.ipynb` runs
  end-to-end with Run All on whatever tracking it finds, including this machine's 1,048,575-row
  truncation, which it **prints as a finding** (players per play, plays present, shares) and does not
  stop on. It states the paths it tried if it finds no tracking at all.
- **AC8 — the suite stays green.**
- **AC9 — provenance.** Every input and output sha256'd into `outputs/ambiguity_attribution/manifest.json`
  with the git HEAD and a UTC stamp; a `run_log.csv` row written.

## 6. O5 — The laptop notebook: can geometry decide at all?

The identified set is wide or narrow depending on how far apart the candidates are. That is a
measurable, and only frame-level tracking can measure it. Full tracking is on Conor's laptop; this
machine holds the truncation. The notebook computes, for each of the 870:

- `dist_yd` from the DB at the break frame to each visible candidate (the frozen rule's own distance,
  applied to all candidates rather than only the nearest);
- `nearest_gap_yd` = second-nearest − nearest, the **margin**;
- `cand_gap_yd` = the distance **between the two candidates** — the physical quantity the artefact's
  `separation gap` is not;
- whether the ordering reverses between the snap frame and the break frame (relative motion), since a
  man closing from behind is not the man standing nearest.

Reported: the margin distribution with its quantiles and denominators; how many turns have a margin
above 1 yd, 2 yd, 3 yd; and the same for `cand_gap_yd`. **No threshold decides anything** — the
margins are printed, and the count above each is printed with the total.

Then one variant, `INFERENCE — nearest`, on the turns where tracking makes every candidate visible:
the frozen rule extended to nearest-of-many by distance, reported beside the frozen unique-only result
and inside O2's bound. Its honest statement ships with it: *where the margin is inside the tracking
error the ranking is a coin flip; where it is large the ranking is a measurement. The margin
distribution is the size of that distinction, and it is printed rather than assumed.*

## 7. What the result will not be able to say

1. **Which man he covered.** No field identifies the opponent; every pairing is an inference.
2. **That the 870 are representative.** O4 reports their composition against the 1,731; if they
   differ, the identified set describes a different population, and that is stated rather than
   adjusted away.
3. **Anything about the 1,462 + 3,306 turns where the cohort hides candidates.** They are outside this
   cycle entirely and stay outside the denominator of every claim here.
4. **That a bound is a finding.** A bound says what the data will not rule out. It is not evidence
   for a mechanism, and it does not enter the report.

## 8. Files

| Path | Role |
|---|---|
| `notes/multi-candidate-spec.md` | this file; committed first |
| `src/bdb27/ambiguity_attribution.py` | the analysis: frame builder, closed-form bound, variants, composition, candidate geometry |
| `scripts/ambiguity_attribution.py` | headless `--mode full` run to `outputs/ambiguity_attribution/` |
| `notebooks/bdb27_multi_candidate.ipynb` | **the interface** for anything needing full tracking — Run All, one config cell, no CLI |
| `tests/test_ambiguity_attribution.py`, `tests/test_multi_candidate_notebook.py` | AC1–AC9 |
| `outputs/ambiguity_attribution/{funnel.csv,multi_turns.csv,bounds.csv,variants.csv,composition.csv,geometry.csv,summary.json,manifest.json}` | the artefacts |
| `notes/multi-candidate-result.md` | the result, written after the run |
| `notes/decisions.md` | D207 |

## 9. API contract

So the notebook and the script share one code path:

```python
build_frame(turns_path, player_play_path, *, status="single"|"multiple") -> pd.DataFrame
    # "single"  : one row per turn, exactly the D199 sample (AC1)
    # "multiple": one row per (turn, candidate), n_opp_route_sep >= 2
candidate_columns(df) -> list[str]
identified_set(frame, spec="primary"|"player_fe"|"pass_only"|"man_only") -> dict
    # {beta_frozen, n_frozen, ci_lo, ci_hi, beta_min, beta_max, width,
    #  ci_width, branch, label, n_ambiguous, n_with_separation, note}
variant_fit(frame, rule="target"|"min_sep"|"max_sep", spec=...) -> dict
random_assignments(frame, spec=..., reps=20, seed=...) -> pd.DataFrame   # AC3
composition(frame_single, frame_multiple) -> pd.DataFrame                # O4
candidate_distance_table(frames, turns, player_play) -> pd.DataFrame     # O5, needs tracking
funnel(...) -> pd.DataFrame                                              # O1
```

`build_frame` must reuse `bdb27.outcome_test`'s SQL rather than re-implement it, so AC1 is a
comparison and not a coincidence. Every returned frame carries `attribution = "INFERENCE — ..."`.
