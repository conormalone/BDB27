# Turn context: the 30–60° band joined to `player_play.csv`

**Provenance `[FULL]`.** One row per band turn, built by
`scripts/turn_context.py` from `src/bdb27/turn_context.py`. Every number below is
in an artefact under `outputs/turn_context/` and every input and output is
hashed in `outputs/turn_context/manifest.json`. Reproduce with:

```
.venv/bin/python scripts/turn_context.py
```

Band: `30 <= abs_turn_angle < 60`, `setting = 'game'`, `position_group = 'DB'`,
`valid`, left-closed — the project's frozen band, taken at import from
`bdb27.outcome_link` (`BAND_LO = 30.0`, `BAND_HI = 60.0`). The turn rows are
read in full from the parquet, and that read is checked against that module's
own band reader by exact key-multiset equality; a mismatch raises rather than
being resolved by hand.

## 1. The join and its coverage

| | value | artefact |
|---|---:|---|
| band turns (rows) | 7,777 | `summary.json → counts.band_turns` |
| distinct `(game_id, play_id, nfl_id, frame_start)` | 7,774 | `counts.distinct_turn_keys` |
| rows sharing a key with another turn | 3 | `counts.duplicate_key_rows` |
| distinct `(game_id, play_id, nfl_id)` | 7,404 | `counts.turn_players` |
| distinct plays | 7,198 | `counts.band_plays` |
| distinct players | 113 | `counts.band_players` |
| distinct games | 809 | `counts.band_games` |
| turns with **no** `player_play` row | 0 | `counts.no_player_play_row` |

The join key is the turning player's own `(game_id, play_id, nfl_id)` row. No
opponent, radius or attribution is inferred: every band turn has exactly one
such row, so the join adds columns and never a row. The three rows that share a
four-column key with another turn are two overlapping segmentations of the same
player's turn (different `frame_end`, different angle); both keep their own row
and both carry the same play context. They are not deduplicated.

## 2. Pass and run

`play_type` is `pass` where `pass_result` is present and `run` where it is
absent (`play_type_split.csv`):

| play_type | turns | share of 7,777 |
|---|---:|---:|
| pass | 5,197 | 66.8% |
| run | 2,580 | 33.2% |

Checked against `play_description` (`description_check.csv`):

| play_type | description check | turns |
|---|---|---:|
| run | reads as a designed run ("left tackle", "right guard", "right end", "up the middle", …) | 2,554 |
| run | does not (all 26 are aborted snaps, fumbles, a kneel and two-point tries) | 26 |
| pass | reads as a dropback (no run wording) | 4,826 |
| pass | reads as a run — all 371 are `pass_result = 'R'`, i.e. quarterback scrambles | 371 |

So the two-way split hides a third group. Of the 5,197 pass turns, **371 are
scrambles** (`counts.pass_scramble_turns`), which leaves **4,826** turns
(62.1%) that are dropbacks ending in a throw, interception or sack. The
scrambles are kept inside `play_type = 'pass'` as specified, flagged in the
`pass_scramble` column, and their share is reported separately
(`play_type_split.csv`); no cut of them is merged back into the two-way split.

**No turn in the band lacks a `player_play` row**, so the split covers the whole
band rather than a labelled subset.

## 3. Field availability (`field_availability.csv`)

Present = non-null on the turn's own `player_play` row. Denominators are printed
in every row; nothing is withheld for being thin.

| field | band (n=7,777) | pass (n=5,197) | run (n=2,580) |
|---|---:|---:|---:|
| `team_coverage_man_zone` | 6,823 (87.7%) | 4,723 (90.9%) | 2,100 (81.4%) |
| `team_coverage_type` | 6,823 (87.7%) | 4,723 (90.9%) | 2,100 (81.4%) |
| `coverage_assignment` | 4,139 (53.2%) | 4,134 (79.5%) | **5 (0.19%)** |
| `coverage_assignment_at_snap` | 4,107 (52.8%) | 4,102 (78.9%) | 5 (0.19%) |
| `pass_result` | 5,197 (66.8%) | 5,197 (100%) | 0 (0.0%) |
| `cushion` | 4,543 (58.4%) | 3,154 (60.7%) | 1,389 (53.8%) |
| `tackle` = `assist` = `tackle_for_loss` = `caused_forced_fumble` | 1,232 (15.8%) | 795 (15.3%) | 437 (16.9%) |
| `down`, `yards_to_go`, `target` | 7,777 (100%) | 5,197 (100%) | 2,580 (100%) |
| `separation_at_pass_forward` | **0 (0.0%)** | 0 (0.0%) | 0 (0.0%) |
| `route_ran` | **0 (0.0%)** | 0 (0.0%) | 0 (0.0%) |

Two of those zeros are structural, not gaps: `separation_at_pass_forward` and
`route_ran` describe an offensive player and are never filled on a defender's
row. Any separation figure over this band therefore comes from a receiver's row
on the same play — a different join, not this table — and is not reproducible
from `turn_context.parquet`.

The four defensive-event fields are present on exactly the same 1,232 turns:
they are one block, and a turn either carries it or does not.

Median values, descriptive only (`medians.csv`): cushion 6.57 yd on pass turns
(n=3,154) and 6.69 yd on run turns (n=1,389); median `abs_turn_angle` 50.18° on
pass and 50.25° on run. The split does not separate shallow breaks from steep
ones, and it does not separate close from loose coverage.

## 4. What the split does to the demand claim

The claim is a **band share against a band share**: 13.26% of valid game DB turns
sit in 30–60° against 2.21% of Combine DB turns, 6.01×. The band share counts
*turns*, and turns are what the claim is about, so the headline is unaffected by
the split — but the band share is not the same under both play types
(`demand_scope.csv`):

| scope | turns | in 30–60° | band share |
|---|---:|---:|---:|
| all valid game DB turns | 58,664 | 7,777 | 13.26% |
| pass turns only | 42,004 | 5,197 | 12.37% |
| run turns only | 16,660 | 2,580 | 15.49% |

Run turns are slightly *more* likely to sit in the band than pass turns, so the
headline is not an artefact of run support inflating the numerator. What the
split does affect is the **wording**: two thirds of the band are coverage breaks
and one third are pursuit and run-support turns, and on the run third the
coverage fields are absent. Any sentence that describes the band as coverage
demand must either condition on `play_type == 'pass'` or be reworded to describe
turn shape. Both subsets are reported above; neither is chosen here.

## 5. Three sentences for the paper

> The 30–60° band is a count of turns, not of coverage reps: of its 7,777 game
> DB turns, 5,197 (66.8%) fall on pass plays and 2,580 (33.2%) on run plays.
> On the run turns the coverage fields are effectively absent —
> `coverage_assignment` is present on 5 of 2,580 (0.19%) — so those turns are
> pursuit and run-support turns rather than coverage breaks.
> The supply-against-demand ratio is a statement about turn shape and survives
> the split (12.37% of pass turns and 15.49% of run turns sit in the band, around
> the 13.26% pooled figure), but the band must not be described as coverage
> demand unless the sentence conditions on `play_type = 'pass'`.

## 6. Points where this re-derivation differs from the pre-measurement

- **Band size: 7,777, not 7,778.** The band is left-closed in the project
  (`.venv` readings: no turn sits at exactly 30.0° or exactly 60.0°), so the
  inclusive-edge count cannot be reproduced from this turn file. Every count
  here is 7,777-based; the pass figure follows, 5,197 rather than 5,198.
- **Pass is 5,197, of which 371 are scrambles.** The pre-measurement did not
  separate `pass_result = 'R'`; the three-way breakdown is in
  `play_type_split.csv` and the two-way split is unchanged by it.
- **`separation_at_pass_forward` is 0% present on band turns.** It cannot be
  measured on the defender's own row. The MAN/ZONE separation figures quoted
  from band *plays* are a receiver-row measurement and are not part of this
  table.

Everything else reproduces: 87.7% team coverage label, 58.4% cushion, 53.2%
`coverage_assignment`, 15.8% defensive-event block, 5 of 2,580 run turns with a
coverage assignment, 2,554 of 2,580 run turns reading as runs, and the medians
above.
