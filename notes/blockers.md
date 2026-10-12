# Blockers — BDB27

**Status: 0 ACTIVE.** Per `TASK.md`, a blocker stops work and must be written here. **Prior
(combine-fatigue) blockers B1–B7 are archived at git tag `archive/combine-fatigue-v1`** (HEAD `fdc794c`).

As of **2026-10-12 (Cycle 2)** there are **no active blockers**. The pre-registration blocker that gated the
full run has been **resolved**: `notes/prereg.md` is **FROZEN** (Conor-approved values; `notes/decisions.md`
D28) and the freeze gate (`src/gate.py`) now passes for `--mode full` of the Combine-side stage
(`02_combine_envelope`). Remaining obstacles to a *whole-pipeline* full run are the **game-tracking files
being absent on this Pi** (watch-item 3, `PENDING FULL RUN`) — a data-availability limit, not a design
blocker.

Trigger a blocker on:

- A **missing required Combine/game field**.
- **Matched receivers or route snaps below config minima**.
- **Key Combine features unreliable** (below the ICC threshold).
- **Thresholds provenance not `FULL` in a full run**.
- **`notes/prereg.md` missing / unfrozen before a full run**.
- Any instruction **conflicting with competition rules**.

## Watch-items (documented, not blockers)

1. **Competition caps unverified at source.** Kaggle pages are JS-rendered; caps captured from live-page
   snippets + secondary mirrors (`notes/rules.md` §6). **Action:** re-verify word/figure caps on the
   competition page before the final writeup. Safe posture: ≤2,000 words, ≤5 figures.
2. **Time zone unverified.** `time` is naive; assumed UTC. **Action:** confirm before using absolute
   time-of-day as an exposure.
3. **Full game-tracking files absent.** `game_tracking_{2023,24,25}.csv` are not on the drive; game-side
   audit items (c/d/e/h/i) and Phases 3–5 are `PENDING FULL RUN` (expected, not blocking Phase 0).
4. **`notes/prereg.md` freeze — RESOLVED 2026-10-12.** Was a skeleton (TBD values); now **FROZEN** with
   Conor-approved values (`notes/decisions.md` D28). The runtime freeze gate is `src/gate.py`; the go/no-go
   gate for game-side full runs additionally needs the audit gate (matched WR, route snaps, ICC).
5. **`route_ran` is categorical, not a flag** (audit d, 2026-10-11). Config key `game.route_snap_col`
   still reads as a flag; *route snap* is defined as `route_ran IS NOT NULL`. Handled in code — documented
   so nobody treats it as boolean. Not a blocker.
6. **Thin Combine repeats → low/unestimated ICC** (audit g, 2026-10-11). 40-yd dash has ~2 attempts/player;
   cutting drills mostly 1 (many features ICC≈0 or `UNESTIMABLE`). This is a design constraint for Phase-2
   feature selection (reliable: `peak_speed`, `speed_at_10/20yd`, `env_vmax`), not a blocker. **Frozen
   threshold** `envelope.icc_min=0.5` (prereg §4); residue: the two retained raw-speed features remain
   collinear (VIF>5) — flagged, not a blocker (`notes/limitations.md`).
