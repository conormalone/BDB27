# Spec — figure 1 from the uploaded full-data run (D185)

Status: **accepted for implementation, 2026-11-11**. Owner: PM.
Inputs verified by Main against the Windows run's own manifest.

## 1. Why

Figure 1 (`outputs/figures/fig_the_hole.png`) is the headline exhibit. Both of
its panels are drawn from `outputs/turns/turns.parquet`, whose **game** rows are
the 333-game, 1,048,575-row 2023 sample. Conor ran the turn-detection notebook
on the full three-season tracking files and sent back the artefacts. The game
side of figure 1 must now be the full data, and its provenance must say `FULL`,
not `PLACEHOLDER_SAMPLE`.

The Combine side does not move. `sample_files` in `config.yaml` overrides only
`game_tracking`; the Combine files were already read in full by the sample run.
The full run's own band table reproduces those Combine counts exactly, which is
the cross-check that the two sides belong together.

## 2. Source artefacts (pinned)

Directory: `data/interim/full_run_20261111/`

| file | bytes | sha256 |
|---|---:|---|
| `bdb27_full_db_turns.parquet` | 9,773,407 | `f2e7cd01db214b7d88ab3f029876fe0fe7ced1530d2f014b9ad55b1b2dfb8e04` |
| `bdb27_full_game_bands.csv` | 4,249 | `7cd10b9db2b9ccae21099c2bab8fe5d5f1420e295aec6e9dc09ed2c6ade454a1` |
| `bdb27_full_game_manifest.json` | 35,281 | `dc8888a0e98d0d67a165106c29638fbe75f6b29d8dbce9a77e87c3dcc3c9de73` |

The manifest's own `artefacts` block records the first two checksums, so the
three files are one run. It also records `config_sha a4b45b6f476ddae5` and
`git_rev aeb483c`. This host's `config.yaml` has the same fingerprint, and the
detection sources are unchanged from `aeb483c` (checked in code, §6).

## 3. What the artefact contains, and what it does not

- `bdb27_full_db_turns.parquet`: **game turns only** (`setting == "game"`),
  **DB only** (`position_group == "DB"`), 58,665 rows, 65 columns, 58,664
  valid, `season` in {2023, 2024, 2025}. Per-season valid DB turns 8,016 /
  21,275 / 29,373. 115 players, 2,234 player-games. 7,777 valid turns in
  30–60°.
- `bdb27_full_game_bands.csv`: the notebook's own band table, all position
  groups × six bands × both settings.
- No other stage-03/04/05 product was produced by the full run. Anything that
  needs the full envelope, demand map or coverage table is therefore **not**
  unblocked by this upload.

Figure 1 only needs per-turn `setting`, `position_group`, `valid`,
`abs_turn_angle`, `snap_type`, `nfl_id`, `game_id` — all present.

## 4. Decision — how the full artefact enters the pipeline

A new, single-purpose renderer: `src/07_full_figure1.py` with module
`src/bdb27/full_run.py`.

- It **assembles** figure 1's turn table from two sources:
  1. every row of `bdb27_full_db_turns.parquet` (game, DB), and
  2. the Combine-DB rows of the existing `outputs/turns/turns.parquet`
     (`setting == "combine"`, `position_group == "DB"`).
- It then calls the **existing** `figures.band_table` → `figures.build_plot`
  → `figures.render` path, with edges from `figures.hole.plot_bands`. No
  drawing logic is duplicated and no figure contract is changed.
- It writes into the **canonical** `outputs/figures/`:
  - `fig_the_hole.png` (redrawn from the full data),
  - `fig_the_hole.csv` with `provenance=FULL`, `label=FULL DATA OUTPUT`,
  - `fig_the_hole.provenance.json` (source artefacts, checksums, cross-checks,
    season coverage),
  - `fig_the_hole.drawn_text.json` (the rendered-text dump),
  - and updates the `fig_the_hole` entry in `figures_manifest.json`.
- **The sample path is untouched.** `src/06_figures.py --mode sample` still
  renders all 18 figures exactly as before; a later sample run would simply
  overwrite the PNG/CSV again. Nothing in `figures.py`, `common.py` or
  `06_figures.py` changes behaviour.

Why a separate entry point rather than `06_figures.py --mode full`: full mode
there correctly refuses without FULL thresholds and `notes/prereg.md`. Neither
exists in this upload, and faking them to unlock a figure would be dishonest.
The uploaded artefact is a turn table, not a re-run of stages 00–05.

## 5. Decision — provenance and labelling

- The CSV uses `common.write_csv(..., mode="full")`: `FULL` / `FULL DATA OUTPUT`
  in the leading columns.
- The manifest entry carries `"provenance": "FULL"`, `"stamp": "FULL DATA
  OUTPUT"`, and `"sources"` naming the three uploaded artefacts (plus the
  Combine source).
- The manifest itself now has mixed entries (figure 1 FULL; the other 17 are
  still sample). Its top level therefore says
  `"provenance": "MIXED"` and a label that states the split — it must not
  claim the whole manifest is sample, nor that it is all full.
- `fig_the_hole.provenance.json` is the dedicated provenance record: every
  source file with bytes and sha256, the run manifest's `git_rev`/`config_sha`
  /`runtime_seconds`, the cross-check results, and the season coverage.

## 6. Refusals (the renderer must stop, not guess)

1. Any source file's sha256 differs from the pin in §2.
2. The parquet is not game-only and DB-only, or `valid_db_turns`,
   `valid_db_30_60`, `valid_db_players`, `valid_db_player_games` disagree with
   the manifest's own `counts`.
3. The assembled band table does not match `bdb27_full_game_bands.csv` for
   **DB**, band by band and setting by setting (count and share).
4. The Combine side does not reproduce 1,179 total / 26 in 30–60°.
5. `config.yaml`'s fingerprint differs from the manifest's `config_sha`, or the
   detection sources (`src/bdb27/figures.py`, `common.py`, `turns.py`,
   `kinematics.py`, `geometry.py`, `envelope.py`, `src/02_turns.py`) differ
   from `git_rev aeb483c`.
6. Figure 1's own guard fires: 30–60° is not the largest proportional
   shortfall among the bands drawn.

## 7. Per-season coverage — decision

The figure stays **pooled**, because that is what figure 1 measures (DB supply
versus demand by angle band) and because a season facet would change the
drawing contract D183 fixed. The split lives in the **manifest and the
provenance record**, not on the image:

- `season_coverage.by_season` gives valid DB turns, players and games per
  season;
- a caveat states 8,016 / 21,275 / 29,373 and that the 2023 tracking file has
  roughly a third of the 2025 file's rows for the same number of games, so
  season-level rates are not comparable and the pooled figure is what is
  reported. That unevenness is an open question with Conor; this work does not
  resolve it.

## 8. Drawing contract (unchanged, D183)

Heading + data only. No subtitle, no caption, no red callouts, no on-image
provenance banner. The rendered-text dump must contain only the heading, the
panel strips, the axis tick labels, the legend labels, the focus-band direct
labels and the two peak labels.

## 9. Acceptance criteria

1. `fig_the_hole` redrawn from the full artefact: 30–60° game **7,777 /
   58,664 = 13.26%**, Combine **26 / 1,179 = 2.21%**, headline **6.0×**
   (6.011 exact), DB only, valid turns only.
2. The 30–60° band is still the largest proportional shortfall (next is
   90–120° at 1.65×); the figure's own guard passes.
3. `fig_the_hole.csv` has every row stamped `FULL` / `FULL DATA OUTPUT`;
   the manifest entry says `FULL DATA OUTPUT`; the dedicated provenance file
   names all three uploaded artefacts with their sha256 and sizes.
4. A rendered-text dump proves heading and data only, with no banned token.
5. The sample path still renders and still stamps `PLACEHOLDER_SAMPLE`; the
   full test suite is green.
6. Determinism: two full renders produce a byte-identical PNG.
7. Commit and push; report the SHA (`== origin`), the numbers, the proof and
   what was deliberately left out.

## 10. Explicitly out of scope tonight

- **Figure 5 (all positions)**: the upload contains DB game turns only; the
  other positions' full game turns were not exported.
- **Figure 6 (measurement limits)**: needs the full stage-03
  `variance_components.csv`; the upload has no envelope output.
- **Figures 7–17**: all are DB-only and could in principle be redrawn from the
  same turn table, but their contracts include stage-04/05 products, bootstrap
  intervals or other artefacts the full run did not produce. Re-rendering them
  from a half-full input would mix a FULL game side with SAMPLE-derived stage
  products under a FULL stamp. That is the failure mode this whole task exists
  to prevent, so they stay sample-stamped until their full inputs exist.
- **Figures 2–4**: Combine-only or schematic. They are not blocked by the
  game sample (figure 2's numbers already come from the full Combine file), so
  there is nothing for this upload to fix.
