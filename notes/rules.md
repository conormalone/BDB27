# Competition rules & limits — NFL Big Data Bowl 2027

**Task:** leverage × ceiling (WR primary, TE replication). Binding internal spec:
`TASK.md`. This file records what the official competition pages actually served
on **2026-10-26**, and separates verified facts from claims we cannot check.

> **Bottom line: the official rules text is not machine-readable from this
> host.** All three competition pages return HTTP 200 but only a ~6 kB
> JavaScript shell; the Kaggle API returns 401. Every cap below that is not
> quoted from a readable official body is marked
> `UNVERIFIED — official page not machine-readable`. Third-party blog/repo
> claims are **not** promoted to facts.

## 1. What was fetched (verbatim evidence)

All requests: `curl -sS -L --max-time 30 -A "Mozilla/5.0 (X11; Linux aarch64)"`,
2026-10-26, from the gateway host.

| URL | HTTP | bytes | content-type | readable body after stripping `<script>/<style>/<tags>` | rules text found |
|---|---|---|---|---|---|
| `https://www.kaggle.com/competitions/nfl-big-data-bowl-2027/overview` | 200 | 6000 | `text/html; charset=utf-8` | 31 chars: `NFL Big Data Bowl 2027 | Kaggle` | none |
| `https://www.kaggle.com/competitions/nfl-big-data-bowl-2027/rules` | 200 | 6000 | `text/html; charset=utf-8` | 31 chars: `NFL Big Data Bowl 2027 | Kaggle` | none |
| `https://www.kaggle.com/competitions/nfl-big-data-bowl-2027/data` | 200 | 5945 | `text/html; charset=utf-8` | 31 chars: `NFL Big Data Bowl 2027 | Kaggle` | none |
| `https://www.kaggle.com/api/v1/competitions/list?search=big+data+bowl` | **401** | 40 | `application/json` | `{"code":401,"message":"Unauthenticated"}` | n/a |

Search for the usual rule vocabulary in the `/overview` body:
`2,000` → 0 hits, `words` → 0, `figures` → 0, `tables` → 0, `deadline` → 0,
`External Data` → 0, `January` → 0. The only machine-readable competition text
is the meta description:

```
<meta name="description" content="Uncover non-obvious linkages between Combine
sensor tracking and regular-season NFL game performance">
```

A second fetch with Python `urllib` (same UA) returned 200 / 6055, 5945 and 6000
bytes for overview/rules/data, i.e. the same shell. So this is not a one-off
truncation: the rules are client-rendered and require an authenticated browser
or the Kaggle API with credentials.

## 2. Caps and obligations

Legend: **V** = verified from a machine-readable official body on this date
(nothing below is V); **U** = `UNVERIFIED — official page not machine-readable`;
**I** = internal decision in `TASK.md`, binding on us regardless of the official cap.

| Item | Value we hold | Status | Where it comes from |
|---|---|---|---|
| Writeup word cap | **2,000 words** | U | carried forward from the 2026-era page capture; not re-readable today |
| Figure/table cap (official) | **fewer than 10** | U | carried forward; not re-readable today |
| Figure cap (this entry) | **≤ 5 figures** | I | `TASK.md` §6 — stricter than the carried-forward official cap |
| Public notebook required | **yes, attached public Kaggle notebook** | U | carried forward; corroborated by prior entries but not re-read today |
| External data | **permitted with obligations** in the 2026-era rules | U | carried forward; exact 2027 wording not readable |
| Our external-data stance | **none used** | I | `TASK.md` Rules + Phase 3; only the provided 2027 files |
| Deadline | **2027-01-06 23:59 UTC** | U (official) / I (binding) | `TASK.md` header; official page not readable |
| Prize pool | ~**$100,000** total (Open + University + grand prize) | U | carried forward from operations.nfl.com / prior captures |
| Entry limit | one entry per team (our assumption) | U | not re-readable |
| Scoring rubric | Football 30 / Data Science 30 / Writeup 20 / Visualization 20 | U | secondary mirrors only — do not present as official |

**Do not launder.** The numbers `2,000`, `<10`, `$100,000` and the rubric split
appear in older internal notes and in third-party write-ups. They are plausible
and we plan against them, but none was readable on the official page today.
At submission time, re-check them in an authenticated browser and update this
file with the exact quote and the page section.

## 3. What this means for the entry

1. **We design to the stricter limits anyway.** ≤2,000 words and ≤5 figures are
   safe under either the carried-forward official caps or a stricter 2027 rule.
   The five figures are the only committed images (`outputs/figures/*.png`).
2. **No external data.** The pipeline reads only `data/raw/*.csv`. No external
   win-probability or expected-points model is used; leverage is derived from
   the provided `pre_snap_*_win_probability` columns. This satisfies the
   external-data section under either reading and keeps the audit honest.
3. **Deliverable shape.** One public Kaggle notebook + writeup, produced from
   the `src/` scripts rather than ad-hoc cells. Result placeholders stay
   placeholders until a `--mode full` run with thresholds provenance `FULL`.
4. **Gates we impose on ourselves** (internal, not claimed as official):
   scripts 01–05 must run end-to-end in `--mode sample`; every sample artefact
   says `UNVALIDATED SAMPLE OUTPUT`; no number is reported from sample data.

## 4. Open items to confirm in an authenticated browser (not assumptions)

1. Exact 2027 word cap and whether it counts captions/references.
2. Exact 2027 figure/table cap and how tables are counted.
3. Whether an attached **public** notebook is pass/fail, and whether judges
   re-run it (reproducibility obligation).
4. External-data section: exact wording, citation duties, and whether derived
   tracking metrics count as external data.
5. Deadline/timezone and prize/eligibility rules (travel, team size, tracks).
6. Any 2027 change to the scoring rubric.

Until items 1–6 are read from the official page, treat every number in §2
marked U as provisional. No competition-rule conflict currently forces a stop;
`notes/blockers.md` records the readable-page gap as an accepted risk.
