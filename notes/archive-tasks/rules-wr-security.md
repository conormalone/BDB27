# Competition rules & limits — NFL Big Data Bowl 2027

**Purpose:** Phase 0 summary of the competition's rules, limits and requirements that constrain this project.
**Captured:** 2026-10-09 (combine-fatigue task); **retained 2026-10-11** for the ceiling-binding task — the
competition rules are unchanged. **Binding internal spec = `TASK.md`.** **Updated 2026-10-17** to record the
**security/stakes EDP board** external data (provenance, sha256, permission) — see §7.

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
| **Final deadline** | **2027-01-06 23:59 UTC** | Primary (TASK.md; Kaggle) |
| Results | ~late Jan 2027; finalists present at the 2027 NFL Scouting Combine (Indianapolis) | Primary (operations.nfl.com) |
| Positions in scope | WR, TE, OL, DL, DB (5 groups) | Primary (Front Office Sports; Kaggle) |
| Data years | 2023, 2024, 2025 combine classes; matching in-game tracking | Primary (Front Office Sports) |
| Prize pool | ~$100,000 total (Open-track + University-track prizes + grand prize) | Primary (operations.nfl.com) |

## 2. Submission format & limits (the hard caps)

- **Word limit:** "Submissions should contain **no more than 2,000 words**." — *primary (Kaggle overview snippet).*
- **Figure/table limit:** "**fewer than 10 tables or figures**." — *primary (Kaggle overview snippet).*
  - ⚠️ **Note vs our spec:** `TASK.md` imposes a **stricter 5-figure cap**. We follow the stricter
    internal cap (≤5), which automatically satisfies the official "<10". (Recorded in `decisions.md`.)
- **Penalty:** "Submissions over these limits may be subject to penalty." — *primary.*
- **Notebook requirement:** Each entry needs "readable markdown, embedded visuals, and an **attached
  Public Kaggle Notebook**." The writeup + a **public Kaggle notebook** are a **pass/fail gate**.
  — *primary (Kaggle overview snippet); gate corroborated by prior-work scan.*
- **Code location:** the deliverable is a **public Kaggle Notebook**. Our repo `src/` is the development
  source; the notebook must import/execute the same logic. (This task's deliverable model: a scripts
  engine in `src/` + a Kaggle notebook that runs it — see `README.md` / `README_RUN.md`.)

## 3. Judging

- Four components scored **0–10** each; rubric weighting **Football 30 / Data Science 30 /
  Writeup 20 / Visualization 20** (documented for 2025 and 2026; assume it holds until the 2027
  rules say otherwise — *secondary, chasko-labs mirror*).
- One example criterion quoted on the page: *"Would NFL teams (or the league office) be able to use
  these results on a week-to-week basis?"* — *primary.*
- Judges are NFL club analytics staff and tracking vendors (domain experts) — *secondary.*
- **Two pass/fail gates:** (1) an explicit Combine→NFL linkage; (2) a public Kaggle notebook + writeup.
  Failing a gate means not scored, regardless of content — *secondary (chasko mirror); consistent with README.*

## 4. External data policy

- The Competition Rules contain a **Section on External Data** and state that using External Data
  "does not limit your other obligations under these Competition Rules, including … Section 2.8
  (Winners Obligations)", i.e. **external data is permitted, with obligations** — *primary
  (Kaggle /rules snippet).*
- **Our stance (stricter):** `TASK.md` says "No external data unless permitted." Since the official
  rules permit it, external data **is** allowed, **but we default to using only the provided 2027
  dataset**, and any external source (e.g. PFR combine history, nflverse) must be (a) cited, (b)
  consistent with the rules, and (c) logged in `decisions.md` first. Third-party repos are
  **unverified hints**, never a source of truth.

## 5. Timeline / logistics (for planning)

- Deadline **2027-01-06 23:59 UTC**; as of this restructure (2026-10-11) → **~87 days** remaining.
- Two tracks to enter (Open and University); we target **Open** (mixed/unknown eligibility — confirm).

## 6. External data — the EDP **board** (security/stakes axis) + permission  ·  2026-10-17

The stakes task (`TASK.md`, Phase 4) needs a **pre-Combine expected-draft-position (EDP) board** to build the
security exposures. It is external data (see §4) and is **permitted**.

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
- **Redistribution:** the board file is **NOT committed** (`data/` is gitignored); the spec says do not commit
  the board if redistribution is restricted. Only code, config, notes, tests and synthetic fixtures are shipped.
- **Use:** pre-Combine snapshots only as exposures (2-weeks- and 1-week-before); `Combine Week` and the
  post-Combine snapshots are **outcomes**, never exposures. Provenance/sha256 are asserted in code
  (`src/security_features.py::verify_board`) and recorded in `notes/schema.md` §9.

## 7. Open items to confirm at entry time (do not assume)

1. Exact 2027 wording of the word/figure caps (we hold ≤2,000 words, ≤5 figures to be safe).
2. Whether the rubric weighting is unchanged for 2027.
3. Formal external-data terms and any per-source restrictions.
4. Whether finalist travel is required for prize eligibility (a public discussion thread asks this).

*No competition-rule conflict forces a stop; see `blockers.md` (none active).*

## 8. Post-treatment variables are diagnostics, never §8 controls  ·  2026-10-19

- **Draft slot is post-Combine.** It may be used to *describe* realised draft capital or as an
  exploratory conditioning variable, but never as a clean control in the pre-registered model.
  Conditioning on it can open a collider; every such fit is labelled a mechanism/diagnostic probe.
- **The target flag is post-snap and role-dependent.** `target == True` is selection on QB, scheme
  and depth chart, not a randomisation. Target-only fits are decomposition/diagnostics, never a clean
  group effect.
- **The clean group estimate remains block B on all rows.** If a post-treatment split is reported,
  the write-up must say what it can and cannot license and must not enter §8.
