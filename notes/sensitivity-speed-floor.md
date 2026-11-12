# Sensitivity — Combine speed floor at 2.0 yd/s (2026-11-12)

**Question (Conor, verbatim).** "What do the numbers look like if 2 yards per
second are taken as the limit in the combine?"

**Status.** Sensitivity, real data, DB group. It is **not** a sample run and
**not** a new FULL artefact: the game side is the committed FULL band table
reused unchanged, and the committed figures 1 and 5 were neither touched nor
re-rendered. Provenance: `SENSITIVITY — real Combine data; game side FULL
reused`.

**One-line answer.** Nothing material moves. The 30–60° share is 2.19% at a
2.0 floor against 2.21% at the committed 2.5, so the shortfall ratio against
the game's 13.26% goes from **6.01× to 6.06×**. The Combine still supplies
2.40× the game's share of 120–150° plants (2.17× at 2.5). The added turns are
not shallow turns: 184 of the 284 extra DB turns land in 120–150°, and 81% of
all extra turns come from the W drill and the Box.

## Method

`src/09_speed_floor_sensitivity.py` re-detects the Combine side with one
parameter changed: `DetectorParams.speed_floor` 2.5 → 2.0. Everything else is
the committed definition — primary smoother `savgol_g9_p3`, 30° minimum turn
angle, 0.5 s window, `speed_maxima` segmentation, and the reporting bands of
`figures.hole.plot_bands`. The game side is **not** re-run: its floor was
already 2.0 (`detector.speed_floor_game`), and
`data/interim/full_run_20261111/bdb27_full_game_bands.csv` is read and reused
as-is.

The script first runs a **control pass at 2.5** and refuses unless it
reproduces the committed FULL Combine table exactly — 1,179 valid DB turns
and 26 / 237 / 159 / 499 / 128 / 130 across the six reporting bands. That
control passed. The detection-relevant config blocks (`smoothing`, `detector`,
`sampling`, `plant`, `bins`, `positions`, `sanity`, `geometry`) are identical
to the ones at `aeb483c` (config_sha `a4b45b6f`) that produced the FULL run;
the current fingerprint `a76cf3bd9012f085` differs only in `figures.*`.

Inputs: `data/raw/combine_tracking.csv` (sha256 recorded in the manifest),
1,432 DB attempts, 108,650 DB frames, 122 DB attendees, 6,310 attempts across
all positions. Artefacts live in `outputs/sensitivity/speed_floor/` and are
committed (see `.gitignore`).

## DB band table

Counts are valid DB turns; shares are within setting. Game numbers are the
FULL table, unchanged.

| band | 2.5 count | 2.5 share | 2.0 count | 2.0 share | game count | game share |
|---|---:|---:|---:|---:|---:|---:|
| 30–60° | 26 | 2.21% | 32 | 2.19% | 7,777 | 13.26% |
| 60–90° | 237 | 20.10% | 281 | 19.21% | 13,077 | 22.29% |
| 90–120° | 159 | 13.49% | 188 | 12.85% | 13,016 | 22.19% |
| 120–150° | 499 | 42.32% | 684 | 46.75% | 11,444 | 19.51% |
| 150–180° | 128 | 10.86% | 140 | 9.57% | 8,137 | 13.87% |
| >180° | 130 | 11.03% | 138 | 9.43% | 5,213 | 8.89% |
| **Total** | **1,179** | | **1,463** | | **58,664** | |

- **30–60° shortfall:** 13.2569 / 2.2053 = **6.01×** at 2.5; 13.2569 / 2.1873
  = **6.06×** at 2.0.
- **120–150° over-supply:** Combine/game = 42.32/19.51 = **2.17×** at 2.5 and
  46.75/19.51 = **2.40×** at 2.0 (the reciprocal, game/Combine, is 0.46× at
  2.5 and 0.42× at 2.0).
- Total DB turns rise by 284 (+24.1%); the 30–60° count rises by 6, and the
  120–150° count by 185.

## Where the extra turns come from

**By drill.** 81.0% of the +284 is the W drill (+165, 107 → 272) and the Box
(+65, 304 → 369). The remaining 54: Back-Pedal-and-90 (+19), Teryl Austin 2
(+19), Three-Cone (+14), Forty-Yard Dash (+2); the other five drills are
unchanged.

**By band.** The 288 turns new at 2.0 (one-to-one match on attempt + centre
frame within 5 frames, the detector's own 0.5 s tolerance) split: 184 in
120–150°, 44 in 60–90°, 32 in 90–120°, 10 in 150–180°, 10 in >180°, 8 in
30–60°. Only 6 existing turns change band; 4 turns disappear (two ~49.5°
Three-Cone turns, one 71° Three-Cone turn, one 95.7° Back-Pedal-and-90 turn).

**The 30–60° band specifically.** Of the 32 turns at 2.0, 24 are the same
turns as at 2.5, 8 are new, and 2 of the 26 at 2.5 are lost — both Three-Cone
turns at 49.6° and 49.0°, reclassified or dropped at the lower floor. The 8
new shallow turns are concentrated: 6 in the W drill, 1 in the Box, 1 in the
Three-Cone. Net per drill: W +6, Box +1, Three-Cone −1.

**By player.** 103 of 122 DBs gain at least one turn, median +2 (max +6); the
top ten players account for only 19% of the increase, so the extra volume is
spread across the cohort because the W and Box are run by nearly every DB.
The 30–60° additions are *not* spread: 8 players gain one each, and 2 players
(the two Three-Cone losses) lose one each, for the net +6.

## The caveat, quantified: the shuffled-heading null

The 2.5 floor exists because heading is noisier at low speed. The engine's
shuffled-heading null (permute heading increments, keep the speed profile,
re-detect) shows what that noise does at each floor, DB turns only:

| floor | observed detections | observed / 1,000 frames | null detections | null / 1,000 frames | null / observed |
|---:|---:|---:|---:|---:|---:|
| 2.5 | 1,179 | 10.85 | 1,131 | 10.41 | 0.959 |
| 2.0 | 1,463 | 13.47 | 1,306 | 12.02 | 0.893 |

The null rises by 175 for the 284 observed additions, i.e. **62% as much as
the observed increase**; only 109 of the 284 extra detections (+38%) are
beyond the null's own increase. Read honestly: the 2.0 floor adds a lot of
detections whose kinematic signature the shuffled-heading null can also
produce. This does not make them false positives — D138 is explicit that the
null is a **sensitivity floor, not a false-positive estimate**, and geometry
validation is what establishes real detections. But it does mean the extra
2.0 turns should not be treated as clean signal without that geometry check,
and it is consistent with the reason the Combine floor was set at 2.5. The
null/observed level is not a DB peculiarity: the committed all-position
Combine sample run reads 5,294 null against 5,536 observed (0.956) at 2.5.

The shallow band is where this matters least in one sense (only 8 new turns)
and most in another: those 8 are exactly the low-speed, low-angle events the
floor was raised to suppress. Raising or lowering the floor cannot create
shallow turns in volume — the W drill's low-speed weave produces mostly deep
absolute heading changes (120–150°), not 30–60° cuts.

## What this does and does not change

- **The 6.01× headline survives a 2.0 floor: it reads 6.06×.** The answer to
  Conor's question is that the headline is insensitive to this choice.
- The draft's section 2 sentence ("speed above 2.5 yd/s at entry and at exit")
  describes the committed definition and remains correct. If Conor *adopts*
  2.0 for both settings, that sentence and the Combine counts in the section-5
  table would change; this note does **not** edit the draft, per instruction.
- No committed figure, CSV or FULL artefact was touched or re-rendered.

## Reproduction

```
python src/09_speed_floor_sensitivity.py            # floor 2.0, control 2.5
```

`tests/test_speed_floor_sensitivity.py` covers band assignment, the delta
tables, one-to-one turn matching and the control refusal.
