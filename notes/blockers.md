# Blockers — BDB27

**Status: NONE ACTIVE.** Phase 1 cleared the gate (GO). Recorded 2026-10-09.

Per `TASK.md`, a blocker stops work and must be written here. The following were checked and are **not** blocking:

- **Timing/order field present?** Yes — `time` (TIMESTAMP) + `attempt`, agreement 0.9992.
- **A position group qualifies?** Yes — **DB** under the all-drills family (D13; DB 122/122 reach ≥3 observed attempts).
- **Reliability below threshold?** Not yet measured (Phase 3; pre-registered stop <0.2).
- **Thresholds provenance not "FULL" in a full run?** N/A this cycle (Phase 0/1 only).

## Watch-items (documented, not blockers)

1. **Competition caps unverified at source.** Kaggle pages are JS-rendered; caps captured from live-page
   snippets + secondary mirrors (`notes/rules.md` §6). **Action:** re-verify word/figure caps on the
   competition page before writing the final writeup. Safe posture adopted (≤2,000 words, ≤5 figures).
2. **Time zone unverified.** `time` is naive; assumed UTC (`decisions.md` D11). **Action:** confirm before
   using time-of-day as an exposure.
3. **Full game-tracking files absent.** `game_tracking_2023/24/25.csv` not on the drive; game-side audit
   (e) and Phase 4/5 need them. Marked PENDING FULL RUN — expected, not blocking Phase 0/1.
4. ~~**Interpretation of "family of repeated maximal-effort drills".**~~ **RESOLVED (D12):** human decision
   (2026-10-09) sets the family = **ALL drills**; recorded in `decisions.md` D12. Supersedes D3's drill pick.
5. **Position scope LOCKED to DB only (D13).** DB is the locked study population (human decision, Conor 2026-10-09; `audit.study_population: DB`). WR is excluded (in-game intensity is scheme-dependent → between-system noise). The single-position design **satisfies TASK.md Phase 1 ("select ONE position group")** — not a deviation. Table: `outputs/audit/h_family_scope.csv`.
6. **Matched Combine→NFL count for DB = PENDING FULL RUN.** Needs
   `game_tracking_{2023,24,25}.csv` (absent). Not blocking Phase 0/1; needed before the Phase 5 link
   (if the DB matched-N is too small, that is a blocker). See `pending_full_run.md` P1/P8.
7. **Draft position control: structural "undrafted", not missing (A12).** `players.draft_overall_pick` and `draft_round` are null for the **same 127/510** prospects (the undrafted); the >20%-null exclusion rule applies to **non-structural** missingness only, so it does **not** fire → draft position is **KEPT** as a Phase-5 control, with "undrafted" encoded as its own level (recorded `assumptions.md`, `limitations.md`, `decisions.md`). Not a blocker; the spec makes draft position a control where available.
