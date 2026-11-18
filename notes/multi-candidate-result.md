# Multi-candidate attribution — result: the FULL run reports **N = 2,558 of 7,777**, recorded here; this machine still holds the no-tracking run, **N = 2,295**

**Status: two states of one number, both readable.** The notebook has been run on full tracking, on
Conor's laptop, on 2026-11-18, after the Amendment-3 precedence fix (`49a6b0f`). He sent
`precedence.csv`. The figures from that run are recorded in §1 **as he sent them**. The committed
artefacts in `outputs/ambiguity_attribution/` on this machine are **not** that run — they are the
no-tracking run, `N = 2,295`, recorded in §4 and left exactly as committed. Neither state overwrites
the other. Spec: `notes/multi-candidate-spec.md`, including **Amendment 3**. Decisions: D207 (the
no-tracking number and its composition), D208 (precedence executed), D210 (this entry). Code:
`src/bdb27/ambiguity_attribution.py`; interface `notebooks/bdb27_multi_candidate.ipynb`.

`TASK.md`, `notes/report-draft.md`, the band table, the four figures, the notebook and `src/` are
unchanged by this note.

---

## 1. The FULL result — reported from Conor's laptop, 2026-11-18

**Provenance, exactly.** Conor ran `notebooks/bdb27_multi_candidate.ipynb` on his laptop with the
full tracking files and sent **`precedence.csv`**, the one table Amendment 3 §3.6 asks for. The
attachment reached this machine at 11:07 GMT on 2026-11-18; the file itself carries no run
timestamp and no git HEAD, so the exact commit he ran is not recorded anywhere — the tree here was
at `49a6b0f` when the file arrived. Every number in this section is **reported**, not recomputed.

What accompanies it is nothing else: no `manifest.json`, no sha256 of the full turns table or of the
full tracking file, no git HEAD, no run timestamp, no row counts, no `geometry.csv`, no
`route_a_audit.csv`, no `verdict.txt`. The received file is one table of twelve counted rows.

**As it was sent.** The twelve rows, in the order and wording Conor's message gave them (the same
twelve rows, in columnar form, are in the committed CSV at
`outputs/ambiguity_attribution_received/precedence.csv`):

```
pool_with_visible_route_runner  3,008 of 7,777
N_frozen                        1,731 of 3,008   (RULE)
N_geo_5                           411 of 1,277
N_geo_8                           178 of 1,277
geo_5_plus_8                      589 of 1,277
pool_for_route_a_slot             688 of 3,008
N_route_a                         238 of 688
route_a_reference                 564 of 1,277   (cross-check pass, not a slot)
overlap_reference_with_geo        326 of 564
n_double_counted                    0 of 2,558
N                               2,558 of 7,777   (32.89%)
N_if_both_passes_summed         2,884 of 7,777   (32.89? no — 37.08%, labelled upper bound)
```

The parenthetical on the last line is his own correction, kept as sent: 2,884 of 7,777 is 37.08%,
and the row is labelled an upper bound. Both forms of the table — his message and the CSV — agree
row for row.

| Slot | Label on the row | n | of | share of its own denominator | share of N |
|---|---|---|---|---|---|
| `N_frozen` | `RULE` | **1,731** | 3,008 | 57.55% | 67.67% |
| `N_geo_5` | `INFERENCE — geometry 5 yd` | **411** | 1,277 | 32.18% | 16.07% |
| `N_geo_8` | `INFERENCE — geometry 8 yd` | **178** | 1,277 | 13.94% | 6.96% |
| `N_routeA` | `INFERENCE — Route A` | **238** | 688 | 34.59% | 9.30% |
| **`N`** | the four slots, disjoint, summed | **2,558** | **7,777** | **32.89% of the band** | 100% |

The label shares are arithmetic on the paste's own counts, not a new measurement: 1,731 / 2,558 =
67.67% `RULE`, 827 / 2,558 = 32.33% `INFERENCE` — 589 geometric plus 238 Route A.

The pool, as the paste prints it: 3,008 turns of 7,777 carry at least one visible opposing
route-runner; the frozen rule takes 1,731 of them and leaves 1,277.

## 2. The reconciliation arithmetic — every line checks

No count below is new. Each line is an identity between numbers the paste already prints, carried
here so a reader can see the total is a sum and not a claim.

| # | Identity | Result |
|---|---|---|
| 1 | `1,731 + 589 + 238` | **= 2,558 = `N`** — the four slots summed (frozen + geometry + Route A) |
| 2 | `1,731 + 411 + 178 + 238` | **= 2,558** — the same sum with the radii split |
| 3 | `411 + 178` | **= 589 = `geo_5_plus_8`** — the two geometry slots adjacent, not overlapping |
| 4 | `564 − 326` | **= 238 = `N_route_a`** — Route A's slot is its reference pass minus what geometry had already taken |
| 5 | `1,277 − 589` | **= 688** — the slot pool for Route A, and the pool the paste prints |
| 6 | `589 + 238` | **= 827 of the 1,277** the frozen rule left (64.76%) — so 450 of those 1,277 stay unattributed |
| 7 | `3,008 − 1,731` | **= 1,277** — the frozen-excluded pool both geometry and Route A draw on |
| 8 | `2,558 + 326` | **= 2,884 = `N_if_both_passes_summed`** — the upper bound is exactly `N` plus the overlap |
| 9 | `n_double_counted` | **= 0 of 2,558**, as AC3 requires; the paste's own row, not a check made here |

Identity 4 is the whole of Amendment 3 in one line: Route A's reference pass sees all 1,277, the
radii had already taken 326 of the 564 turns it could attribute, and only the remaining 238 are
added to `N`. Route A reordered first would have claimed 564; that counterfactual is what the
overlap row measures, and the paste reports the disagreement as a number rather than a silence.

**One agreement this machine can check.** The reference pass is **564 of 1,277** — exactly the
committed no-tracking run's Route A slot, 564 of 1,277 (§4). That is the expected shape of a
tracking-free route on the same pool: Route A reads `player_play.csv` and never reads tracking, so
the run with tracking and the run without it agree on the turns it can attribute. It is the only
count in the paste that has a committed counterpart here to meet, and it meets it exactly.

## 3. The upper bound — flagged, not resolved

`N_if_both_passes_summed` prints **2,884 of 7,777 (37.08%)**. Before Amendment 3 that number *was*
the headline: the code added the Route A pass and the geometric slots without subtracting the turns
both took. On the no-tracking run it happened to equal `N` (both 2,295), because with no tracking
the overlap was zero — which is exactly how the defect survived that run and could only appear once
tracking existed. On the FULL run it is **326 turns above `N`** — 37.08% of the band against 32.89%.

**This note does not resolve it and does not restate it.** It is not a second total, not a rival
headline, and not a rival estimate of anything: it is the printed size of the double count, and the
paste labels it *"an upper bound on `N`, never `N`"*. `N` is 2,558. A reader who finds 2,884 quoted
anywhere in this project is looking at the pre-Amendment-3 number or at this row, not at the
attribution.

## 4. What this machine holds — the no-tracking run, **N = 2,295** (unchanged)

`outputs/ambiguity_attribution/` is **not** the run in §1. It was produced here on 2026-11-18
08:07 UTC from `git_head` `99df784` — the head at run time; HEAD has since moved to `49a6b0f` —
with `config.tracking = null`: no frame-level tracking, so no candidate distance, so no geometric
slot. Every file in it is the committed artefact of that run and none of it was touched by this
cycle.

| Slot | Label | n | of | status |
|---|---|---|---|---|
| `frozen` | `RULE` | 1,731 | — | — |
| `geo_5` | `INFERENCE — geometry 5 yd` | 0 | 1,277 | `NOT EVALUABLE` — candidate distances need frame-level tracking at the break frame, which this run did not have |
| `geo_8` | `INFERENCE — geometry 8 yd` | 0 | 1,277 | `NOT EVALUABLE` — same |
| `route_a` | `INFERENCE — Route A` | 564 | 1,277 | — |
| **`N`** | | **2,295** | **7,777** | **29.51% of the band** |

`disjoint: true`, `n_double_counted: 0`, `overlap_route_a_reference_with_geo: 0`,
`N_if_both_passes_summed: 2,295` — all four read from `summary.json` and `precedence.csv` as
committed. The composition tables, the identified set and the `BOUND — NOT IDENTIFIED` label in
`verdict.txt` belong to **this** run. The paste in §1 carries no coefficient, no interval and no
label, and this note does not attribute the `verdict.txt` statistics to the FULL run.

**Two states, one difference that matters.** The no-tracking number and the FULL number differ by
**263 turns** (2,558 − 2,295), and every one of them is an `INFERENCE`: 178 at 8 yd, 411 at 5 yd, less
the 326 the radii took out of Route A's reference pass (589 − 326 = 263). The `RULE` slot is
**identical at 1,731 in both runs**, as it must be: the frozen 3-yd rule is never re-picked, and a
tracking file cannot move it.

Row for row, the two `precedence.csv` files agree on exactly three counts — the pool (**3,008**),
`N_frozen` (**1,731**) and `route_a_reference` (**564**, the cross-check pass on the frozen-excluded
pool) — and differ on the two radii, `geo_5_plus_8`, the slot pool, the Route A slot, the overlap and
both totals. `n_double_counted` is **0** in each, its denominator being `N` in each (2,295 and 2,558).
That is the whole of the difference between the two states, and it is confined to the rows that need
frames.

## 5. One-turn difference in the committed files, flagged

The paste's `pool_with_visible_route_runner` is **3,008**. The committed `funnel.csv` row
`turns_with_opposing_route_runner` is **3,009**, and `verdict.txt`'s funnel sentence says *"3,009
carry a visible opposing route-runner"*. The committed `precedence.csv` row says **3,008**, so the
paste agrees with the precedence row and not with the funnel row.

The two committed files already differ by that same single turn in the no-tracking run, and
`funnel.csv` prints the reason in its own two groupings: band rows are 7,209, turn keys are 7,206 —
band rows that are second detections sharing a `frame_start` merge into one turn key. The pool row is
counted per turn key, the funnel row per band row.

**Not resolved here, and it touches no slot.** `N` uses `N_frozen` of 3,008 in both states, and the
1,277 pool is 3,008 − 1,731 in both. Recorded so that a reader who finds 3,009 and 3,008 side by side
knows it is a unit difference that predates this cycle, not a drift introduced by the FULL run.

## 6. What a future reader will see, and what they cannot get here

The record is this note, D207, D208 and D210, and two directories that are different runs.

- **To see the numbers:** §1 and §2 above, and the received file committed byte-for-byte at
  `outputs/ambiguity_attribution_received/precedence.csv` (sha256
  `896a1f5c189ff4c409cf72d8051b85c9dd0c52dc0c09074ceeb32edbea3aaeb8`, 1,345 bytes, CRLF line
  endings — a Windows write, against LF in the local file). That sha256 identifies **the bytes that
  were sent**. It verifies **nothing about the run**: there is no manifest to check the counts
  against, and no input or output hash was sent with them.
- **To reproduce 2,558:** they cannot, from this repository. `python scripts/ambiguity_attribution.py
  --mode full` on this machine produces 2,295 and always will until a full tracking file is present;
  the geometric slots come back `NOT EVALUABLE`, and there is no copy of the full tracking data here.
  Re-deriving 2,558 needs Conor's laptop and its full tracking files, a Run All, and a
  `precedence.csv` to compare against the committed received copy.
- **What the paste does not contain, and this note therefore cannot report:** the agreement rate
  between Route A and the geometric matcher on the 326 turns both took (the §4 cross-check needs the
  per-turn attributed man, not a count — **NOT ON THIS MACHINE**); the per-turn detail behind 411 /
  178 / 326 (`geometry.csv`, `route_a_audit.csv` — **NOT ON THIS MACHINE**); the coefficient,
  interval, identified set or label for the FULL run (**NOT ON THIS MACHINE**); and any funnel or
  row count other than the four the paste prints (**NOT ON THIS MACHINE**).

## 7. What the FULL run does not establish

- `N` is a count of **attributions with stated evidence**, not of known coverages. The dataset
  records no opponent identifier; a turn attributed at 5 or 8 yd is labelled `INFERENCE` for that
  reason, and 827 of the 2,558 are 8-yd, 5-yd or Route A attributions by that standard.
- The geometric slots are a *radius extension*, not a tie-break: the rule is still *exactly one
  candidate inside the radius*. Nothing in the paste says which man a 6-yd match named.
- The 326 overlap is a **count of turns two routes both took**. It is not an error rate for either
  route. The cross-check that would read agreement is not in the paste (§6).
- The 2,884 upper bound is not a competing total, and the 2,295 on this machine is not a stale
  headline to be replaced: it is the number the no-tracking run supports, and it is what this
  repository regenerates.

## Provenance

- **FULL run:** Conor's laptop, 2026-11-18; received here as `precedence.csv`
  (1,345 bytes, CRLF, sha256 `896a1f5c…3aaeb8`), committed at
  `outputs/ambiguity_attribution_received/`. No manifest, no input hash, no HEAD, no timestamp
  accompanied it — the tree here was at `49a6b0f` when it arrived, and the commit he ran is not
  recorded.
- **No-tracking run:** this machine, 2026-11-18 08:07 UTC, `git_head` `99df784`,
  `config.tracking = null`, written to `outputs/ambiguity_attribution/` and committed unchanged.
  `manifest.json` there hashes the turns table (`f2e7cd01…dfb8e04`) and `player_play.csv`
  (`68926a26…c35b2a`) plus every output of that run.
- Band: 7,777 `[FULL]` turns, from
  `data/interim/full_run_20261111/bdb27_full_db_turns.parquet`.
- **Unchanged by this cycle:** `TASK.md`, `notes/report-draft.md`, the band table, the four figures,
  `notebooks/bdb27_multi_candidate.ipynb`, `src/`, the tests.
