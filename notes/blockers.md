# Blockers — BDB27

**Status: 0 ACTIVE.** Per `TASK.md`, a blocker stops work and must be written here. **Prior
(combine-fatigue) blockers B1–B7 are archived at git tag `archive/combine-fatigue-v1`** (HEAD `fdc794c`).

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
4. **`notes/prereg.md` is a skeleton.** Its values are `TBD`; it must be frozen **before** any full run —
   at that point, an unfrozen prereg is itself a blocker.
5. **`route_ran` is categorical, not a flag** (audit d, 2026-10-11). Config key `game.route_snap_col`
   still reads as a flag; *route snap* is defined as `route_ran IS NOT NULL`. Handled in code — documented
   so nobody treats it as boolean. Not a blocker.
6. **Thin Combine repeats → low/unestimated ICC** (audit g, 2026-10-11). 40-yd dash has ~2 attempts/player;
   cutting drills mostly 1 (many features ICC≈0 or `UNESTIMABLE`). This is a design constraint for Phase-2
   feature selection (reliable: `peak_speed`, `speed_at_10/20yd`, `env_vmax`), not a blocker.
