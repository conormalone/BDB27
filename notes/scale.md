# Scale probe — stage 02 (kinematics and turn detection)

Method: `scripts/scale_probe.py` builds worlds from the committed synthetic fixture
concatenated `N` times with renamed `nfl_id`/`event_id`/`game_id`/`play_id` (so each copy is
an independent player and game), runs the real stage CLI in `--mode sample`, and reads wall
time and peak RSS from that run's `outputs/run_log.csv`.

Host: Raspberry Pi (ARM64), Python 3.13, 8 GB RAM. Each row is one fresh process.

| copies | Combine rows | game rows | turns | wall (s) | peak RSS (MB) |
|---:|---:|---:|---:|---:|---:|
| 1 | 1,277 | 147 | 52 | 1.78 | 234.8 |
| 2 | 2,554 | 294 | 104 | 2.20 | 235.8 |
| 4 | 5,108 | 588 | 208 | 3.23 | 236.4 |
| 8 | 10,216 | 1,176 | 416 | 5.23 | 239.6 |
| 16 | 20,432 | 2,352 | 832 | 9.17 | 239.3 |

Memory is flat to within 5 MB across a 16× increase in rows; wall time is close to linear
(1.78 s → 9.17 s, i.e. ~0.55 s per copy after fixed startup). That is the intended shape:
turn rows stream to Parquet, game tracking is processed in 12-game chunks, and the only
whole-file object is the projected Combine frame.

For reference, the real `--mode sample` run (463k Combine rows, 1,048,575 game rows,
14,612 play-player segments) took **413 s and peaked at 399 MB**. The difference from the
fixture probe is the Combine frame itself (463k rows) plus per-chunk pandas overhead.

## Does it fit a full run on the Pi?

Roughly. Peak RSS is dominated by fixed interpreter/library overhead (~235 MB) plus the
projected Combine frame (the complete file is fixed at 463k rows, so it does not grow with
game data) plus `game_chunk_games` chunks. If the full game tracking is 3× the sample
(three seasons), peak should stay in the 400–600 MB band; if the sample is a 10% row slice
of one season and the full file is ~30×, the memory answer is unchanged because nothing
game-sized is held in RAM — only more wall time.

The wall-time risk is real: ~28 ms per play-player segment (six smoothers plus one null
pass). At 30× the sample (≈440k segments) the stage would need roughly 3.4 hours on this
Pi. **Recommendation:** run the full-data stage on the larger machine (TASK.md already says
the final run happens there), or raise `resources.duckdb_threads` and accept the wall time.

Watch items if the full run is attempted here:

* `resources.game_chunk_games` must stay small (12). Raising it to hundreds reintroduces a
  game-sized pandas frame.
* The summary step runs two DuckDB aggregations over the written Parquet, under
  `duckdb_memory_limit: 1200MB`; it does not load the turn table into pandas.
* `sanity.null_shuffle_reps` doubles the detector work per attempt; keep it at 1 unless the
  null has to be tightened.
