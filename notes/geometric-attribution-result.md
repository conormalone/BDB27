# Geometric attribution — result: notebook shipped, run on the laptop; not measurable here

**Status: the notebook is built and tested; it has not been run on full tracking, because the full
tracking is on Conor's laptop and every tracking file on this machine is the 1,048,575-row
truncation.** Written 2026-11-14. Spec: `notes/geometric-attribution-spec.md` (committed as
`4d6de4b`, before any geometric coefficient existed). Evidence:
`outputs/geometric_attribution_sample/` (a `PLACEHOLDER_SAMPLE` run that proves the code path and
supplies the stop conditions). Code: `src/bdb27/geometric_match.py`,
`scripts/geometric_attribution.py`, `notebooks/bdb27_geometric_attribution.ipynb`. Tests:
`tests/test_geometric_match.py`, `tests/test_geometric_notebook.py`.

`TASK.md`, the band table, the four figures and `notes/report-draft.md` are unchanged.

---

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
| judgeable turns | 3 — **below the pre-stated minimum of 50** |
| verdict | `TOO FEW JUDGEABLE TURNS FOR A RATE`; `TRACKING DOES NOT OBSERVE OPPONENTS ON MOST PLAYS` (99.2%); no estimate fitted |

**The misattribution rate is not measurable on this machine.** Three judgeable turns cannot
distinguish 0% from 100%. The file puts at most four players on a play and tracks 154 of the 497
prospects, so on 99.2% of band turns it observes no opposing player at all. This is the stop the
spec pre-declared as a legitimate outcome, not a failure of the run.

## What one Run All produces on the laptop

1. **Pre-flight** — per tracking file: rows, games, plays, players, median players per play, and a
   pass/stop against the four full-file checks. A truncation stops the run with the file, value
   and threshold named. Nothing is substituted.
2. **Extraction** — a progress line per chunk (rows read, rows kept, share of file, ETA), one
   Parquet shard per input file, resumable after a crash.
3. **Coverage** — unique / ambiguous / none / unobservable / untracked at 2, 3 and 5 yd, with the
   attributed fraction.
4. **The misattribution rate** — the 2×2 against the 1,731 elimination turns, the rate with its
   denominator, and the ambiguous/none shares beside it.
5. **The estimate** — the D199 estimator on the geometric unique set: nine specifications, the
   wild cluster bootstrap, the gate label, and the elimination-versus-geometric table side by side.
6. **Persistence** — the nearest-defender check at the pass-forward frame, as a diagnostic.
7. **Manifest** — sha256 of every input and output, git HEAD, config, rule string.
8. **A plain-English verdict** — the attribution rate, what happened to the coefficient, whether
   the pre-stated criterion was met, and nothing more.

## The decision Conor faces on the laptop

He does not have to decide anything before pressing Run All; every branch is reported. Afterwards:

| What the notebook shows | What it licenses |
|---|---|
| misattribution rate high and geometric β negative, CI excludes zero, \|β\| ≥ 2.0, sign stable | the geometric result is the finding the D199 cycle could not reach; propose a report section to Conor — do not insert it |
| misattribution rate high but the geometric β still null | the D199 null survives better attribution; the attenuation explanation is spent, and "no detectable link in this cohort" is the honest sentence |
| misattribution rate low | the elimination null was not attenuated; D199 stands as a precise null and the geometric run adds precision, not a different story |
| gate not met (fewer than 500 attributed turns, or <30% of the band) | descriptive only, whatever the sign |
| tracking covers fewer than half the band | the stop prints the counts; that is a data answer, not a failure |

## Why the number still cannot be called proof

The geometric match is an inference: the dataset records no opponent identifier, so a receiver
within 3 yd at the break is plausibly, not certainly, the man covered. Rubs, picks and zone-match
all break it. The matched receiver must also be in the 497-player cohort to carry a separation
value, so the estimate remains prospect-versus-prospect. The misattribution rate measures
disagreement between two rules, not the error of either.

## Provenance

- Band: 7,777 `[FULL]` turns, 7,198 plays, 113 players, from
  `data/interim/full_run_20261111/bdb27_full_db_turns.parquet`.
- Elimination set: 1,731 turns, rebuilt from raw `player_play.csv` by
  `outcome_test.build_sample` — the same code path as D199.
- Tracking: `data/raw/game_tracking_2023_sample.csv`, **1,048,575 rows**, used only to prove the
  code path and to trigger the stop; every number above is `PLACEHOLDER_SAMPLE`.
- Manifest: `outputs/geometric_attribution_sample/manifest.json` (hashes of the turns table,
  `player_play.csv`, the sample tracking file and every output).

## What is still not done

- No full-tracking run has happened. The laptop run is the first real one, and its artefacts
  (`outputs/geometric_attribution/`) are not committed from here.
- No report section is written. If the laptop run meets the criterion, the paragraph is proposed to
  Conor, not inserted.
- The `frame_centre` index in the turn table is not used to disambiguate a break frame when a
  player's position is identical across consecutive frames; the tie resolves to the earliest frame
  deterministically, and the 0.25-yd gate still applies. On the sample, all 326 turns recover at
  ≤0.063 yd, so this is a note, not a defect.
