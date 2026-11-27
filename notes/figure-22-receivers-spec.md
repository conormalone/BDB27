# Figure 22 — the receiver ladder (`fig_population_receivers`)

**Authority.** Conor, 2026-11-26 22:16, on the 18 Nov prototype: *"Rebuild it."* Approved scope is
this one figure rebuilt as a first-class committed artefact (PNG + CSV + drawn-text dump +
`.provenance.json` + manifest entry), from committed data, with every bar traced to a named artefact
and field and printed with its denominator. Nothing broader: no restructuring, no other figure, no
`TASK.md`, no `notes/report-draft.md`, no data-engineering module, no new config key.

**Branch.** `fig22-population-receivers`, cut from `02cf531` (main). Do not merge or push to `main`.

## 1. What the lost prototype did (recovered, not guessed)

The scratch dir is gone. The sources are the sent JPEG
(`/home/conormalone/.openclaw/media/outbound/pop_receivers---921ae5c1-…jpg`) and the 18 Nov session
transcript, which still holds the script (`pop20.py`, sed-edited into `pop21.py` and saved as
`pop_receivers.png` at 18:40). Its literals, verbatim:

```
rows  = [("All DB turns of 30–60°", 7777, 6315),
         ("A listing opponent who ran a route", 6315, 3009),
         ("That route has a separation", 3009, 2601)]
ident = [("1", 1882), ("2", 534), ("3", 137), ("4", 5)]
N_ID, N_NEAR, TRACK_ID, TRACK_NEAR, UNMEAS = 2558, 413, 2601, 2558, 151
```
labels of the last two bars: `Number of receivers` (segmented 1/2/3/4, small numbers above with
leader lines) and `Receiver within 3 yd`, with the text `(+151 not measured)` beside it; title
`What survives each step, by how many men were on the play`; x label `DB turns of 30–60° (n)`;
track `#f0e4bd`; segment shades `#f6dd8a / #e8b100 / #b08600 / #6f5400`.

**The last number is 413, not 418.** 418 is a pixel width in the *reference* image Conor sent to
copy (a different chart, bars 878/418/361/241/65 px). Nothing in the prototype or the data says 418.

## 2. The ladder, and where every number comes from

PM verified each line below on this machine against the received pastes, before this spec was
written. The coder must reproduce these numbers from the files and **print** each with its
denominator and source; a mismatch is printed and raises only where §5 says it is a real
contradiction.

| bar | state (drawn) | entered | survives | source |
|---|---|---:|---:|---|
| 1 | All DB turns of 30–60° | 7,777 | 6,315 | `outputs/ambiguity_attribution/funnel.csv` `band_row`: `band_turns` 7,777 − `no_opposing_player_play_row` 1,462 |
| 2 | A listing opponent who ran a route | 6,315 | 3,009 | same file: `turns_with_opposing_route_runner` |
| 3 | That route has a separation | 3,009 | 2,601 | same file: `turns_with_a_candidate_separation` |
| 4 | Number of receivers | 2,601 | **2,558** | `outputs/ambiguity_attribution_received/precedence.csv`: `N` (2,558 of 7,777); segments from the received `geometry.csv` + `route_a_turns.csv` (§3) |
| 5 | Receiver within 3 yd | 2,558 | **413** | 213 from `outputs/population_shrink_received/single_turn_geometry.csv` (`within_radius == True`) + 200 from the received `geometry.csv` union (§3); `+151` = 2,558 − 1,731 − 676 |

Bar-4 segments (men on the play, from the identified multi-candidate union):
`1 → 1,882` (= 1,731 rule turns + 151 single-candidate turns recovered from the separation-less
pool), `2 → 534`, `3 → 137`, `4 → 5` (sum 2,558).

Bar-5 detail: within 3 yd among the same union, by men on the play: `2 → 148`, `3 → 51`, `4 → 1`
(= 200); plus the 213 rule turns at one candidate = **413**. The `+151` are identified turns whose
distance was computed in the run but **not exported to any table**, so they cannot be counted in or
out. Wording on canvas: `(+151 without an exported distance)` — see §6.

## 3. Derivation from the two newly committed received pastes (exact)

`geometry.csv` — 870 rows, one per turn with ≥2 visible candidates (`n_candidates` 2/3/4 =
704/161/5), columns include `dist_nearest_yd`, `dist_second_yd`, `nearest_nfl_id`, `geometry_status`
(869 `tracked`, 1 `break_frame_unrecovered`).

```
geo5      = (dist_nearest_yd <= 5) & (dist_second_yd > 5)                       -> 326
geo8      = (dist_nearest_yd > 5) & (dist_nearest_yd <= 8)
            & ((dist_second_yd > 8) | dist_second_yd.isna())                    -> 129
geometry  = geo5 | geo8                                                         -> 455
route_a   = key in route_a_turns.csv (564 rows; 531 of them inside the 870)     -> 532
union     = geometry | route_a                                                  -> 676
overlap   = geometry & route_a                                                  -> 311
segments  = union.groupby(n_candidates)          2 -> 534, 3 -> 137, 4 -> 5
within3   = union & (dist_nearest_yd <= 3)       2 -> 148, 3 -> 51, 4 -> 1      -> 200
```

`single_turn_geometry.csv` (1,731 rows, already committed and pinned): `within_radius == True`
→ **213**.

Residual: `outside = N − N_frozen − union = 2,558 − 1,731 − 676 = 151`, and the `1` segment is
`N_frozen + outside = 1,882`. Print both as residuals with the three inputs named.

Identity checks to print (all are real contradictions if violated, not thresholds):
`band − no_opposing == 6,315`; `6,315 ≥ 3,009 ≥ 2,601`; `sum(segments) == union`; `union + 1,731
+ outside == N`; `213 + 200 == 413`; `geo5 + geo8 == geometry`; `overlap ≤ min(|geometry|, |route_a|)`.
`outside` negative or `union` not contained in the 870 is a contradiction; anything else prints.

## 4. Set algebra at bar 4 (must be recorded; it is **not** a nested subset)

`2,558` is **not** a subset of the `2,601`: the 151 recovered turns come from the separation-less
pool (a route-runner with no `separation_at_pass_forward`), and 194 turns of the 2,601 with two or
more candidates are not identified. So `2,601 → 2,558` loses 194 and gains 151, netting −43; the
"lost" arrow of a nested bar is a fiction at this step.

The figure keeps the approved drawing (the numbers he approved), and the set algebra is carried in
the **CSV, the sidecar, the manifest and the D note** — never asserted on the canvas:
`entered 2,601`, `survives 2,558`, `in_both 2,407`, `surviving_from_outside_entered 151`,
`entered_not_surviving 194`. Print all five with their derivations.

## 5. Inputs to commit (received, pinned)

Copy byte-for-byte from the inbound attachment set of 2026-11-18 (three identical copies of the
geometry table exist; the route-A table likewise):

| source (inbound) | committed as | bytes | rows | sha256 | endings |
|---|---|---:|---:|---|---|
| `geometry_1---93fc49a7-0d0b-4eae-a599-6642dc2e7b60.csv` | `outputs/ambiguity_attribution_received/geometry.csv` | 636,148 | 870 | `d93eab30a790ca7646359202d4d284045515e151aad16b80d4cda6e30b270074` | CRLF |
| `route_a_turns_1---4d95fd86-3ec9-427a-9180-d84790a8ca48.csv` | `outputs/ambiguity_attribution_received/route_a_turns.csv` | 114,073 | 564 | `79ed9a087a59e3a6fc13f3de02c2fd83ea8bcc7b9a31bc59eabf068d8fa4af51` | CRLF |

Extend `outputs/ambiguity_attribution_received/PROVENANCE.md` (it currently describes
`precedence.csv` and says "it holds one file"): one section per file, each stating what it is (a
laptop paste from `notebooks/bdb27_multi_candidate.ipynb` against the full tracking files on
2026-11-18), the sha256, bytes, rows, line endings, the inbound filename(s), that no manifest came
with it, and that **the sha256 identifies the bytes sent and verifies nothing about the run**. Also
record plainly: the geometry table is from the 07:17 UTC upload (before the D208 slot-precedence
fix), the precedence table from 11:07 UTC; the PM's reconciliation (676 + 1,731 + 151 = 2,558 =
the post-fix N) is why the two are used together, and the distances and the Route-A reference pass
do not depend on slot order.

Pin both sha256 in `figures.py` and refuse (naming `PROVENANCE.md`) if the committed bytes differ.
That is a provenance guard, not a threshold: it prints both digests first.

## 6. Drawing (reuse, do not re-implement)

Extend `plot_population_shrink` with two optional arguments, defaulting to `None`:

* `segments=None` — `{bar_index: [(label, count), ...]}`; when given, that bar's fill is drawn as
  those stacked shades and `label` is drawn small above each segment with a leader line;
* `notes=None` — `{bar_index: str}`; when given, drawn muted to the right of the bar's end label.

With both `None` the output must be **byte-identical** to today's figure 19 PNG — prove it: render
`fig_population_shrink` from the working tree and from `02cf531` (or from a stashed copy) and compare
sha256. Do not change any `POP_SHRINK_*` constant, any existing function's default behaviour, or
`config.yaml` (config_sha must not move).

Wording change, deliberate and to be recorded: the prototype's `(+151 not measured)` becomes
`(+151 without an exported distance)`. 134 of those 151 were attributed *by* a radius in the run, so
their distance exists — it was simply never exported. "Not measured" is false about the run.

Also deliberate: bar 5's state label reads `Nearest opponent within 3 yd`, not `Receiver within 3
yd`. The 213 is the identified man's distance (all 1,731 rule turns carry exactly one candidate, so
nearest == identified). The 200 is the nearest candidate's distance: of the 220 Route-A-only turns,
105 attribute a man who is not the nearest, and 3 of those are counted as within 3 yd. The
identified man's distance was never exported, so "the receiver is within 3 yd" is not what the data
shows. Record the 3 in the D note and the caveats.

## 7. Outputs

`outputs/figures/fig_population_receivers.{png,csv,drawn_text.json,provenance.json}` and one merged
entry in `outputs/figures/figures_manifest.json`. The stage's other 21 PNGs must not change (write
only these four files; run the stage into a scratch dir to check the rest are identical, as D211
did).

CSV: flat, one row per drawn element — `row_kind` (`bar` | `segment`), `step`, `state`, `segment`
(empty for bars), `entered`, `survives`, `lost`, `reason`, `source_artefact`, `source_field`,
`verified_on_this_machine`, `provenance` (`FULL` / `RECEIVED`), `label`, `figure`, `config_sha`,
`stamp`. Bar 4's row carries `in_both`, `surviving_from_outside_entered`, `entered_not_surviving`.
No `nfl_id`, no player name, no opponent identifier anywhere in the figure's four files.

Stamp/label: `FULL + RECEIVED` / `FULL DATA OUTPUT + RECEIVED STEPS` — nothing on this figure's last
two bars is reproducible on this host.

## 8. Wiring and tests

* `src/06_figures.py`: render figure 22 after figure 21, same pattern as figure 19 (own `drawn_text`
  dump, own manifest payload, own provenance sidecar); add its `run_log` detail; add a `22.` line to
  the stage docstring.
* `tests/test_population_receivers.py` (new): the five bar numbers, the four segments, the 200 split,
  413 = 213 + 200, the 151/194/2,407 algebra, every `source_artefact` exists on disk, no banned
  tokens, drawn-text assertions (5 state labels, 5 pairs, 4 segment labels, the note, no
  figure-level text), determinism, and the sha-pin refusal on a corrupted copy in `tmp_path`.
* `tests/test_figures_stage.py`: figure key set 21 → 22 and `len(man["figures"]) == 22`; add a
  rendered-files test mirroring figure 19's.
* `tests/test_notebook_contract.py`: 24 → 25 manifest entries, comment updated.
* `README.md` stage table: twenty-one → twenty-two figures. `notebooks/README.md` likewise if it
  counts.

## 9. Known conflict, do not "fix"

`TASK.md` said the paper carried **exactly four figures** (and ≤5 in the README). Adding figure 22
to the gallery does not change `TASK.md`; the conflict is recorded in the D note (D225) and left for
Conor.

**Resolved 2026-11-27 (D226).** Conor ruled that there is no figure cap and six figures is fine.
`TASK.md` §Figures now names all six, figure 22 as row 6; the README's ≤5 line is gone. Nothing else
in this spec changes.
