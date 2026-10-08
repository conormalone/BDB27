# De-risk findings — within-combine fatigue signal

**Project:** NFL Big Data Bowl 2027 — combine-fatigue study (`conormalone/BDB27`)
**Artifact:** `notebooks/01_de_risk_prototype.ipynb` (runs top-to-bottom; figures in `outputs/figures/`)
**Date:** 2026-10-08 · **Scope:** cheap reconnaissance, *not* the full study.

---

## TL;DR verdict

| # | Question | Verdict |
|---|---|---|
| 1 | Is drill order / time-of-day recoverable? | **Yes — fully.** ISO `time` gives exact order; `attempt` agrees with `time` **100%**. |
| 2 | Crude attempt-to-attempt decay? | **Essentially none.** 40-yd peak speed −0.02 yd/s (p≈0.001, ~0.2%); duration flat; 3-cone/shuttle null. |
| 3 | Is order confounded? | **Badly.** Within a session, order ≈ drill *name* (η²≈0.85); between days, position ≈ drill type (V≈0.89). |

**Bottom line:** the naive "fatigue = slower later in the combine" effect is **not identifiable** in this
design — *later in the session* is almost the same variable as *which drill*. The only clean contrast
(within a single drill, attempt 1 vs 2) shows little signal. **The fatigue angle is high-risk as framed.**

---

## Data reality check (what the data actually supports)

- **6,310 events** (one `event_id` = one attempt by one player in one drill); **510 players**; seasons
  **2023 / 2024 / 2025**. Confirmed 1:1 `event_id → (nfl_id, drill_name, attempt)`.
- Drills: `FORTY_YARD_DASH`, `THREE_CONE_DRILL`, `SHORT_SHUTTLE`, and positional `SKILL_DRILLS_{WR,DB,DL,OL,TE,LB}`
  (the `SKILL_DRILLS_*` families contain many named sub-drills, each restarting `attempt`).
- **`event_id` formats differ:** 2024/25 ids embed a session stamp (`YYYYMMDDhhmmss_<epochms>_<seq>`);
  2023 ids are opaque Mongo-style hashes. **Order must be derived from `time`, not `event_id`.**
- **Each player is exactly one ~1.5 h session.** Using `(player, calendar date)` incorrectly split **111
  players** because workouts run **past midnight** (e.g. 22:17 → 00:12). We instead split on a **2 h
  inactivity gap** → 510 players × 1 session each; intra-session gaps max **53 min**.
- **Timestamps look like UTC** — on-field testing lands at clock hours 18:00–02:00 (≈13:00–21:00 US-Eastern).
  Time-of-day is recoverable, but **confirm the timezone** before using it as an exposure.
- 10 Hz kinematics are credible: **peak speed correlates −0.98** and **window duration +0.81** with the
  *official* 40-yard time.

---

## Q1 — Is drill order / time-of-day recoverable? **YES.**

- **`attempt` is a faithful ordering:** within `(session, drill_name)`, `attempt` is monotonic with `time`
  in **100% of 4,994 groups**. (It only looks non-monotonic within `drill_type` because each sub-drill in
  a `SKILL_DRILLS_*` family restarts `attempt` at 1 — a grouping artefact, not noise.)
- **Within-session order is the combine's fixed protocol.** Across all position groups the sequence is
  `FORTY_YARD_DASH → [positional skill drills] → THREE_CONE_DRILL / SHORT_SHUTTLE`. Median within-session
  rank: 40-yd ≈ 0, skill drills ≈ 5–7, 3-cone ≈ 10, shuttle ≈ 11.

**Implication:** order and time-of-day are recoverable, but order is *structural* (the test protocol),
not a free-running clock — this is exactly what creates the confounding below.

## Q2 — Crude attempt-to-attempt decay? **Essentially none.**

Within player-year, paired attempt 1 vs attempt 2:

| Drill | Metric | n | att1 → att2 | Δ | p |
|---|---|---|---|---|---|
| 40-yard | peak speed (yd/s) | 302 | 10.351 → 10.327 | **−0.024** | 0.001 |
| 40-yard | window duration (s) | 302 | 5.159 → 5.142 | −0.017 | 0.14 |
| 40-yard | path length (yd) | 302 | 40.52 → 40.45 | −0.07 | 0.06 |
| 3-cone | duration (s) | 19 | 7.716 → 7.684 | −0.03 | 0.74 |
| 3-cone | peak speed | 19 | 6.27 → 6.71 | +0.45 | 0.06 |
| shuttle | duration (s) | 27 | 5.51 → 5.37 | −0.14 | 0.83 |

- The only "significant" result (40-yd peak speed) is **~0.2%** of the mean — statistically detectable,
  practically nil, and **direction is partly explained by warm-up/potentiation** (later attempts often
  follow a false start).
- 40-yd is the **only** drill with usable repeat counts (attempts: 372 / 347 / 61 / 13). 3-cone and
  shuttle have **< 30 paired** player-years — underpowered.

**Implication:** on a crude within-drill, attempt-1-vs-2 read, there is **no sizeable fatigue decrement**.

## Q3 — How badly is order confounded? **Order ≈ drill type; day ≈ position.**

| Quantity | Value | Reading |
|---|---|---|
| η²( within-session order ~ **drill_name** ) | **0.851** | order is ~the drill sequence |
| η²( order ~ **drill_type** ) | 0.414 | collinear with the drill family |
| η²( order ~ **position** ) | 0.000 | *within* a session, position adds nothing |
| Cramér's V( **drill_type × position** ) | **0.894** | position groups run disjoint drill sets |
| Cramér's V( **position × combine_day** ) | 0.857 | each position group tests on its own day |
| Cramér's V( drill_type × combine_day ) | 0.555 | drill ↔ day entanglement |

- **Within a session:** everyone runs the same fixed sequence, so "later in the session" is essentially
  "a different drill". A generic order/fatigue term **cannot be separated from drill-type differences**.
- **Between sessions/days:** the day *is* the position group, so "later combine day" is confounded with
  position and ability (and with opt-outs, which are non-random).

---

## Identifiability: can a fatigue effect be identified?

- **Generic "later = more tired" (drill order as the exposure): NO.** Order is a deterministic function
  of drill identity (η²≈0.85 with drill name); any order coefficient also absorbs each drill's own
  scoring scale, only some of which can be differenced away.
- **Between-day fatigue: NO (as-is).** Day ≈ position group (V≈0.86), and participation is non-random.
- **Within-drill repeated attempts: PARTIALLY identified** (drill type held fixed) — but only 40-yd has
  usable repeats, and the crude signal is ~null. 3-cone/shuttle are too sparse for a primary test.

### Recommendations for the full study
1. **Do not lead with "drill order"** as the fatigue exposure; lead with a **within-drill, within-player**
   contrast (attempt-to-attempt, and decay *rate* from the 10 Hz trajectory) where drill type is constant.
2. Treat 40-yd as the workhorse (largest repeats); pool 3-cone/shuttle only descriptively.
3. Consider a **physically-motivated load/rest exposure** (elapsed time since prior max-effort attempt)
   rather than raw order — but validate it is not just a drill proxy.
4. If the opt-out/selection and timezone questions can't be nailed down, treat the fatigue angle as a
   **supporting mechanism** in a broader "combine → NFL" story, not the headline.

*Caveat: this is reconnaissance on a crude, un-fitted signal. A modelling pass (mixed-effects with
player random effects, drift terms fitted from trajectories) could still surface a small effect — but
the design limitations above are structural and will not go away.*
