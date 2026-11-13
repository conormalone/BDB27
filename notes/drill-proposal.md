# Proposed Combine drill: the tempo break

**Status: PROPOSAL — the recommendation carried by `TASK.md` (D191), not a finding. Needs
Conor's sign-off on its parameters before it appears in any writeup.** Written 2026-11-07 from
the sample-mode artefacts
(`outputs/turns/turns.parquet`, `outputs/envelope/`, `outputs/demand_coverage/`), which are
stamped `PLACEHOLDER_SAMPLE`. Every count below is a sample count and will change on the
full run; the *ratios* are the argument, not the absolute numbers.

---

## 1. Why a new drill at all

The Combine's DB drill set samples the big plant heavily and the shallow break thinly. Two
different "gaps" get confused in conversation; they are not the same measurement.

### 1a. The coverage gap (supply vs demand) — this is what the drill targets

Combine supply is the number of detected turns in existing DB drills; demand is the number
of DB turns in game data in the same cell.

| Turn angle | Combine turns | Game turns | Ratio |
|---|---:|---:|---:|
| 30–45° | **4** | **97** | 24× |
| 45–60° | 22 | 229 | 10× |
| 60–90° | 237 | 476 | 2.0× |
| 90–120° | 159 | 508 | 3.2× |
| 120–150° | 499 | 460 | 0.9× |
| 150–180° | 128 | 307 | 2.4× |
| >180° (wrapped reversal) | 130 | 185 | 1.4× |
| **>120° total** | **757** | **952** | 1.3× |

**The 30–45° band is the focus** (Conor, 7 Nov): 4 Combine DB turns against 97 in games.

Rows are mutually exclusive bands; turns above 180° are a real class (a heading change past a
half-turn) and are counted separately rather than being dropped.

By entry speed inside the 30–60° band:

| Entry speed | Combine | Game |
|---|---:|---:|
| 2–4 yd/s | 16 | 178 |
| 4–6 yd/s | 8 | 115 |
| 6–8 yd/s | 2 | 33 |

The 26 shallow-angle turns are not deliberate coverage. They come from **16 players across 9
drills**, median **1 turn per player**, maximum 4:

| Drill | Turns | Players | Median entry | Median angle |
|---|---:|---:|---:|---:|
| THREE_CONE_DRILL | 14 | 11 | 3.9 | 55.8 |
| BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL | 4 | 4 | 3.8 | 48.1 |
| TERYL_AUSTIN_DRILL_2 | 2 | 2 | 3.6 | 55.5 |
| BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION | 1 | 1 | 3.3 | 48.4 |
| BOX_DRILL | 1 | 1 | 3.4 | 37.2 |
| SHORT_SHUTTLE | 1 | 1 | 2.7 | 37.2 |
| TERYL_AUSTIN_DRILL_1 | 1 | 1 | 7.7 | 56.7 |
| W_DRILL | 1 | 1 | 2.8 | 40.3 |
| FORTY_YARD_DASH | 1 | 1 | 3.1 | 48.1 |

They are entry and exit fragments of drills designed to do something else. No player is
tested on that turn more than once.

Proportionally, of 1,179 Combine DB turns and 2,262 game DB turns: **>120° is 64% of the
Combine and 42% of games; 30–90° is 22% of the Combine and 35% of games.** The Combine is not
short of absolute big-angle volume — 757 turns either side of 120° — it is short of big-angle
volume *relative to how it over-supplies it*, and badly short of shallow angle at any speed.

Where the >150° band stands on its own (this is the band the drill could have targeted):
Combine 258 turns against 492 in games — but those 258 come from **112 players**, and 97 of
them are one turn each from TERYL_AUSTIN_DRILL_2, with 68 more from SHORT_SHUTTLE across only
28 players. So the >150° band has breadth in player count and almost no depth per player — a
different defect from the 30–60° band, which has neither.

### 1b. The feasibility gap (outside the envelope) — NOT what the drill targets

Phase 4 flagged the largest DB cut gaps at **6–8 yd/s × 120–180°** (inside-share 0.11 and
0.00). That is a different statement and it is weaker, for two reasons established later
the same day:

- The 90% speed-retention tolerance can never be met by a reversal, so those cells are
  infeasible by construction — a threshold talking, not a physical finding.
- Those turns carry non-physical peak lateral accelerations (median 2.1 g, p95 7.0 g) because
  a circle fit to a hairpin reports a ~1.8 yd radius. Any capacity statement built on them is
  invalid, and they should be measured as plants instead.

The drill therefore targets the **coverage** gap (1a), not the feasibility gap (1b).

---

## 2. The drill

**Tempo break — 45° at speed, both sides**

```
Setup      Approach line marked by two cones, 8-10 yd apart.
           Break cone at the end of the approach.
           Two exit gates 5 yd from the break cone, at 45 deg either side of
           the approach line.
Movement   Walk-in, 2-yd run-up, accelerate over 8-10 yd.
           Reach 3-5 yd/s at the break cone.
           Single plant, break 45 deg, re-accelerate 5 yd through the gate.
Reps       6 total: 3 left, 3 right, alternating.
Timing     No electronic gate required. The value is the 10 Hz tracking.
Duration   ~90 s per player.
Space      Same cones and footprint as the 3-cone; no new station.
```

**Why these parameters**

- 45° is the angle Conor set (2026-11-12). It sits on the upper edge of the chosen 30–45°
  band, the widest proportional gap (24×). The bands are half-open at 45, so a 45° break is
  read against the 30–45 / 45–60 boundary — it is not a clean 30–45° sample.
- 3–5 yd/s entry is the demanded band (178 + 115 game turns at 2–4 and 4–6 yd/s in the
  30–60° band).
- An 8–10 yd build-up reaches that entry speed: the 3-cone already records a 3.9 yd/s median
  entry across segments of about 5 yd.
- Six reps gives every player repeated measurements of the *same* turn, which is what the
  existing incidental detections cannot provide.

**What it would buy:** ~6 deliberate turns per player at that shape — roughly 730 turns
across 122 DBs, against 97 game turns in the 30–45° band and 326 in 30–60°, versus 4
detections today.

---

## 3. Comparison against the existing DB drill set

| Drill | Turns | Players | Median entry | Median angle | Share in 30–120° @ 2–6 yd/s |
|---|---:|---:|---:|---:|---:|
| BOX_DRILL | 304 | 119 | 3.5 | 139 | 2% |
| TERYL_AUSTIN_DRILL_2 | 199 | 111 | 3.6 | 148 | 43% |
| BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION | 133 | 71 | 6.4 | 113 | 28% |
| BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL | 118 | 102 | 3.1 | 89 | 90% |
| TERYL_AUSTIN_DRILL_1 | 108 | 101 | 5.1 | 140 | 3% |
| W_DRILL | 107 | 56 | 2.8 | 139 | 3% |
| THREE_CONE_DRILL | 98 | 24 | 4.0 | 71 | 62% |
| SHORT_SHUTTLE | 70 | 28 | 3.5 | 180 | 3% |
| GAUNTLET_DRILL | 41 | 41 | 7.8 | 85 | 0% |
| FORTY_YARD_DASH | 1 | 1 | 3.1 | 48 | 100% |

**Add, do not replace.** The big-angle drills are not redundant — games still demand 767 DB
turns above 120° (37% of the total), and those drills are standardised with years of prior
data. Removing one to make room would trade well-covered ground for under-covered ground.

If a slot were forced, the cheapest by these numbers are SHORT_SHUTTLE (70 turns, 3%
game-shaped), W_DRILL (107, 3%) and TERYL_AUSTIN_1 (108, 3%) — but each costs coverage of a
shape games do ask for.

**Cheaper alternative:** add a 45° variant beside BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL,
the best-performing DB drill on this measure (90% of its turns in the game shape). One extra
rep instead of a new station.

---

## 4. Other positions (same arithmetic, from the same artefacts)

| Position | Widest gap | Combine | Game |
|---|---|---:|---:|
| TE | 4–6 yd/s × 120–150° | 0 | 33 |
| TE | 4–6 yd/s × 90–120° | 2 | 33 |
| WR | 4–6 yd/s × 90–120° | 20 | 145 |
| WR | 4–6 yd/s × 60–90° | 38 | 193 |
| OL | 2–4 yd/s × 90–120° | 67 | 277 |
| OL | 2–4 yd/s × 120–150° | 64 | 257 |
| DL | 2–4 yd/s × 150–180° | 102 | 153 |

DL has no meaningful speed gap: games barely ask DL to turn above 6 yd/s (8 turns in the
sample), so a DL version of this drill is not indicated by the data.

---

## 5. What must be validated before this is a claim

1. **Entry speed is achievable and maintained.** Can players hold 3–5 yd/s through the break
   without it collapsing into a jog? The entry-speed distribution must be measured, not
   assumed.
2. **The detector recovers a 45° turn at that speed** at 10 Hz, and the measured angle must be
   separated from smoothing noise (the smoother spread on |angle| is already ~5.8° median).
3. **Fatigue.** Six extra reps change the session's load profile — which matters because the
   Combine-as-test premise depends on drills being independent and comparable.
4. **Reversal contamination.** Whatever is added must not feed hairpin-shaped turns into the
   lateral-capacity fit; reversals are measured as plants (apex, exit, regain), never as
   v·ω.

---

## 6. Open questions for Conor

- Target the 30–60° band only, or a two-angle version (45° + 75°) to cover 30–90° in one drill?
- Six reps, or fewer with a left/right balance?
- Is the deliverable a *proposed drill* (design + arithmetic) or an *analysis* of the gap with
  the drill as discussion? These are different papers; the second is safer.
