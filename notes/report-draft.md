# The break the Combine does not test

## 1. The football question

A defensive back's living is the shallow break. He is running at three, four, five yards per
second, a receiver stems him one way and cuts the other, and the defender has to open, change
direction by thirty or forty degrees and keep his speed through the turn. The deep plant — the
hard stop and reversal — matters too. But the shape that a route tree is built to create, and
that defensive technique is built to survive, is the moderate-angle break taken at speed.

The Combine does not test it. Across every defensive-back drill in the 2023–2025 Combine
tracking, turns of 30–60° are **2.21%** of valid DB turns. In the same players' NFL games they
are **13.26%** of valid DB turns `[FULL]`. By share, games ask for that turn **6.01×** more
often than the battery supplies it `[FULL]`.

That is the claim of this writeup and the whole of it: one comparison of supply against demand
by turn angle, and one recommendation that follows from it — add a 30–60° turn drill to the
defensive-back battery. It is not a claim that the Combine is a bad test. It is a good test of
the turn it was designed for. It is not a test of the turn where the shortfall in share is
largest.

## 2. What the data is

Two tracking sources, both at roughly 10 Hz, both 2023–2025, both reduced by the same turn
detector. From the Combine file, 1,179 valid DB turns from 122 players. From the game files,
58,664 valid DB turns from 115 players over 2,234 player-games `[FULL]`. That is 26.3 valid
turns per player-game in the games against 9.7 per Combine player `[FULL, arithmetic]` — the
game is not a sparser record, it is a much denser one.

One detector for both settings is what makes the comparison legitimate. A turn is a sustained
heading change over a short window, measured after the same smoothing, against the same 30°
floor and the same band edges. Nothing about the pipeline knows which file a turn came from.

Two asymmetries are worth stating up front. Orientation (`o`) exists in the game file and not
in the Combine file, so in the Combine a change of direction cannot be separated from a
backpedal or a shuffle. And Combine entry speeds sit below game speeds, which is why the claim
is made on band shares rather than on cell-by-cell counts.

Provenance is tagged throughout. `[FULL]` means a full-data artefact. `[PROPOSAL]` means the
drill design, which is not signed off and is not a finding. `[SCHEMATIC]` means a drawing.
`[PENDING]` marks a check not yet run at full scale. One figure in this writeup carries a
sample stamp; it is named, and nothing is read from it.

## 3. Supply against demand

Count every valid DB turn in both settings by the angle it turned through, in six bands, each
setting's shares taken over its own total `[FULL]`:

| Band | Combine turns | Combine share | Game turns | Game share | Game ÷ Combine |
|---|---:|---:|---:|---:|---:|
| 30–60° | 26 | 2.21% | 7,777 | 13.26% | **6.01×** |
| 60–90° | 237 | 20.10% | 13,077 | 22.29% | 1.11× |
| 90–120° | 159 | 13.49% | 13,016 | 22.19% | 1.65× |
| 120–150° | 499 | 42.32% | 11,444 | 19.51% | 0.46× |
| 150–180° | 128 | 10.86% | 8,137 | 13.87% | 1.28× |
| >180° | 130 | 11.03% | 5,213 | 8.89% | 0.81× |
| Total | 1,179 | | 58,664 | | |

The 30–60° row is the one that matters. Every other band is close to parity or leans the other
way: the Combine over-supplies the 120–150° plant at 42.32% of its turns against 19.51% of the
game's, 2.17× the game's share `[FULL]`. The battery is not short of turns. It is short of one
shape and long on another.

**Figure 1** — `outputs/figures/fig_the_hole.png` `[FULL]`. Combine and game shares by band;
the headline counts and shares in the table above are its values.

The shortfall is not an artefact of pooling defensive backs. It holds in every position group:
DL 4.60% against 9.76% (2.12×), WR 8.62% against 14.45% (1.68×), TE 10.54% against 14.23%
(1.35×), OL 6.51% against 9.34% (1.43×) `[FULL]`. The Combine's whole testing population is
under-supplied on this turn, not one slice of it.

**Figure 2** — `outputs/figures/fig_all_positions.png` `[FULL]`. The 30–60° shortfall across
all five position groups.

The sharpest fact in the paper is what the 26 shallow Combine turns actually are. They come
from 16 players across 9 drills, a median of one turn per player and a maximum of four, and 14
of the 26 are fragments of the 3-cone `[FULL]`. No player is tested on that turn more than
once. A test is a thing you do deliberately, repeatedly, to everyone. This is the opposite: a
band reached by accident, at the edges of drills built to measure something else. The battery
does not sample the shallow break so much as leak it.

[Video — in-game DB break. Caption: one line, what the clip shows and the timestamp. No number
is read from it.]

The clip is here to make the demand concrete; it adds no evidence.

## 4. What the battery actually tests

The battery is not failing to test turning. It is testing one turn extremely well, and the
drill-level numbers say which one.

Take the 3-cone. Its median measured turn is 70.6°, with median entry speed 3.98 yd/s, exit
4.96 and apex 3.16 `[FULL]`. Exit faster than entry is the signature: the player rounds the
corner and drives out of it. This is a **rounded accelerating cut**, and 37.8% of its turns
read as plants rather than arcs `[FULL]`. It is a good drill. It is not a drill about stopping.

Take the short shuttle. Median angle 179.6°, entry 3.55, exit 5.84, apex 0.34 yd/s `[FULL]`.
Speed collapses to a near standstill and the player re-accelerates the other way. This is the
**hard sink-and-reverse**, and 97.1% of its turns read as plants `[FULL]`.

The same gradient shows up in the game data by band. Of game turns at 30–60°, 15.3% are plants
and the rest are arcs; past 180°, 97.7% are plants `[FULL]`. Plant-ness is a function of angle,
and the battery has built its drills at the angle where plant-ness is total.

So the mechanism is simple and it is not a shortage of turns. The battery buys the deep plant:
stop, sink, reverse, re-accelerate. It is well equipped for that and it repeats it 499 times in
the 120–150° band alone `[FULL]`. What it leaves out is the shallow break taken at speed — the
arc-like change of direction that 84.7% of game 30–60° turns actually are `[FULL]`. The 26
turns it does record in that band are the accidental by-product of rounding a 3-cone or
clipping the entry of a backpedal drill.

**Figure 3** — `outputs/figures/fig_drill_angle_scatter.png` `PLACEHOLDER_SAMPLE`. Every
Combine turn at its measured angle, by drill. Stamped unvalidated sample output; nothing in
this writeup is read from it.

## 5. The drill that would close it

The proposal is a **tempo break**: a 45° break at speed to both sides `[PROPOSAL]`. Six reps,
three to each side, alternating. An 8–10 yd approach that reaches 3–5 yd/s at the break. A 5 yd
re-acceleration through a gate on the exit. About 90 s per player, on the existing 3-cone
footprint with no new station `[PROPOSAL]`.

Each parameter is doing a job. The angle is 45° because that is the shallow end of the demand
curve; it sits on the 30–45 / 45–60 boundary, so it is a boundary sample and not a clean
mid-band one — a limit of the design, not a feature of it. The 3–5 yd/s entry is where the
game's shallow turns actually happen. The 8–10 yd build-up makes that speed reachable, and the
3-cone already records a 3.98 yd/s median entry over segments of about 5 yd `[FULL]`.
Six reps is the part that changes the character of the measurement: every player gets several
attempts at the same turn, which the current battery cannot do, since no player is measured on
a 30–60° turn more than once `[FULL]`. Six reps across 122 DBs is about 730 turns of that
shape, against 26 today `[PROPOSAL]`. The value is in the 10 Hz tracking, not a stopwatch; no
electronic gate is required.

What it buys is deliberate, repeated measurement of a shape the battery currently reaches only
by fragment, inside a band where games ask for six times the share the Combine supplies
`[FULL]`. What it does not buy is an ordering of players, a prediction, or a clean mid-band
sample. And it is an addition, not a replacement. Games still demand the deep plant in volume —
19.51% of game DB turns sit in 120–150° `[FULL]` — and the drills that supply it are
standardised, comparable year to year and worth keeping.

**Figure 4** — `outputs/figures/fig_drill_schematic.png` `[SCHEMATIC]`. The proposed geometry:
approach line, break cone, two exit gates at 45°. A drawing of the proposal, carrying no
measurement.

## 6. What this does not say

It does not score a player. There is no player-level number in this writeup, and there could
not be: with one shallow turn per player at most, between-player differences in that band are
not identified. A score built on this data would be the group's average wearing a player's
name.

It makes no claim about what happens on Sunday. Nothing here says that a defender who breaks
well in a drill produces more pressure, defends more passes or plays longer. Those questions
need outcome data this analysis does not touch.

It makes no claim that drill performance carries into games. That link is assumed by every
Combine drill and is not measured here.

It makes no claim about whether players can actually hold 3–5 yd/s through a 45° break, or
whether six extra reps fit a Combine schedule without changing the session's load. Those are
questions for the drill's own validation.

It assigns no cause. The tracking sees the turn, not the contact, the scheme, the technique or
the opponent that produced it.

## 7. Measurement limits

Heading is derived from smoothed x/y positions at 10 Hz, and the smoother is the most
consequential choice in the pipeline: across the alternatives the measured angle moves by about
6° at the median. Every conclusion here is reported under those alternatives. The scale is the
point — a 6° uncertainty cannot move a turn out of the middle of a 30°-wide band; it can move
turns that sit near a boundary, which is why the bands are read as shares and not as crisp
membership.

The detector has a 30° floor, so it cannot see a turn below 30°. The 30–60° band is censored at
its own lower edge and the Combine count of 26 is a **lower bound**, not a census — a bias
against the claim, not for it.

The 3-cone's measured angle is the least reliable in the set: it is the drill where measured
angle disagrees most with drill geometry, so its contribution to the shallow band is also a
lower bound. The full-data quantification is `[PENDING]`; the sample-mode figure exists and is
not quoted here.

There is no orientation in the Combine, so a plant there cannot be separated from a backpedal
or a shuffle, and facing-decoupled movement is invisible.

Combine entry speeds sit below game speeds, so cell-by-cell comparison is partly extrapolated
by construction. That is why the claim is a statement about band shares.

Reversal-class turns are measured as plants — apex, exit, regain — never as v·ω, because a
circle fitted to a hairpin reports a radius that makes the acceleration non-physical.

The tracked players are the drafted group: 122 Combine players and 115 game players, not all
NFL defensive backs. The demand map describes who reached the league.

Season sampling is uneven: the 2023 tracking file is about a third the size of 2025's for the
same number of games, so only the pooled figure is used.

## What is not claimed

- **A player score.** Between-player differences at this turn are not identified.
- **Game value.** Nothing here predicts production, pressure or any result.
- **Drill-to-game carry-over.** Assumed by every Combine drill; not measured here.
- **Whether the drill is runnable.** Load, fatigue and schedule fit need their own validation.
- **Cause.** The tracking sees the turn, not the reason for it.
- **Not the modal turn.** 60–90° and 90–120° are the modal bands; 30–60° is where the
  proportional shortfall is largest.
