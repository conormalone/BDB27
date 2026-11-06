# FULL-provenance synthetic fixture

**This is a smoke test, not a result.** No full-data run has happened in this
repository. These files exist for one reason: before this fixture, the figure
renderer had only ever exercised its skip path and the writeup renderer had never
filled a token, so "the renderers work" was an untested claim.

The fixture is stamped `provenance: "FULL"` because both renderers refuse
anything else **by design** — that refusal is the point of the provenance rule,
and it cannot be tested without something that passes it. To keep the stamp from
being mistaken for evidence, every JSON carries:

```json
"fixture": true,
"fixture_note": "SYNTHETIC FIXTURE — smoke test for the renderers. Not a BDB27 result; no full-data run produced these values."
```

`writeup_render.report()` prints a warning when it sees that flag.

## What is planted

Six fictional rushers (ids 9001–9006). Each has a Combine-style curve
`t_i(d) = k_i · d^0.72` and a jump `u_i`; each rep's crossing time is
`t_i(depth) + u_i + noise`, with depth drawn around 0.66 yd and clipped to
0.35–1.40 yd. Fixed seed `20270106`, so the CSVs are reproducible.

The variance-split numbers in `decomposition.fixture.json` are **illustrative
constants** chosen to exercise the plot paths (a non-zero phys share, intervals
that bracket it, non-identical edge/interior). They are **not** recovered from
the generated reps and mean nothing. `validation.fixture.json` is likewise
shaped to exercise the writeup tokens, not to be internally consistent.

## Files

| file | role |
|---|---|
| `fixture.py` | `build()` regenerates the committed files; `materialise()` lays them out as an `outputs/` tree (parquet built in temp, never committed) |
| `decomposition.fixture.json` | shape of `outputs/phase4/decomposition.json` |
| `validation.fixture.json` | shape of `outputs/validation/validation.json` |
| `snaps.fixture.csv` | rep-level rows; materialised as `phase3/snaps.parquet` |
| `physical_time_by_player.fixture.csv` | per-player distance–time curves |
| `player_effects.fixture.csv` | per-player `u_s` for the figure-4 annotation |

## How it is used

`tests/conftest.py` exposes `full_outputs`, which materialises the fixture into
`tmp_path`. `tests/test_figures_full_fixture.py` proves all four figures render
with finite axes and non-empty PNGs, and that they still skip on sample
provenance. `tests/test_writeup_render.py` proves the renderer fills every token
from a FULL artefact and names every field it cannot fill.

Regenerate only when the fixture itself changes:

```bash
python fixtures/synthetic/full_provenance/fixture.py
```
