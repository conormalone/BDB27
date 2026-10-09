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
> ⚠️ The **drill choice** below (`FORTY_YARD_DASH`) is **SUPERSEDED by D12** (family = ALL drills, 2026-10-09). The **position-group choice** (DB) is **CONFIRMED by D13** (population = **DB only, LOCKED**, 2026-10-09; WR excluded). Kept for history.

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
| Required timing field present | **PASS** — `time` (TIMESTAMP) + `attempt` present; attempt~time agreement 0.9992 (1/1315 pairs off, 1 violation) |
| A position group qualifies | **PASS** — *(superseded by D12/D13: family = ALL drills, population = DB only)* — original Cycle-1 result: DB, 40-yd dash (D3) |

**VERDICT: GO.** ~~Proceed to Phase 2 (Combine features) for DB / 40-yard dash.~~ **SUPERSEDED by D12/D13** (family = ALL drills; population = DB only, LOCKED) — see the Cycle-2 section below.
**Recorded caveat:** the fatigue effect is **not** identified from drill *order* (D5); the study's credibility rests on within-drill repeat attempts, load/rest covariates, and honest reporting of a possible null. Phase 3 has a pre-registered reliability stop (<0.2 → report null).

*No blocker raised (see `blockers.md`).*

---

# Cycle 2 — all-drills family + position scope (supersedes D3's drill choice)

> Process note (extends D2): this cycle was again executed **inline by the PM** — no `sessions_spawn`/child agents at depth 1/1. Changes were **self-reviewed inline**; an **independent review will be run separately by Main**. All sample artefacts remain stamped `UNVALIDATED SAMPLE OUTPUT`.

## D12 — Drill family = **ALL drills** (supersedes D3's drill choice); "≥3 observed attempts" read as TOTAL across the family · 2026-10-09
**Decision (human, supersedes D3's drill pick).** The repeated maximal-effort drill **FAMILY = ALL drills** — every `drill_type` (`FORTY_YARD_DASH`, `THREE_CONE_DRILL`, `SHORT_SHUTTLE`, and the position-specific `SKILL_DRILLS_{WR,DB,DL,OL,TE,LB}` blocks), **pooled**. This supersedes D3's choice of `FORTY_YARD_DASH` alone. D3's *position-group* choice is **confirmed by D13** (population = DB only, LOCKED; WR excluded).
**Why (Conor, 2026-10-09).** (i) The 40 alone caps at **≤2 attempts/player** (audit b: DB 102 runners, max 2), so it can never reach the ≥3 observed attempts the Phase 5 link requires; pooling the whole family lifts per-player counts past 3. (ii) The Phase-3 model already keys on `C(drill)` and Phase 2 standardises performance **within drill**, so pooling *different* drills is statistically defensible — drill identity is absorbed by the fixed effect.
**Reading of the spec's "≥3 observed attempts per player" (recorded explicitly — ambiguous, chosen reading stated):** read as **TOTAL observed attempts across the family**, **not** per single drill. Chosen because (a) the human rationale above only holds under pooling; (b) the Phase-3 LMM fits `(1 + load | player)` over *all* of a player's attempts with `C(drill)` as a factor, so "attempts per player" is naturally the pooled row count; (c) a per-drill reading would permanently disqualify the 40-yd dash (max 2) and leave only a handful of `drill_name`s with ≥3, contradicting the decision to broaden the family. Encoded as `audit.min_attempts_for_link: 3` + `audit.attempt_count_unit: event_id`.
**"Observed attempt" definition.** One observed `event_id` (schema.md: "one `event_id` = one drill attempt"); excludes imputed attempts (Phase 2; none exist at audit stage). **Robustness:** ≥3 counts are **identical** under `event_id` vs distinct `(drill_name, attempt)` slots (verified per position for both families), so the A5r duplicates below do not change the threshold.
**Evidence (audit h).** Under the all-drills family **100%** of players in every position group reach ≥3 observed attempts — DB 122/122, DL 118/118, OL 121/121, TE 42/42, WR 107/107 (median 10–17). The ≥3 constraint is therefore **non-binding** under all-drills; position scope is decided by design/power instead (D13).
**Caveat (limitation, logged).** The all-drills family is broad: the `SKILL_DRILLS_*` sub-drills include route/technique drills of heterogeneous intensity, so "maximal-effort" is a looser construct than the timed battery. Kept because the human decision is explicit and the model handles it via `C(drill)` + within-drill standardisation; the tighter alternative (**timed_battery** = 40 + 3-cone + shuttle) is pre-defined in `config.yaml` for sensitivity.

## D13 — Position scope: **DB only, LOCKED** (human decision; WR excluded) · 2026-10-09
**Decision (human, Conor 2026-10-09; supersedes the earlier DB+WR draft).** The study population is **DB only**. Family = ALL drills (D12). WR is **EXCLUDED**. Encoded in `config.yaml` as `audit.study_population: DB` and returned by `recommend()` with `position_group_locked: True`. Evidence: `outputs/audit/h_family_scope.csv`.
**Why DB only / why WR is excluded.** DB is a high-intensity, high-speed in-game role that matches Phase 4's high-intensity-decay question. WR in-game intensity is **scheme-dependent** (route depth / target share vary by offensive system), so pooling WR would inject **between-system noise** into the decay signal. A **single** position group also keeps the day≈position design clean (limitations #2) and **satisfies TASK.md Phase 1's "Select ONE position group"** — so this is **not** a deviation from the spec.
**Evidence (audit h; combine-side = full combine data, game-side = PENDING FULL RUN).**
- Family **all_drills** — DB players reaching ≥3 observed attempts: **DB 122/122 (100%)**. The ≥3 link threshold is non-binding for DB.
- Family **timed_battery** — ≥3: DB 26/108 (24.1%).
- Matched Combine→NFL count for DB: **PENDING FULL RUN** (no full game files; the sample is a non-representative 2²⁰ prefix, D8).
**No position terms are pre-registered.** Because DB-only is a *single* position group, the Phase-3 LMM is the spec formula as written — `perf_z ~ load + load^2 + first_attempt + C(drill) + (1 + load | player)` — with no `C(position)` / `load:C(position)` terms. (D14 is withdrawn.) Identification rests on within-drill repeated attempts + load/rest timing (D5).
**If the full-run matched-N for DB is too small for the Phase 5 link**, that is a **blocker** (TASK.md: "no position group qualifies") — write to `blockers.md` rather than silently broadening scope. `h_family_scope.csv` retains the DB-only / DB+WR / DB+WR+DL+OL counts as *evidence*, not as a fallback plan.

## B1/B2/B3/B4 — reviewer lower-priority items closed · 2026-10-09
- **B1 (drill family)** — **resolved by D12** (all-drills); not re-litigated.
- **B2 (audit_b mixed-granularity mislabel)** — **closed.** `audit_b` summed `(nfl_id, drill_name)` series groups but labelled the result `players_ge2_attempts`, so it could exceed `players_with_drill` (DB `SKILL_DRILLS_DB`: 227 vs 122). Every player count is now a **DISTINCT-player** count (`players_ge1/ge2/ge3_attempts`); the series-group count is a separate, correctly-named column `player_drillseries_groups`. Regression test `test_audit_b_fix` asserts `players_ge2_attempts ≤ players_with_drill`.
- **B3 (missing assertions; A5 violated)** — **closed.** `grep assert src/` was empty despite `assumptions.md` claiming runtime assertions. `verify_assumptions()` now implements A1–A12 with real `assert`s (hard invariants fail loudly) and writes `outputs/audit/assumptions_check.csv`. **A5 is genuinely violated:** `event_id → (nfl_id, drill_name, attempt)` is 1:1 (A5f PASS) but **9 tuples map to 2 `event_id`s** (A5r FAIL — duplicate attempt captures, all at `attempt=1`). `assumptions.md` corrected; A5r recorded as a known data-quality finding (Phase 2 is specified to *flag* duplicate attempts, so it is non-fatal). Surfaced **A12**: the `draft_overall_pick`/`draft_round` NULLs are the **structural "undrafted"** category (127/510; the two NULL sets coincide) → **KEEP as a Phase-5 control** (N1 fix; see `assumptions.md` A12 detail).
- **B4 (magic numbers / config hygiene)** — **closed.** Order-flip threshold `0.05` → `audit.order_flip_threshold`; added `audit.min_attempts_for_link: 3` (the spec's Phase-5 requirement, previously absent) and `audit.attempt_count_unit`; moved Phase-2-only keys `session_gap_s`, `peak_speed_sanity_yds` from `audit` → `features`; added `audit.max_combine_bytes` (referenced by A11 but missing) and used it; added `audit.families` + `audit.candidate_populations`. `sample_row_count` is now **enforced** (check **S1**: sample game rows ≤ 2²⁰ cap) rather than decorative. `primary_drill_type` retained but annotated legacy/superseded (D12).

## Cycle-2 gate (re-affirmed)

| Gate condition (TASK.md) | Result |
|---|---|
| Required timing field present | **PASS** — `time` (TIMESTAMP) + `attempt`; agreement 0.9992; assertions A2/A3 PASS |
| A position group qualifies | **PASS** — all five groups qualify under the all-drills family (100% ≥3 observed attempts) |

**VERDICT: GO (unchanged).** Family = ALL drills (D12). Position scope = **DB only, LOCKED** (D13; human decision 2026-10-09; WR excluded — scheme-dependent in-game intensity).
**No blocker raised** — matched-N per population is PENDING FULL RUN, not blocking (see `blockers.md`, `pending_full_run.md`).

## D15 — Deferred TASK.md "Engineering" gaps (dtype downcasting; stage checkpointing) · 2026-10-09
**Decision.** Record two TASK.md "Engineering" requirements that are **not yet applied**, deferred to Phase 2+ (where the tracking feature stage makes them material):
(i) **dtype downcasting** — Parquet is written via DuckDB `COPY` with ZSTD compression but **no explicit float64→float32 / int64→int32 downcast** is applied (TASK.md: "Downcast dtypes").
(ii) **stage-level checkpointing** — only the CSV→Parquet **conversion** is checkpointed (`to_parquet` skips when the cache exists); **stage outputs are not** — re-running `01_audit.py` re-executes every query even when its output tables exist (TASK.md: "Stages checkpoint to disk and skip finished work unless `--force`").
**Why deferred.** At Phase 0/1 the audit is cheap (sample run ≈6 s, ≈380 MB) and the Combine Parquet is small (83.8 MB), so the cost is immaterial; both become material at Phase 2 (per-frame tracking features) and on the full run.
