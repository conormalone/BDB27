# Competition rules & limits — NFL Big Data Bowl 2027

**Binding internal spec:** `TASK.md` (the Combine does not test the shallow break), reduced
2026-11-13 (D191). The superseded spec is archived at
`notes/archive-tasks/TASK-turn-coverage-cut-card-2026-11-13.md`.
**Re-verified:** 2026-11-06 on the Raspberry Pi. The 2026-11-05 version of this file
described the superseded burst-vs-jump task; the limit register carried over because
the competition did not change.

> **The official rules are still not machine-readable from this host.** On
> 2026-11-06 `https://www.kaggle.com/competitions/nfl-big-data-bowl-2027/overview`
> returned HTTP 200 with **31 characters of extractable text**
> (`NFL Big Data Bowl 2027 | Kaggle`). The Kaggle API returns 401 (no credentials
> here). Every limit that is not quoted from `TASK.md` is marked **UNVERIFIED**,
> with the exact URL to check in an authenticated browser. Nothing below is
> promoted from a blog or a third-party repo.

---

## 1. Evidence of the fetch

| URL | HTTP | bytes | extractable text | rules content |
|---|---:|---:|---|---|
| `/competitions/nfl-big-data-bowl-2027/overview` | 200 | 5,772 | 31 chars | none |
| `/competitions/nfl-big-data-bowl-2027/rules` (2026-11-05) | 200 | 5,772 | same shell | none |
| `/api/v1/competitions/list?search=big+data+bowl` (2026-11-05) | **401** | 40 | `{"code":401,"message":"Unauthenticated"}` | n/a |

The only official machine-readable sentence is the page meta description:

```
Uncover non-obvious linkages between Combine sensor tracking and regular-season
NFL game performance
```

That is consistent with this project's question, and it says nothing about caps.

## 2. Limit register

| # | Limit | Value used here | Provenance |
|---|---|---|---|
| L1 | Writeup word cap | **2,000 words** as the working ceiling | **UNVERIFIED for 2027** — widely reported for earlier editions; not readable on the 2027 page. Check `/rules` signed in. |
| L2 | Figure cap | **4 figures** | **Binding from `TASK.md`** ("exactly four, and no others", D191). The earlier 5-figure cap came from the superseded spec; the Kaggle cap is unverified and higher. |
| L3 | Notebook requirements | Public Kaggle notebook, runs end to end, no internet assumed, outputs committed | **UNVERIFIED as to runtime/CPU/GPU/internet policy**; `TASK.md` requires a public notebook. |
| L4 | External data | **Prohibited** | **Binding from `TASK.md`** ("No external data") and consistent with the standard Kaggle rule. |
| L5 | Deadline | **2027-01-06 23:59 UTC** | From `TASK.md` (supplied by Conor); **not re-verified on Kaggle**. |
| L6 | Team size / submissions | Not used | Unknown; irrelevant for a solo analysis entry. |
| L7 | Data licence / redistribution | Do not redistribute raw data | Kaggle terms not readable here; `data/` is gitignored regardless. |

## 3. Consequences for this repo

1. **No external data, ever.** `data/raw/external/gtm/` survives from an earlier
   task and is **not read by any script** in this pipeline. It must not be.
2. **Five figures, fixed.** The deliverable plan in `TASK.md` Phase 7 names exactly
   five: Cut Card, demand map + feasible region, transfer scatter with noise
   ceiling, feasibility by speed range, held-out outcome comparison.
3. **Reproducible from the competition data alone.** Every stage reads only
   `data/raw/*.csv` and writes only `data/interim/` + `outputs/`, so the notebook can
   be generated from stage outputs rather than ad-hoc cells.
4. **The word budget is a design constraint.** 2,000 words is a working assumption;
   the section list fits if results are reported as intervals.

## 4. How to close the gap

```bash
kaggle competitions list --search "big data bowl"          # confirms the slug
curl -sS -b cookies.txt https://www.kaggle.com/competitions/nfl-big-data-bowl-2027/rules
```

Then update L1, L3, L6, L7 in place with the fetch date. Until then, **do not** quote
a word cap as an official rule in the writeup.
