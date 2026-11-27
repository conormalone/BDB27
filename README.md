# Towards a test for the 45° turn in defensive backs

Defensive backs change direction on almost every snap. Measured from their
tracking coordinates — a heading change of at least 30° across half a second,
with entry and exit speeds above 2.5 yd/s — the turns they actually make are
large. Across three NFL seasons, more than two in five of the 58,664 detected
defensive-back turns are sharper than 90°, and nearly a quarter are sharper
than 120°. The band a short-area break lives in, 30–60°, is 13% of what the
game asks for.

The Combine asks for almost none of it. In 1,179 detected turns from 122
draft-prospect defensive backs, 26 turns — 2.2% — fall in that band, from 16
players, and 14 of those come from the 3-cone. The drills are built for other
things: the short shuttle's median turn is 179.6° at 3.55 yd/s entering, which
is a plant, and the 3-cone's is 70.6° at 3.98. Even the drill named for a 45°
break lands its turns at 111° and 244°, with one player anywhere near 45°. A
drill that measured the 30–60° break at game speed would have to be built, not
re-scored, and the 3-cone's own cones and footprint are enough to build it.

That is the paper's argument. This repository is its code: it detects the turns,
draws the figures, and computes the reliability and outcome numbers — the
several-fold gap between Combine supply and game demand, the ICC of a player's
retention through the band, and the EPA and completion rate attached to each
band of turn.

## What you need

Four tables from the NFL Big Data Bowl release, plus the small player table that
ships with them, in `data/`:

| file | what it supplies |
| --- | --- |
| `combine_tracking.parquet` | Combine x/y tracking by drill and attempt |
| `combine_results.parquet` | each prospect's Combine position (the DB cohort) |
| `players.parquet` | `nfl_id`, `nfl_position`, `draft_year` |
| `game_tracking.parquet` | game x/y tracking (the full file, all seasons) |
| `player_play.parquet` | snap context, pass result, expected points added |

CSV is accepted too: `data/combine_tracking.csv` and so on. If the files live
somewhere else, set `BDB27_DATA` to that folder. Nothing else is required and
nothing is downloaded.

## Run it

```
pip install -r requirements.txt
python turns.py                # builds figures/turns.parquet and prints counts
python supply_demand.py        # Combine share vs game share, by band, plus table
python backpedal_polar.py      # every turn in the backpedal 45, on one dial
python drill_angle.py          # every Combine turn, sorted by drill
python drill_schematic.py      # the proposed drill's geometry (no data needed)
python reliability.py          # ICC(1), adjusted ICC, Spearman-Brown
python epa_bands.py            # EPA and completion by band, with no-turn baseline
```

That block is the whole pipeline. The order matters only in that `turns.py`
comes first: it builds the table everything else reads. Each line also runs on
its own.

Each figure script writes its PNG and the table behind it, as CSV, into
`figures/`; `reliability.py` prints its numbers. `turns.py` is the expensive one
and caches its output, so the rest are quick. Two sides are reported separately
throughout. If the game tracking file is not present, the scripts say so, print
the Combine numbers, and skip the game half rather than guessing at it.
