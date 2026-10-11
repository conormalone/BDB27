# Scale & memory notes — BDB27

Target device: **Raspberry Pi 5, arm64, 4 GB RAM**. Numbers below are measured on the provided data
(2026-10-09, retained 2026-10-11). Stage-level measurements from the prior (combine-fatigue) pipeline are
archived at tag `archive/combine-fatigue-v1` — the new stages are not implemented yet.

## Measured — data conversion (still valid)

| Artefact | Input size | Output size | Note |
|---|---:|---:|---|
| `combine_tracking.csv` → Parquet (ZSTD) | **83.8 MB** (463,189 rows) | **14.2 MB** (5.9× smaller) | combine asset; fits comfortably in RAM |
| `combine_results.csv` → Parquet | 41 KB (510 rows) | 14 KB | small |

The **complete Combine file is small** (14.2 MB Parquet) — the whole Combine side of this task
(`02_combine_envelope`) is a **compute** problem, not a RAM problem, and runs on the Pi.

## Sample discipline

`game_tracking_2023_sample.csv` = **1,048,575 rows (2²⁰ − 1), 93.7 MB** — a non-representative
**first-N-rows prefix** (see `notes/schema.md` §8). It exercises only the I/O plumbing of the game side,
never the game-side science. All sample outputs are stamped `UNVALIDATED SAMPLE OUTPUT`.

## Extrapolation to full data (game side)

| Data | Rows | CSV size |
|---|---:|---:|
| `game_tracking_2023_sample.csv` (this device) | 1,048,575 (2²⁰−1) | 93.7 MB |
| One full season (2025, per prior-work scan) | ~4.3 M | ~975 MiB |
| **Three seasons (2023+24+25)** | **~13 M** | **~2.9 GB** |
| `player_play.csv` (per-play context) | 327,019 | 140.1 MiB |

**Rule:** the game side must be processed **per game / per player** and reduced to small summary tables;
modelling stages read only those summaries. **Never** `pd.read_csv` a full game-tracking file.

## Stages that risk exceeding RAM if done naively

1. **`03_game_demand`** — the biggest risk. Stream Parquet with DuckDB and aggregate per
   `(game_id, nfl_id)`; emit a compact per-player-per-game effort table, not frame-level rows.
2. **`00_thresholds`** — scans all frames; compute quantiles via DuckDB streaming, not by materialising
   frames.
3. **Audit (e) full mode** — a `SELECT DISTINCT nfl_id` over ~13 M rows; DuckDB streams this, but the
   Parquet conversion of the ~2.9 GB CSVs is the slow (disk-bound, low-RAM) step.

**Extrapolated peak:** ≈ sample peak + (rows_full / rows_sample) × (frame working set). With per-game
chunking the working set stays ~O(one game), so peak should stay **< 1 GB**. Re-measure on the big
machine and update this file — see `pending_full_run.md` P8.
