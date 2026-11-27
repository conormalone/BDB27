# Geometric attribution — result: FULL run done on the laptop, recorded here as `DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE`

**Status: the notebook has been run on full tracking, on Conor's laptop, on 2026-11-15. Its
closing output is recorded below as he pasted it. The full artefacts
(`outputs/geometric_attribution/`) are not on this machine — that directory is empty here — so
every number in the FULL section is *as reported by Conor*, not recomputed here. The sample run
further down is what this machine produced, and it remains the code-path validation.** Spec:
`notes/geometric-attribution-spec.md` (committed as `4d6de4b`, before any geometric coefficient
existed). Decisions: D200 (the pre-stated branches) and D204 (this outcome). Code:
`src/bdb27/geometric_match.py`, `scripts/geometric_attribution.py`,
`notebooks/bdb27_geometric_attribution.ipynb`. Tests: `tests/test_geometric_match.py`,
`tests/test_geometric_notebook.py`.

`TASK.md`, the band table, the figure set and `notes/report-draft.md` are unchanged.

---

## FULL run — reported from Conor's laptop, 2026-11-15 09:52 GMT

**Provenance, exactly.** Conor ran `notebooks/bdb27_geometric_attribution.ipynb` on his laptop
against the full game tracking and pasted the notebook's closing output. This machine holds no
part of that run: `outputs/geometric_attribution/` exists and is empty, there is no manifest, no
sha256 of any FULL input or output, and no FULL row or play count. Every number in this section is
therefore a **reported number**, not a recomputed one. Nothing below was produced by the Pi.

| Quantity | FULL run (reported) |
|---|---|
| attributed turns | 471 |
| attributed share of the band (7,777 turns) | 6.1% |
| gate: attributed turns | **not met** — 471 against ≥500 |
| gate: share of band | **not met** — 6.1% against ≥30% |
| misattribution check: turns where both rules could be evaluated | 213 |
| agree | 213 |
| disagree | 0 |
| misattribution rate | **0.0% (0 of 213)** |
| estimate, geometric unique set | **−0.38 yd** per unit `apex_retention`, 95% CI **−1.08 to +0.33** |
| estimator sample | **n = 411 turns, 84 player clusters** |
| D199 comparison (elimination-matched set) | **+0.05 yd**, 95% CI **−0.36 to +0.46** |
| label | `DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE` |

### Gate arithmetic

Both arms of the frozen gate (D197, copied into spec §10) fail, so the label is decided by
arithmetic and not by judgement:

- attributed turns **471 < 500**;
- attributed share **6.1% < 30%** of the band (471 of 7,777).

The number is still reported in full; what the gate changes is what it may be called. Under
spec §10, either arm failing gives `DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE` and **no finding,
no claim either way**.

### Misattribution arithmetic, and its denominator

As printed:

> of 213 turns where both rules could be evaluated, the geometric match named the same player as
> the elimination rule on 213 and a different player on 0 — a misattribution rate of 0.0%
> (0 of 213).

0 / (213 + 0) = 0.0%. The 213 clears the spec's pre-stated 50-judgeable-turn floor for printing a
rate, so the rate is printed rather than replaced by `TOO FEW JUDGEABLE TURNS FOR A RATE`.

**The denominator is 213 of 471, not 213 alone.** 471 turns were attributed in total; only 213 of
them sit in the elimination set, where the elimination rule has a unique candidate to compare
against (spec §5). The other **258** attributed turns are outside that comparison — the check
cannot see them. Both counts are stated so a reader can see that the rate conditions on 45.2% of
the attributed set (213 / 471). The rate is over turns where both rules could be evaluated; it is
not a rate over all attributed turns.

### The estimate, beside D199's

| Set | β per unit `apex_retention` | 95% CI | n | clusters |
|---|---|---|---|---|
| D199 — elimination-matched | +0.05 yd | −0.36 to +0.46 | 1,731 turns | 102 players |
| FULL — geometric unique at 3 yd | −0.38 yd | −1.08 to +0.33 | 411 turns | 84 player clusters |

The geometric interval includes zero and overlaps D199's heavily. The point estimate is negative,
which is the stated expectation's sign (spec §11(a)), but (b) fails — the interval does not exclude
zero — and the gate fails independently. Under spec §11 the result is
`DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE`: **no attenuation claim is made**, in either
direction. The geometric route neither rescues D199 nor overturns it.

**Not decomposed in the paste.** 471 turns are attributed, 411 enter the estimator and 213 enter
the misattribution check. The paste does not say which turns the 60-turn gap between 471 and 411
came from, so this note does not either; that breakdown is not available on this machine.

### The pre-stated branch this triggers — D200, quoted

D200 set the branches before the run. This outcome lands on two of them at once, and D200's own
wording is quoted for each rather than paraphrased:

- **The gate** — *"The gate (≥500 attributed, ≥30% of the band) decides what the number may be
  called; below it the label is `DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE` and no claim is made in
  either direction."* 471 < 500 and 6.1% < 30%, so this is the sentence that fixes the label.
- **The low rate** — *"if the rate is low, D199 stands as a precise null."* The rate is 0 of 213, so
  this is the clause D200 wrote for that outcome.

Quoted, not paraphrased into anything stronger. The two agree on the label and nothing here is
claimed beyond them: the geometric estimate is descriptive only, it is not a second estimate that
replaces D199's, and no attenuation claim is made. What D200 says about D199's standing is D200's
sentence, above. None of D200's outcomes enters `TASK.md`, the band table, the figure set or
`notes/report-draft.md` without Conor's word, and he has not given it.

### What the FULL run does not establish

- The misattribution rate measures **disagreement between two rules**, not the error of either
  (spec §6). 0 of 213 says the elimination rule never named a different player from the geometric
  nearest route-runner *on the 213 turns both could see*; it does not certify either rule.
- The rate conditions on geometry being able to decide, and on the elimination rule having a
  unique candidate. That subset is not random; its size (213 of 471 attributed) is the conditioning
  made visible.
- The estimate remains prospect-versus-prospect and descriptive: 6.1% of the band, 411 turns,
  84 clusters, an interval spanning zero.

## What ships

| File | What it does |
|---|---|
| `notebooks/bdb27_geometric_attribution.ipynb` | **The interface.** Run All; no CLI; nothing below §1 to edit. |
| `src/bdb27/geometric_match.py` | Pre-flight, chunked extraction, break-frame recovery, the frozen match, coverage, the misattribution rate, the geometric sample, the manifest. |
| `scripts/geometric_attribution.py` | The same pipeline headless — used to prove it here; not something Conor needs. |
| `tests/test_geometric_match.py` | 22 tests: the rule, the statuses, the stops, resume, determinism, estimator wiring, verdict text. |
| `tests/test_geometric_notebook.py` | 10 contract tests: unexecuted, UTF-8 on Windows, shared estimator, misattribution before the fit. |

**The estimator is not re-implemented.** `outcome_test.run_all` and `outcome_test.verdict` are
called on the geometric frame, so a difference between the D199 coefficient and the geometric one
cannot come from the estimator. A test asserts the functions' module identity.

## What the sample run measured here — and what it could not

Run on the 1,048,575-row sample (`PLACEHOLDER_SAMPLE`), the pipeline reproduces the D196/D197
geometric table on the same 326 turns **exactly**: unique 4, ambiguous 0, none 59, unobservable
263 at 3 yd, at 2 yd unique 1 and at 5 yd unique 5. That agreement is the code's validation: two
independent implementations of the frozen rule, the same counts.

| Quantity | Sample-file value |
|---|---|
| band turns | 7,777 `[FULL]` |
| break frames recovered (≤0.25 yd) | 326 — the sample tracking holds only the D196 sample turns |
| 3-yd status on the band | unique 4 · ambiguous 0 · none 59 · unobservable 263 · untracked 7,451 |
| attributed fraction at 3 yd | 0.1% (4 of 7,777) |
| misattribution, elimination set (1,731) | agree 3 · disagree 0 · ambiguous 0 · none 14 · unmeasurable 1,714 |
| judgeable turns | 3 — below the pre-stated minimum of 50 |
| verdict | `TOO FEW JUDGEABLE TURNS FOR A RATE`; `TRACKING DOES NOT OBSERVE OPPONENTS ON MOST PLAYS` (99.2%); no estimate fitted |

**The misattribution rate was not measurable on this machine.** Three judgeable turns cannot
distinguish 0% from 100%, and no estimate could be fitted on four unique turns. The file puts at
most four players on a play and tracks 154 of the 497 prospects, so on 99.2% of band turns it
observes no opposing player at all. That was the stop the spec pre-declared as a legitimate
outcome; the FULL numbers above come from the laptop run, not from this sample.

## What one Run All produces on the laptop

1. **Pre-flight** — per tracking file: rows, games, plays, players, median players per play, and a
   pass/stop against the four full-file checks. The report prints with denominators; an empty
   extraction reports zeros rather than raising (D202).
2. **Extraction** — a progress line per chunk (rows read, rows kept, share of file, ETA), one
   Parquet shard per input file, resumable after a crash.
3. **Coverage** — unique / ambiguous / none / unobservable / untracked at 2, 3 and 5 yd, with the
   attributed fraction.
4. **The misattribution rate** — the 2×2 against the 1,731-turn elimination set, the rate with its
   denominator, and the ambiguous/none shares beside it.
5. **The estimate** — the D199 estimator on the geometric unique set: nine specifications, the
   wild cluster bootstrap, the gate label, and the elimination-versus-geometric table side by side.
6. **Persistence** — the nearest-defender check at the pass-forward frame, as a diagnostic.
7. **Manifest** — sha256 of every input and output, git HEAD, config, rule string.
8. **A plain-English verdict** — the attribution rate, what happened to the coefficient, whether
   the pre-stated criterion was met, and nothing more.

## The branches, and which one the FULL run selected

D200 pre-stated the branches before the run (§ *What Conor decides on the laptop*):

| What the notebook shows | What it licenses |
|---|---|
| misattribution rate high and geometric β negative, CI excludes zero, \|β\| ≥ 2.0, sign stable | the geometric result is the finding the D199 cycle could not reach; propose a report section to Conor — do not insert it |
| misattribution rate high but the geometric β still null | the D199 null survives better attribution; the attenuation explanation is spent, and "no detectable link in this cohort" is the honest sentence |
| misattribution rate low | D199 stands as a precise null |
| gate not met (fewer than 500 attributed turns, or <30% of the band) | descriptive only, whatever the sign |
| tracking covers fewer than half the band | the stop prints the counts; that is a data answer, not a failure |

The FULL run hits **the fourth row** (471 < 500, 6.1% < 30%): descriptive only, whatever the sign.
The misattribution rate is 0 of 213, which also hits **the third row**; D204 records both, quotes
both in D200's words, and treats the gate row as the one that fixes the label. The estimate is not
offered as adding precision, because the gate keeps it descriptive.

## Why the number still cannot be called proof

The geometric match is an inference: the dataset records no opponent identifier, so a receiver
within 3 yd at the break is plausibly, not certainly, the man covered. Rubs, picks and zone-match
all break it. The matched receiver must also be in the 497-player cohort to carry a separation
value, so the estimate remains prospect-versus-prospect. The misattribution rate measures
disagreement between two rules, not the error of either.

## Provenance

- **FULL run:** Conor's laptop, 2026-11-15, reported by paste at 09:52 GMT. No artefact of it is on
  this machine; `outputs/geometric_attribution/` is empty here.
- Band: 7,777 `[FULL]` turns, 7,198 plays, 113 players, from
  `data/interim/full_run_20261111/bdb27_full_db_turns.parquet`.
- Elimination set: 1,731 turns, rebuilt from raw `player_play.csv` by
  `outcome_test.build_sample` — the same code path as D199.
- Tracking here: `data/raw/game_tracking_2023_sample.csv`, **1,048,575 rows**, used only to prove
  the code path and to trigger the stop; every sample number above is `PLACEHOLDER_SAMPLE`.
- Manifest here: `outputs/geometric_attribution_sample/manifest.json` (hashes of the turns table,
  `player_play.csv`, the sample tracking file and every output). It covers the sample run only.

## What is still not done

- The FULL artefacts are not committed from here, and cannot be: they are on Conor's laptop. The
  numbers above stand on his paste until the manifest is brought across.
- No report section is written. The FULL run misses the gate, so under D200 nothing enters the
  report draft without Conor's word; he has not given it.
- The `frame_centre` index in the turn table is not used to disambiguate a break frame when a
  player's position is identical across consecutive frames; the tie resolves to the earliest frame
  deterministically, and the 0.25-yd gate still applies. On the sample, all 326 turns recover at
  ≤0.063 yd, so this is a note, not a defect.
