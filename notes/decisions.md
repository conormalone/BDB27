# Decisions log — BDB27

Append-only. Each entry: what was decided, why, and the evidence/constraint behind it. Binding spec = `TASK.md`.

---

## D1 — Cycle scope: Phase 0 + Phase 1 (audit gate) · 2026-10-09
**Decision.** This cycle delivers Phase 0 (setup/rules/schema) and Phase 1 (the **go/no-go audit**, questions a–g) and the spec-layout scaffold. Phase 2 (Combine features) is **not** started this cycle.
**Why.** TASK.md frames Phase 1 as a hard gate ("stop … if no group qualifies"); the audit must be signed off before feature engineering. Scope is deliberately bounded.

## D2 — Process: coder/reviewer/validator could not be spawned; PM executed inline · 2026-10-09
**Decision.** The PM role owns `diagnose → spawn coder → spawn reviewer + validator`. In this run **no `sessions_spawn` tool was available** (sub-agent at depth 1/1 with a policy-filtered tool set: read/write/edit/exec/… only). Rather than stall, the PM **executed the audit directly** and performed **inline self-review + validation** (schema assertions, edge-case checks, determinism re-run, adversarial checks on the (c)/(d)/(f) findings).
**Why / caveat.** This is a **deviation** from the letter of `ROLES.md`/`TOOLS.md` forced by the environment. Flagged to Main: if independent coder/reviewer/validator passes are mandatory, this cycle's artifacts need a separate review pass. All methodology calls remain the PM's (D3–D11).

## D3 — Recommended position group = **DB**; drill family = **FORTY_YARD_DASH** · 2026-10-09
**Decision.** Primary study unit = **defensive backs**, primary repeated maximal-effort drill = the **40-yard dash**.
**Evidence (audit b/c):** 40-yd is the only drill with broad repeats — DB 93 of 102 runners have ≥2 attempts (195 attempts), ahead of DL 84, OL 84, WR 82, TE 32. `THREE_CONE_DRILL` / `SHORT_SHUTTLE` are far too sparse per position (≤13 players with ≥2 attempts) and 61–65% opted out at the results level. 40-yd also has the lowest opt-out (16.9%).
**Why DB specifically:** the largest 40-yd repeat cohort and a high-intensity in-game role — which matters for the Phase 4/5 in-game decay link.
**Secondary:** keep 3-cone/shuttle descriptively only; do not pool them into the primary model.

## D4 — `dis` is unreliable; recompute distance from x/y · 2026-10-09
**Decision.** Do **not** use the provided `dis` column as path distance. Recompute distance by integrating `x,y` per frame (Phase 2); keep `dis` only as an advisory cross-check.
**Evidence (audit d):** summed `dis` under-counts the x/y path by a median **9% (40-yd)** to **40–65% (skill drills)**; 80–100% of attempts differ >5%. The x/y trajectories are smooth (max 10 Hz step 1.2 yd; no teleports), so x/y is the trustworthy source. The low 40-yd correlation (r=0.19) is a low-variance artefact (all 40-yd attempts ≈40 yd).

## D5 — Order is confounded with drill type; identify from within-drill repeats · 2026-10-09
**Decision.** Do **not** lead with raw drill order as the fatigue exposure. Identification rests on **within-drill, within-player repeated attempts** (attempt 1 vs 2+) plus **load/rest timing**.
**Evidence (audit c):** `FORTY_YARD_DASH` is always the first drill (rank sd = 0; pairwise order 100% fixed); the only cross-player order variation is `SHORT_SHUTTLE` ↔ `THREE_CONE_DRILL` (≈50/50) — two *different* drills, so still drill-confounded. This matches the de-risk memo (order ≈ drill name, η²≈0.85).

## D6 — Missing values: `NA` → NULL at read time · 2026-10-09
**Decision.** All CSV→Parquet conversions pass `nullstr=['NA','']`.
**Why.** Audit found that `read_csv_auto` else keeps `"NA"` as a **string**, hiding real missingness (first run reported 0 nulls in `combine_results`). After the fix: `three_cone` 64.7%, `short_shuttle` 60.6%, `bench_reps` 62.4%, `forty` 16.9% null. Logged as a fixed bug.

## D7 — Figure cap: adopt the stricter internal limit · 2026-10-09
**Decision.** Cap figures at **≤5** and the writeup at **≤2,000 words**.
**Why.** Official rules say "no more than 2,000 words and fewer than 10 tables or figures"; `TASK.md` imposes 5 figures. The stricter cap satisfies both. No rule conflict (a stricter self-limit is not a conflict).

## D8 — Sample discipline · 2026-10-09
**Decision.** Every sample-mode artifact carries the string `UNVALIDATED SAMPLE OUTPUT` (a `provenance` column in each table + `outputs/audit/PROVENANCE.txt` + banners). Never interpret/tune against sample results. Game-side audit (e) is **skipped** in sample mode and marked **PENDING FULL RUN**.
**Why.** TASK.md rules; the game file is a non-representative 2²⁰-row prefix (schema.md §5).

## D9 — External data: default OFF · 2026-10-09
**Decision.** Use only the provided 2027 dataset in the primary pipeline. If an external source is ever used it must be cited, rule-compliant and logged here first.
**Why.** Official rules permit external data (with obligations), but `TASK.md` says "no external data unless permitted"; default-off is the safe reading.

## D10 — Attempt-numbering gaps treated as lost data (Phase 2 plan) · 2026-10-09
**Decision.** Any gap in attempt numbering = **lost data**. Impute **load only** (add the drill-level median cost), flag imputed rows, and exclude them from performance observations (TASK.md Phase 2).
**Evidence (audit g):** overall 32.1% of player-drill groups have a numbering gap; for the 40-yd dash by position: DB 27.5% (17.6% missing attempt 1), DL 25.8%, WR 17.0%, TE 17.6%, OL 6.3%. Non-trivial → imputation and the robustness refit (observed-only load) both matter.

## D11 — Time zone unverified · 2026-10-09
**Decision.** Treat `time` as **naive, assumed UTC** (de-risk memo: on-field testing at clock hours ≈13:00–21:00 US-Eastern). Do not build any exposure on absolute time-of-day until verified.
**Evidence (audit a):** timestamps carry no TZ offset (`Z`/`+hh:mm` absent).

---

## Gate decision (Phase 1)

| Gate condition (TASK.md) | Result |
|---|---|
| Required timing field present | **PASS** — `time` (TIMESTAMP) + `attempt` present; attempt~time agreement 0.9992 (1/1316 pairs off) |
| A position group qualifies | **PASS** — DB, 40-yd dash (D3) |

**VERDICT: GO.** Proceed to Phase 2 (Combine features) for DB / 40-yard dash.
**Recorded caveat:** the fatigue effect is **not** identified from drill *order* (D5); the study's credibility rests on within-drill repeat attempts, load/rest covariates, and honest reporting of a possible null. Phase 3 has a pre-registered reliability stop (<0.2 → report null).

*No blocker raised (see `blockers.md`).*
