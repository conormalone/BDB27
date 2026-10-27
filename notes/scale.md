# Scale, memory and storage — leverage × ceiling task

**Measured 2026-10-26** on the Raspberry Pi 4 (4,049 MB RAM, 2,047 MB swap,
4 cores; root eMMC + USB SSD). Values below are measurements, not guesses,
unless explicitly labelled **estimate**.

## 1. Storage layout

| mount | size | used | free at 21:27 | free at 22:45 (guard) | role |
|---|---:|---:|---:|---:|---|
| `/` (eMMC, `data/raw` is *not* here) | 6.9 GB | 6.5 GB (99 %) | **104 MB** | **98.1 MB** | repo, code, notes, `outputs/` |
| `/mnt/project_data` (USB) | 28.9 GB | 18.5 GB (68 %) | **8,867 MB** | **8,826 MB** | `data/raw` and `data/interim` |

`data/raw` and `data/interim` are symlinks to the USB. Nothing bulky may be
written to `/`: the root eMMC has < 110 MB free, and `/tmp` is on it.

**Disk guard.** `lc_common.assert_disk_space` measures the two volumes
separately (`shutil.disk_usage(REPO_ROOT)` and
`shutil.disk_usage(paths["interim"])`, never `disk_usage("/")` for the USB),
and refuses to run when either free value is below
`runtime.min_free_mb_root = 40` / `min_free_mb_interim = 2048`. The failure
message prints both actual free MB values and both paths. Tests force the
failure with impossible thresholds and check the numbers appear.

**TMPDIR redirect.** `lc_common.duckdb_connect` sets `TMPDIR`, `TMP`, `TEMP`,
`tempfile.tempdir` and DuckDB `temp_directory` to `data/interim/tmp` (USB).
Without this, a DuckDB spill or numpy temp file would land on the 100 MB root
tmpfs and fail the whole run. This is tested.

## 2. Measured stages (sample data)

Measurements are the two rows appended to `outputs/run_log.csv` with the new
header (`stage,mode,provenance,wall_s,peak_rss_mb,when,notes`). Peak RSS is
sampled every 0.2 s by `timed_stage`; wall time is `perf_counter`.

| stage | input | wall | peak RSS | output |
|---|---|---:|---:|---|
| `measure_parquet_game_tracking` | `game_tracking_2023_sample.csv` (93.7 MB, 1,048,575 rows, 12 cols) | **5.95 s** | **209.8 MB** | 17,083,303 B parquet (17.1 MB; 5.5:1) |
| `measure_scan_player_play` | `player_play.csv` (146.9 MB, 314,197 rows) | **1.84 s** | **180.5 MB** | count + distinct-play aggregate |

A first, back-to-back run of the same parquet conversion took 2.10 s at
191.0 MB; the 5.95 s figure is the logged run (cold page cache / concurrent
load). Treat ~2–6 s and ~190–210 MB as the working range for a 94 MB CSV with
DuckDB single-threaded.

The legacy `outputs/run_log.csv` used a different header
(`timestamp,stage,mode,wall_s,peak_ram_mb,error,notes`). It was preserved
unchanged as `outputs/run_log_legacy_2026-10-26.csv` so the new log has a
coherent single header; this is recorded in `notes/decisions.md`.

## 3. Per-stage expectations on the full data (estimates)

Assumptions, stated so they can be checked: full tracking covers **1,001 games
/ 114,954 plays** (from `player_play.csv`, all three seasons), all 22 players
are tracked per play (PENDING FULL RUN), and each player-play has ~72 frames at
10 Hz (the sample has 1,048,575 rows / 12,638 plays / 1.156 players per play
= 71.8 frames per player-play).

**Full tracking row estimate**

```
114,954 plays × 22 players × 71.8 frames ≈ 181.6 M rows
89.4 bytes/row (93,739,461 / 1,048,575)      ≈ 16.2 GB CSV
÷ 5.5 compression (measured parquet ratio)    ≈ 2.9 GB parquet
```

If the full data tracks fewer than 22 players per play (the sample cannot tell
us), scale linearly: 11 players/play ⇒ ~91 M rows, ~8.1 GB CSV, ~1.5 GB parquet.
Either way it does **not** fit in RAM and must be processed per game.

| stage | reads | expected wall (full) | expected peak RSS | risk |
|---|---|---:|---:|---|
| `00_thresholds --mode full` | one `count(*)` over full tracking (16 GB CSV) | 3–8 min | < 250 MB | IO-bound gate; streams, no materialisation |
| `01_audit` | Combine 84 MB + tracking per game | 20–45 min | < 1.2 GB | must chunk by `game_id`; never `SELECT *` all games |
| `02_combine_ceiling` | Combine 84 MB (fits in RAM) | 1–3 min | < 600 MB | fine on this Pi |
| `03_game_efforts` | tracking per game, writes one row per effort | 30–90 min | < 1.2 GB | the heavy stage; per-game checkpointing mandatory |
| `04_models` | effort summary (~0.3–0.5 M rows) | 2–10 min | < 1 GB | statsmodels mixed models are CPU-bound |
| `05_validate` | effort summary + permutations | 5–20 min | < 1 GB | permutation reps from config |

`runtime.duckdb_memory_limit = 1200MB` and `threads = 1` are deliberate: the
box has ~1.8 GB available and ~2 GB swap already nearly full. Single-threaded
reductions also keep results deterministic. If a stage approaches 1.2 GB,
DuckDB spills to `data/interim/tmp` rather than dying.

## 4. Disk budget for the full run

* Full tracking CSV: ~16 GB, lives on the USB **as provided** (read-only; we do
  not copy it).
* Parquet cache: ~2.9 GB under `data/interim/parquet` (USB).
* DuckDB temp/spill: cap by free space; 8.8 GB free minus 2.9 GB parquet
  leaves ~5.9 GB. A full-stage spill must not exceed that; per-game chunking
  keeps spills small.
* Effort summary + model outputs: < 100 MB under `outputs/` and
  `data/interim/summaries/`.
* **Do not** write the 16 GB CSV a second time anywhere; convert per season
  and delete the intermediate parquet only if space demands it (it is a cache,
  reproducible from raw).

## 5. Sample vs full — what the sample cannot tell us

The sample is row-capped at 2²⁰−1 rows **and** effectively player-capped
(154 players, 1.16 per play). It therefore cannot estimate:

* true frames per player-play in the full file (the 71.8 figure is sample-only);
* whether all 22 players are present per play;
* per-receiver effort counts, leverage-bin occupancy, or matched-pair yield;
* full-file row count (the 181.6 M estimate is arithmetic on assumptions).

It can and does exercise schema/parse, per-frame kinematics, event alignment,
WP joins, parquet conversion time/RAM, and the disk guard. All sample outputs
are stamped `UNVALIDATED SAMPLE OUTPUT` and are never reported as results.

## 6. Phases 1–2 measured on the real provided files (2026-10-27, D60)

Two new stages were measured with the new run log. They read the complete Combine file and the complete
`player_play.csv`; no tracking parquet was built.

| stage | input | wall | peak RSS | output |
|---|---|---:|---:|---|
| `01_audit --mode sample` | combine_tracking 84 MB + player_play 147 MB + games + sample tracking | **16.3–32.4 s** (cold cache 32.4 s; warm 16–19 s) | **314–334 MB** | 10 CSVs + decision + summary (all < 100 kB) |
| `02_combine_ceiling --mode sample` | combine_tracking 84 MB | **3.9–28.2 s** (cold cache 28.2 s; warm 3.9 s) | **205–208 MB** | 58 kB CSV + 0.8 kB JSON |

Both are far below the 1.2 GB DuckDB cap. `01_audit` is IO/aggregate-bound (five `player_play` scans);
`02` is CPU-bound on 238 least-squares fits. Full-data extrapolation is unchanged from §3: the audit's
tracking items (g/i) will dominate and must be chunked per game; the Combine-side ceiling fit does not
grow with game data and stays ~4 s / 205 MB. Root disk was not stressed (all artefacts are small; DuckDB
temp is redirected to the USB).
