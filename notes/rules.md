# Competition rules & limits — NFL Big Data Bowl 2027

**Purpose:** Phase 0 summary of the competition's rules, limits and requirements that constrain this project.
**Captured:** 2026-10-09 (combine-fatigue task); **retained 2026-10-11** for the ceiling-binding task;
**refreshed 2026-10-26** for the active task — **"In-game decay of DB performance relative to the Combine
ceiling"** (`TASK.md`, rewritten 2026-10-25; `notes/decisions.md` D51).
**Binding internal spec = `TASK.md`.** The prior WR/TE ("security") version of this file is archived verbatim
at `notes/archive-tasks/rules-wr-security.md`.

> ⚠️ Kaggle competition pages are JS-rendered and returned no body to `web_fetch`; the exact
> official text below was captured from the **live Kaggle page content surfaced via search-result
> snippets** and corroborated by secondary mirrors. **Verify against
> `kaggle.com/competitions/nfl-big-data-bowl-2027/{overview,rules}` at entry time** before relying
> on any cap. No number below is invented. **Source of truth = the official competition docs**;
> third-party repos (e.g. the chasko-labs history repo) are **unverified hints**.

---

## 1. At a glance

| Item | Value | Source / confidence |
|---|---|---|
| Competition | NFL Big Data Bowl 2027 (9th annual), hosted on Kaggle, powered by AWS | Primary (operations.nfl.com) |
| Theme | "Uncover non-obvious linkages between **10 Hz Combine sensor tracking** and regular-season NFL game performance" | Primary (Kaggle overview snippet) |
| Sign-ups opened | 2026-10-06 (Tue) | Primary (Kaggle page date) |
| **Final deadline** | **2027-01-06 23:59 UTC** | Primary (`TASK.md` header; Kaggle) |
| Results | ~late Jan 2027; finalists present at the 2027 NFL Scouting Combine (Indianapolis) | Primary (operations.nfl.com) |
| Positions in scope (competition) | WR, TE, OL, DL, DB (5 groups) | Primary (Front Office Sports; Kaggle) |
| **Positions in scope (this entry)** | **DB only** — `CB`, `SS`, `FS`, `DB` as coded in `players.nfl_position`; n = 118 DBs with Combine tracking (`outputs/audit/db_decay/a_db_coverage.csv`) | Internal (`TASK.md`; audit a) |
| Data years | 2023, 2024, 2025 combine classes; matching in-game tracking | Primary (Front Office Sports) |
| Prize pool | ~$100,000 total (Open-track + University-track prizes + grand prize) | Primary (operations.nfl.com) |

**Scope note.** The competition allows five position groups; nothing in the rules requires using all of them.
This entry uses **DB only**, which is a deliberate narrowing, not a rules constraint. The one-entry rule
(internal: a single Kaggle submission) is recorded in D51.

## 2. Submission format & limits (the hard caps)

- **Word limit:** "Submissions should contain **no more than 2,000 words**." — *primary (Kaggle overview
  snippet).* The internal writing target is the same 2,000 words (`TOOLS.md` writer brief; `TASK.md` §6).
- **Figure/table limit:** "**fewer than 10 tables or figures**." — *primary (Kaggle overview snippet).*
  - ⚠️ **Note vs our spec:** `TASK.md` §6 imposes a **stricter 5-figure cap**. We follow the stricter
    internal cap (≤5), which automatically satisfies the official "<10". (Recorded in `decisions.md`.)
- **Penalty:** "Submissions over these limits may be subject to penalty." — *primary.*
- **Notebook requirement:** Each entry needs "readable markdown, embedded visuals, and an **attached
  Public Kaggle Notebook**." The writeup + a **public Kaggle notebook** are a **pass/fail gate**.
  — *primary (Kaggle overview snippet); gate corroborated by prior-work scan.*
- **Code location:** the deliverable is a **public Kaggle Notebook**. Our repo `src/` is the development
  source; the notebook must import/execute the same logic. (This task's deliverable model: a scripts
  engine in `src/` + a Kaggle notebook that runs it — see `README.md` / `README_RUN.md`.)

**UNVERIFIED — confirm against the official Kaggle competition rules:**

1. **Reproducibility obligation.** The captured text requires a public notebook but does **not** quote a
   reproducibility requirement (fixed seeds, deterministic re-run, no hidden steps). We impose fixed seeds
   and scripts-not-cells internally (`TASK.md` Rules), which is our own standard, **not** a verified
   competition rule.
2. **Runtime / external-package limits** on the Kaggle notebook (e.g. whether the notebook must run within
   the Kaggle image without `pip install`, or whether internet must be disabled). Not captured.
3. **Whether a notebook re-run by judges from the attached version is required**, or only the writeup plus
   a public notebook link.

## 3. Judging

- Four components scored **0–10** each; rubric weighting **Football 30 / Data Science 30 /
  Writeup 20 / Visualization 20** (documented for 2025 and 2026; assume it holds until the 2027
  rules say otherwise — *secondary, chasko-labs mirror*).
- One example criterion quoted on the page: *"Would NFL teams (or the league office) be able to use
  these results on a week-to-week basis?"* — *primary.*
- Judges are NFL club analytics staff and tracking vendors (domain experts) — *secondary.*
- **Two pass/fail gates:** (1) an explicit Combine→NFL linkage; (2) a public Kaggle notebook + writeup.
  Failing a gate means not scored, regardless of content — *secondary (chasko mirror); consistent with README.*

**Relevance to this entry.** Gate (1) is structural for the DB-decay question: the Combine ceiling *is* the
linkage, so the entry satisfies it by construction rather than by a separate argument. Gate (2) is the
notebook + writeup; both exist as placeholders until the full run (`TASK.md` Phase 6).

## 4. External data policy

- The Competition Rules contain a **Section on External Data** and state that using External Data
  "does not limit your other obligations under these Competition Rules, including … Section 2.8
  (Winners Obligations)", i.e. **external data is permitted, with obligations** — *primary
  (Kaggle /rules snippet).*
- **Our stance (stricter):** `TASK.md` says "No external data unless permitted." Since the official
  rules permit it, external data **is** allowed, **but we default to using only the provided 2027
  dataset**, and any external source must be (a) cited, (b) consistent with the rules, and (c) logged in
  `decisions.md` first. Third-party repos are **unverified hints**, never a source of truth.
- **This entry uses no external data.** The DB-decay task reads only the provided files; the EDP board in
  §6 is legacy for the archived security task and is not read by the DB-decay pipeline.

**UNVERIFIED — confirm against the official Kaggle competition rules:** the exact 2027 wording of the
External Data section (the quoted sentence is from the 2026-era rules page) and whether any per-source
restriction applies to derived tracking metrics.

## 5. Timeline / logistics (for planning)

- Deadline **2027-01-06 23:59 UTC**; as of this refresh (2026-10-26) → **72 days** remaining.
- Two tracks to enter (Open and University); we target **Open** (mixed/unknown eligibility — confirm).

## 6. External data — the EDP **board** (security/stakes axis) + permission  ·  2026-10-17  ·  **LEGACY**

> **Status: not part of the active task.** Retained verbatim for provenance of the archived WR/TE security
> work (D37/D38, `notes/archive-tasks/TASK-wr-security-2026-10-20.md`). The DB-decay pipeline does not read
> this file and no DB-decay claim depends on it. Do not delete.

The stakes task (archived `TASK.md`, Phase 4) needed a **pre-Combine expected-draft-position (EDP) board** to
build the security exposures. It is external data (see §4) and is **permitted**.

| Item | Value |
|---|---|
| Local file | `data/raw/external/gtm/GTM_BDB_EDP_Snapshots_20261016.csv` (a `.xlsx` twin exists; only the CSV is read) |
| sha256 | `4664b369e56dfcbe62de678932059695c2c5b8b128989fa5a9068994857563b4` |
| Rows / cols | 3,625 data rows · 13 columns (order pinned in `config.yaml`, asserted on read) |
| Date format | `DD/MM/YYYY` (day values 14/21/28 force DMY; parsed with an explicit `dateformat`) |
| Scope | draft years 2023–2025; `snapshot_note` ∈ {2 Weeks Pre-Combine, 1 Week Pre-Combine, Combine Week, 1 Week Post-Combine, 2 Weeks Post-Combine} |
| Join key | `nfl_id` (exact join only — **never** fuzzy-name matching) |

- **Permission:** the official Competition Rules have an **External Data** section permitting external data
  with citation/obligations (§4 above). **Conor confirmed the board is permitted (2026-10-17).** Recorded in
  `config.yaml` (`external.board.permitted: true`) and `notes/external_data_manifest.md`.
- **Redistribution:** the board file is **NOT committed** (`data/` is gitignored); only code, config, notes,
  tests and synthetic fixtures are shipped.

## 7. Open items to confirm at entry time (do not assume)

1. **UNVERIFIED — confirm against the official Kaggle competition rules:** exact 2027 wording of the
   word cap (we hold ≤2,000 words) and the figure/table cap (we hold ≤5 figures, stricter than the
   captured "<10").
2. **UNVERIFIED — confirm against the official Kaggle competition rules:** whether the rubric weighting
   (Football 30 / Data Science 30 / Writeup 20 / Viz 20) is unchanged for 2027.
3. **UNVERIFIED — confirm against the official Kaggle competition rules:** formal external-data terms and
   any per-source restrictions for 2027.
4. **UNVERIFIED — confirm against the official Kaggle competition rules:** whether the submitted notebook
   must be reproducible by judges (seeded / no hidden state) or only public and attached.
5. Whether finalist travel is required for prize eligibility (a public discussion thread asks this).

*No competition-rule conflict forces a stop; see `blockers.md` (none active).*

## 8. Post-treatment variables are diagnostics, never clean controls  ·  2026-10-19  ·  retained

- **Draft slot is post-Combine.** It may be used to *describe* realised draft capital or as an
  exploratory conditioning variable, but never as a clean control in a pre-registered model.
  Conditioning on it can open a collider; every such fit is labelled a mechanism/diagnostic probe.
- **In this task** draft slot is **not** a model control at all: the ceiling is the Combine measurement and
  the estimand is within-player effort decay. `draft_overall_pick` NULLs are the structural "undrafted"
  level (24/118 DBs), useful only for describing the population.
- **`target == True` is post-snap and role-dependent.** Target-only fits are decomposition/diagnostics,
  never a clean group effect. In the DB-decay design `target` is not a control; the max-effort subset is
  defined by the ball-in-air/pursuit window (§9), which is a post-snap definition and is stated as such.

## 9. Scope-specific limits — DB-decay task  ·  2026-10-26

These are the limits that actually bind this entry. Each is traceable to `TASK.md` or to the audit evidence
named; none is invented.

1. **Population (claim limit).** Claims are limited to **DBs who reached the NFL and appear in game data**.
   Combine tracking covers 118/118 DBs (`a_db_coverage.csv`); 115 appear in `player_play.csv`
   (`c_matched_levels.csv`). Of the 118, **94 carry a draft slot and 24 do not** (`players.csv`;
   `draft_overall_pick`/`draft_round` NULL = structural "undrafted" level per `limitations.md` §10) — and
   **all 24 undrafted DBs do appear in `player_play`**. So the selection is "reached the NFL and played",
   **not** "was drafted". The writeup must say this in those words; "draftee-only" is inaccurate for this
   data. Tracked-in-`game_tracking` (frame-level) is the narrower number: **33** in the sample
   (`c_matched_levels.csv`) and **PENDING FULL RUN** for the full seasons.
2. **Sample vs full.** Frame-level game tracking on this Pi is a **1,048,575-row (2²⁰−1) cap over 2023-class
   players only**, with scattered play coverage (`d_sample_draw.csv`, `d_sample_blocked_paths.csv`). Sample
   outputs are stamped `UNVALIDATED SAMPLE OUTPUT` and can **never** produce a reportable number (D57).
   Every headline number in the writeup must come from a `--mode full` run with thresholds provenance
   `FULL`.
3. **One stale ceiling is not a moving target.** The Combine file is complete, so the ceiling can be
   estimated for real now; the game side cannot. The writeup must therefore not mix Combine-complete
   numbers with sample game numbers as if both were final.
4. **Data years.** 2023/2024/2025 combine classes with their NFL games. Season scope follows D49:
   **REG + POST**, preseason excluded. The 2025 class has the shortest follow-up (`limitations.md`).
5. **Deliverable shape.** One public Kaggle notebook + one writeup, ≤2,000 words, ≤5 figures, both produced
   from `src/` scripts rather than ad-hoc cells. Result placeholders stay placeholders until the full run.
6. **No external data** is used by this entry (§4), so the external-data obligations are not engaged.
