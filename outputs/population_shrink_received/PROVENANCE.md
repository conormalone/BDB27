# Received artefact — not produced by any code in this repository

**This directory is not a pipeline output.** No script, notebook or test in this repo writes here,
and nothing here was computed on this machine. It holds one file, exactly as it arrived.

| Field | Value |
|---|---|
| File | `single_turn_geometry.csv` |
| What it is | `single_turn_geometry.csv` from Conor's laptop run of `notebooks/bdb27_multi_candidate.ipynb` against the **full** tracking files, 2026-11-18, at `notebooks/bdb27_multi_candidate.ipynb`'s `single_candidate_geometry()` step |
| sha256 | `49e08e2186e04227b7625e2296f55e8791a1a99dd3e013027d4ff987fd2b5680` |
| Bytes | 112,744 |
| Line endings | CRLF (a Windows write; this machine's own copy is LF) |
| Rows | header + 1,731 counted rows |
| Received as | a chat attachment, `/home/conormalone/.openclaw/media/inbound/single_turn_geometry_1---fce7368a-00fd-4b9b-aba0-876a73096f8b.csv` — sent twice with identical bytes; the earlier copy is `single_turn_geometry_1---e9e0d9c5-d4e8-46b2-8105-eda05b35af92.csv` (same sha256) |

**The sha256 above identifies the bytes that were sent. It verifies nothing about the run.** No
manifest came with it: there is no hash of the full turns table, no hash of the tracking file, no git
HEAD, no run timestamp, and no per-turn detail beyond the nine columns below. The counts in this file
cannot be checked against the run that produced them from anything on this machine, and cannot be
regenerated here — this machine has no full game tracking file, so its own `single_turn_geometry.csv`
(`outputs/ambiguity_attribution/`) carries an empty `dist_nearest_yd` and an empty `within_radius` on
every row and reports `NOT EVALUABLE`.

## What it carries

Nine columns, one row per single-candidate turn (the 1,731 that the frozen rule attributes):

`game_id, play_id, nfl_id, frame_start, n_candidates_tracked, dist_nearest_yd, within_radius,
geometry_status, attribution`

Counted from the file as committed, not quoted from a chat message:

| Field | Count |
|---|---|
| rows | 1,731 |
| `geometry_status == tracked` | 1,720 |
| `no_candidate_tracked` | 10 |
| `break_frame_unrecovered` | 1 |
| `within_radius == True` (≤ 3 yd) | **213** |
| `within_radius == False` | 1,507 |
| `within_radius` blank (not evaluable) | 11 |
| `attribution` | `RULE` on all 1,731 |

The 213 is the fifth and last bar of `fig_population_shrink` (stage `06_figures`, figure 19): the
only number on that figure that is not reproducible on this machine. The other four bars are read
from the committed `outputs/ambiguity_attribution/funnel.csv`.

**One number in the request that asked for this figure does not match the file.** The request
described the paste as "1,731 rows, 1,713 tracked". The file has **1,720** tracked rows and **11**
not evaluable (10 `no_candidate_tracked`, 1 `break_frame_unrecovered`); `1,720 − 1,507 = 213`. The
1,713 appears nowhere in the file's own columns. The figure and its sidecar use the file's numbers.

The copy here exists so the received table is durable rather than living in a chat transcript, and so
a future run's `single_turn_geometry.csv` can be diffed against it row by row. It is deliberately not
named `*_full` and is deliberately not under `outputs/ambiguity_attribution/`.

## Who reads it

`src/bdb27/figures.py` (`population_shrink_ladder`) reads this file **read-only** when stage
`06_figures` renders figure 19, counts `within_radius == True`, and refuses to draw if the bytes here
stop matching the sha256 pinned in that module — so the 213 cannot become a different number without a
visible code change. Nothing writes to this directory.
