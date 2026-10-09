# Competition rules & limits — NFL Big Data Bowl 2027

**Purpose:** Phase 0 summary of the competition's rules, limits and requirements that constrain this project.
**Captured:** 2026-10-09. **Sources are tagged; the binding internal spec is `TASK.md`.**

> ⚠️ Kaggle competition pages are JS-rendered and returned no body to `web_fetch`; the exact
> official text below was captured from the **live Kaggle page content surfaced via search-result
> snippets** (DuckDuckGo-lite) and corroborated by secondary mirrors. **Verify against
> `kaggle.com/competitions/nfl-big-data-bowl-2027/{overview,rules}` at entry time** before relying
> on any cap. No number below is invented.

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
| Prize pool | ~$100,000 total (Open-track + University-track prizes + grand prize) | Primary (operations.nfl.com: "$100,000 in prize money"); FOS also states "$100,000 shared prize" |

## 2. Submission format & limits (the hard caps)

- **Word limit:** "Submissions should contain **no more than 2,000 words**." — *primary (Kaggle overview snippet).*
- **Figure/table limit:** "**fewer than 10 tables or figures**." — *primary (Kaggle overview snippet).*
  - ⚠️ **Note vs our spec:** `TASK.md` imposes a **stricter 5-figure cap**. We follow the stricter
    internal cap (≤5), which automatically satisfies the official "<10". (Recorded in `decisions.md`.)
- **Penalty:** "Submissions over these limits may be subject to penalty." — *primary.*
- **Notebook requirement:** Each entry needs "readable markdown, embedded visuals, and an **attached
  Public Kaggle Notebook**." The writeup + a **public Kaggle notebook** are a **pass/fail gate**.
  — *primary (Kaggle overview snippet); gate corroborated by prior-work scan.*
- **Code location:** deliverable is a Kaggle Notebook (public). Our repo `src/` is the development
  source; the notebook must import/execute the same logic.

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

- Deadline **2027-01-06 23:59 UTC**; today (cycle start) **2026-10-09** → ~89 days.
- Two tracks to enter (Open and University); we target **Open** (mixed/unknown eligibility — confirm).

## 6. Open items to confirm at entry time (do not assume)

1. Exact 2027 wording of the word/figure caps (we hold ≤2,000 words, ≤5 figures to be safe).
2. Whether the rubric weighting is unchanged for 2027.
3. Formal external-data terms and any per-source restrictions.
4. Whether finalist travel is required for prize eligibility (a public discussion thread asks this).

*No competition-rule conflict forces a stop; see `blockers.md` (none active).*
