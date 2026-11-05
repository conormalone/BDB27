# Scale — what this runs on, and what it will do on full data

Measured on the Raspberry Pi (4 GB RAM, 4 cores, ARM64, USB 3 SSD for `data/`).
Numbers are from `outputs/run_log.csv`, which every stage appends to.

## Measured (sample mode, 2026-11-05)

| stage | input | wall | peak RSS |
|---|---|---:|---:|
| `01_audit` | 1,048,575 tracking rows + 314,197 `player_play` + 463,189 combine | **42.8 s** | **439 MB** |

The audit is I/O-bound on CSV scanning: DuckDB re-reads the 94 MB sample once for
the schema/unit gate and once per 25-game batch for the definition check.

`data/interim` after a sample run: **0 MB** — the audit writes only CSVs and JSON
under `outputs/audit/` (a few hundred kB total). It builds no Parquet cache yet.

## What full data will look like

The sample is a **player-in-play** draw: 1–4 of 22 players, on 1–90 of ~150 plays
per game (`notes/schema.md` §3). So rows per game are not representative.

| quantity | sample | full (derived) |
|---|---:|---:|
| tracking rows per game | 3,149 | **~120,000–170,000** |
| ratio | 1× | **~40–55×** |
| games | 333 | 1,002 (3 seasons) |
| total tracking rows | 1.05 M | **~120–170 M** |
| CSV size | 94 MB | **~11–15 GB** |
| bytes per row | 89 | 89 (same schema) |

The per-game row estimate assumes 22 players × 120–140 plays × 45–55 frames per
play. It is an **extrapolation, not a measurement**; the first thing the full run
should do is replace it with the real numbers in `outputs/run_log.csv`.

## Stages and their scaling risk

| stage | pattern | expected peak | risk |
|---|---|---:|---|
| `01_audit` | CSV scans + 25-game pandas batches | 0.4–1.5 GB | **low** — bounded by the batch, not the file |
| `02_combine_physical_time` | Combine only (463 k rows, 84 MB) | 0.5 GB | **low** — fits in RAM on the Pi today |
| `03_game_snaps` | per-game streaming → one row per snap | 0.8–2.0 GB | **medium** — see below |
| `04_models` | reads the per-snap summary only | 1–3 GB | **low** on a bigger machine; PyMC/Stan is the real cost |
| `05_validate` | re-reads summaries + refits | 1–3 GB | **low** |

### `03_game_snaps` is the RAM risk

At ~170 M tracking rows, **never** load the file. The stage must:

1. convert each season's CSV to Parquet once (`common.to_parquet`, explicit column
   list, zstd) — this is a streaming `COPY`, so peak RAM is DuckDB's buffer
   (1.2 GB limit), not the file size;
2. iterate **per game** with a DuckDB query filtered on `game_id`;
3. write one row per snap to `data/interim/snaps/*.parquet` in append mode.

Parquet at zstd should land around **2–4 GB** for the three seasons — it fits on
the 7 GB free on the USB, but only just. Convert one season at a time and delete
the previous one's is **not** acceptable (the modelling stage needs all three);
instead budget ~4 GB and check `du` after each season.

### Guard rails already in the code

* `common.assert_disk_space` refuses to start below 1,024 MB free on the interim
  volume and 40 MB on root.
* `common.duckdb_connect` sets `memory_limit` and redirects `TMPDIR` to the USB,
  so a spill cannot fill the 640 MB root filesystem.
* `common.chunk_rows` (250,000) is the batch size for streaming stages.
* The audit's definition check batches 25 games at a time, so the same code path
  works on 1 M and on 170 M rows.

## Runtime guess for a full run

Not measured; stated as an order of magnitude so nobody is surprised. Using the
sample's throughput of ~24,000 tracking rows/s (1.05 M rows / 43 s):

* Parquet conversion of 170 M rows: **~2 h** on this Pi, **~15 min** on a laptop;
* `03_game_snaps` over 1,002 games: **~3–6 h** on the Pi;
* `04_models` with PyMC: **hours**, and it belongs on the bigger machine.

This is why `TASK.md` says the final run happens elsewhere. Nothing in the code
requires the Pi.

## Extrapolation check that has *not* been run

`TASK.md` Tests item 4 asks for "the sample concatenated several times with
renamed IDs" to check roughly linear memory growth. That was **not** run in this
cycle because Phase 3 (the stage that would consume it) is a stub. It is recorded
in `notes/pending_full_run.md` as **S1** with the exact command, and must be run
before the first full-data attempt — it is the cheapest way to discover a
quadratic join on the big machine instead of at hour four.
