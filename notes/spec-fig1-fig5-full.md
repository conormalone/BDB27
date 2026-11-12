# Spec — figure 1 proportions-only and figure 5 full rebuild (2026-11-11)

Status: **accepted for implementation**. Owner: PM. Inputs verified by Main
against the Windows run's own manifest (D185).

## 1. The two asks (Conor, 2026-11-11)

1. **Figure 1, 23:04.** *"Ok, for figure 1 the numbers are too skewered for the
   counts, so do it again with just the proportions, the right hand side."*
   The raw-count panel is removed; the share panel is kept, on its own, full
   width. The full-data numbers already committed are unchanged: 7,777 /
   58,664 game against 26 / 1,179 Combine, 6.0×.
2. **Figure 5, 22:54.** *"Redo figure 5 as well."* Still outstanding;
   `outputs/figures/fig_all_positions.csv` read `PLACEHOLDER_SAMPLE`. The
   uploaded bands table carries every position group, so the drawn counts and
   shares are rebuildable without per-turn rows.

## 2. Sources (pinned, unchanged from D185)

`data/interim/full_run_20261111/`:

| file | bytes | sha256 |
|---|---:|---|
| `bdb27_full_db_turns.parquet` | 9,773,407 | `f2e7cd01db214b7d88ab3f029876fe0fe7ced1530d2f014b9ad55b1b2dfb8e04` |
| `bdb27_full_game_bands.csv` | 4,249 | `7cd10b9db2b9ccae21099c2bab8fe5d5f1420e295aec6e9dc09ed2c6ade454a1` |
| `bdb27_full_game_manifest.json` | 35,281 | `dc8888a0e98d0d67a165106c29638fbe75f6b29d8dbce9a77e87c3dcc3c9de73` |

`config_sha a4b45b6f476ddae5`, `git_rev aeb483c`, runtime 6,660 s. The config
fingerprint and thresholds are unchanged by this work; **no `config.yaml` edit
is permitted**, because the fingerprint is the upload's identity. Figure
layout decisions therefore live in code, not config.

## 3. Figure 1 — one proportions panel

- **One panel only.** x = the six figure-1 display bands (30–60°, 60–90°,
  90–120°, 120–150°, 150–180°, >180°); y = each setting's own share of valid
  turns (%). Two bars per band: Combine, Game.
- **The raw-count panel is removed from the figure and from the code path.**
  `plot_frame` no longer constructs a counts panel; there is no facet wrap, no
  `(a)`/`(b)` strip, and no count label on the canvas. Counts and denominators
  stay in the CSV and the manifest as the data record.
- **Full width.** Uses `figures.hole.width_in` × `figures.hole.height_in`
  (11.5 × 7.6 in at 150 dpi) — the full configured canvas for the single panel.
- **Axis limits.** y 0–50%, ticks every 10 percentage points. A non-default
  band set whose largest share exceeds 50% widens the axis to the next 10-point
  ceiling rather than clipping a bar; only a "share" above 100% refuses as an
  inconsistent table. Axis title: `Share of turns within setting (%)` — the old
  panel (b) meaning, kept as the axis label now that the facet strip is gone.
- **Label treatment.** No bar labels. D183 removed the tiny percentage marks
  from this panel, and the count labels belonged to the removed panel. Band
  tick labels, the colour legend and the focus-band tint/rules remain.
- **Heading.** Unchanged wording and numbers: *"The hole: games demand 6.0× the
  share of 30–60° DB breaks the Combine supplies"* (6.5× on the sample run).
- **Drawing contract.** Heading and data only: no subtitle, caption, red
  callout, EXPLORATORY mark, in-plot band tag, or provenance banner.

## 4. Figure 5 — six panels from the uploaded band table

- **Six position groups**: `DB, WR, TE, OL, DL, OTHER` (configured order, OTHER
  appended). OTHER is the deliberate all-position exception: it is unclassified
  and exists only on the game side.
- **Source.** Every drawn count, denominator and share is read from
  `bdb27_full_game_bands.csv`. No per-turn rows are needed for the drawing.
- **What genuinely needs per-turn data, and is therefore not invented:**
  - player exposure (`n_players`, `players_by_setting`) for the non-DB game
    positions — absent from the uploaded band table, so those CSV cells stay
    blank; DB game players (115) come from the uploaded DB turn table and
    Combine players from `outputs/turns/turns.parquet`;
  - player-games (recorded for DB only, 2,234);
  - snap-type composition — not drawn by figure 5.
- **OTHER has no Combine turns.** Its panel is drawn game-only (six bars) and
  is excluded from the heading/claim wording by name: the heading reads *"…in
  every position it tests"* for this six-group figure. The sample figure (five
  tested groups) keeps *"…in every panel"*.
- **Drawing contract (D183 trimmed).** Heading, bars, band rows, counts and
  legend. No subtitle, caption, band callout or EXPLORATORY badge. Counts stay
  on the accent (DB) panel only. Shared x axis 0–70%.

## 5. Provenance

Both figures write FULL artefacts into `outputs/figures/`:

- CSV with `provenance=FULL`, `label=FULL DATA OUTPUT`, `stamp=FULL DATA
  OUTPUT`;
- a manifest entry with `provenance: FULL`, the three pinned source artefacts
  and their checksums;
- a dedicated `*.provenance.json` naming each source file with bytes/sha256,
  the run's `git_rev`/`config_sha`/runtime, the cross-checks and (figure 1) the
  per-season coverage;
- a `*.drawn_text.json` dump of the visible strings plus a banned-token scan.

`figures_manifest.json` therefore becomes **MIXED**: 2 FULL (figures 1 and 5),
15 sample, 1 schematic. It must never claim to be wholly FULL or wholly sample.

## 6. Refusals

Figure 1 keeps every D185 refusal (pins, manifest counts, band-table
cross-check, Combine 1,179/26, config/detection identity, largest-shortfall
guard) plus a guard against a "share" above 100%.

Figure 5 refuses when:

1. any source file's checksum differs from its pin;
2. the parquet is not game-only/DB-only or disagrees with the manifest's counts;
3. the uploaded bands do not match the figure's display bands;
4. a configured position is absent from the upload;
5. a position other than OTHER has no Combine rows;
6. a share is not exactly `count / setting_total`, or a position's band counts
   do not reconstruct its uploaded denominator;
7. the uploaded Combine rows do not equal the Combine turn table band for band;
8. the uploaded DB game rows do not equal figure 1's assembled DB table;
9. the config fingerprint or the remaining detection sources differ from
   `aeb483c`.

`src/bdb27/figures.py` is removed from `DETECTION_FILES`: it draws and
aggregates, it does not detect turns, and its band assignment is proved by the
row-for-row band-table cross-check. Without this, any drawing change would be
reported as detection drift.

## 7. Sample path

`06_figures.py --mode sample` still runs end to end and still stamps
`PLACEHOLDER_SAMPLE`. Figure 1's sample drawing changes shape (one proportions
panel, as asked); figure 5's sample drawing keeps its five configured positions
and its byte-stable behaviour. No config key changes, so the sample and FULL
fingerprints stay identical.

## 8. Acceptance criteria

- Figure 1 renders **one** axes; drawn text is the heading, the y-axis title,
  legend, six band ticks and 0–50 ticks — no `(a)`, no `(b)`, no counts panel,
  no count label.
- Figure 5 renders **six** axes; OTHER draws six game bars and no Combine bar;
  every one of the 66 drawn rows equals its uploaded row.
- Both figures' CSVs are FULL in every row; both manifest entries are FULL with
  the three pinned artefacts; both drawn-text dumps scan clean.
- Figure 5's Combine rows equal the Combine turn table exactly; its DB game
  rows equal figure 1's assembled DB table (12 rows).
- The sample figure-1/figure-5 tests and the whole suite are green.
- Two renders of each figure are byte-identical.
- Commit and push; report the SHA, what each figure shows, and what was left
  out (player exposure for non-DB game positions) and why.
