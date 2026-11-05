# Competition rules & limits — NFL Big Data Bowl 2027

**Binding internal spec:** `TASK.md` (commit `bcfd48b`).
**Re-verified:** 2026-11-05 on the Raspberry Pi. Previous version of this file
(2026-10-26) described the retired leverage × ceiling task; it is superseded.

> **Bottom line: the official competition rules are still not machine-readable
> from this host.** The competition pages return HTTP 200 but serve a ~6 kB
> JavaScript shell with ~31 characters of extractable text. The Kaggle API
> returns 401 (no credentials on this machine). Every limit below that is not
> quoted from a readable official body is marked **UNVERIFIED**, with the exact
> URL to check in an authenticated browser. Nothing here has been promoted from
> a third-party blog or repo.

---

## 1. What was fetched, verbatim evidence

Requests made 2026-11-05 from the gateway host with
`curl -sS -L --max-time 25 -A "Mozilla/5.0 (X11; Linux aarch64)"`.

| URL | HTTP | bytes | extractable text after stripping tags/script/style | rules content |
|---|---:|---:|---|---|
| `https://www.kaggle.com/competitions/nfl-big-data-bowl-2027` | 200 | 5,772 (rules path) / 6,000 (overview) | 31 chars: `NFL Big Data Bowl 2027 | Kaggle` | none |
| `https://www.kaggle.com/competitions/nfl-big-data-bowl-2027/rules` | 200 | 5,772 | same shell | none |
| `https://www.kaggle.com/api/v1/competitions/list?search=big+data+bowl` | **401** | 40 | `{"code":401,"message":"Unauthenticated"}` | n/a |

The only machine-readable official sentence available is the page meta
description:

```
Uncover non-obvious linkages between Combine sensor tracking and regular-season
NFL game performance
```

That sentence is consistent with this project's question and is the one official
statement this file can cite. It says nothing about caps.

**Why this matters.** The spec says "Official competition docs and rules are the
source of truth". They cannot be read from here, so the safe move is to record
the gap rather than invent a number. A fabricated writeup cap is worse than an
unknown one: it silently constrains the deliverable.

## 2. Limit register

Each row states the limit, where it comes from, and what would settle it.

| # | Limit | Value used in this project | Provenance |
|---|---|---|---|
| L1 | Writeup word cap | Treat **2,000 words** as the working ceiling | **UNVERIFIED for 2027.** Widely reported for earlier Big Data Bowl editions; not readable on the 2027 page. Check `/rules` while signed in. |
| L2 | Figure cap | **5 figures** | **Binding from `TASK.md`** ("max 5 figures"), not from Kaggle. Safe either way: 5 is at or below every BDB cap in recent memory. |
| L3 | Notebook requirements | Public Kaggle notebook, runs end to end, committed outputs | **UNVERIFIED as to runtime/CPU/GPU/internet policy.** `TASK.md` requires a *public* notebook. No internet is assumed in the notebook: all inputs come from the competition dataset. |
| L4 | External data | **Prohibited** | **Binding from `TASK.md`** ("No external data"), and consistent with the standard Kaggle rule. |
| L5 | Deadline | **2027-01-06 23:59 UTC** | **From `TASK.md`**, supplied by Conor. Not re-verified on Kaggle. |
| L6 | Team size / submissions | Not used | Unknown; irrelevant to a solo analysis-only entry. |
| L7 | Data licence / redistribution | Do not redistribute raw data | Kaggle competition data terms are not readable here. The repo gitignores `data/` regardless. |

## 3. Consequences for this repo

1. **No external data, ever.** A local `data/raw/external/gtm/` directory
   survives from the retired task. It is **not read by any script** in this
   pipeline and must not be. `notes/decisions.md` D86 records it.
2. **Figures are capped at 5, by spec.** The deliverable plan in `TASK.md`
   Phase 6 already names exactly five. Do not add a sixth without removing one.
3. **The notebook must be reproducible from the competition data alone.** That is
   why every stage reads only `data/raw/*.csv` and writes only
   `data/interim/` + `outputs/`, and why the notebook is generated from stage
   outputs rather than from ad-hoc cells.
4. **The writeup budget is a design constraint.** 2,000 words is the working
   assumption; the section list in `TASK.md` Phase 6 fits inside it if results
   are reported as intervals rather than tables of p-values.

## 4. How to close the gap

Run this on a machine with a Kaggle session cookie (or `~/.kaggle/kaggle.json`):

```bash
kaggle competitions list --search "big data bowl"      # confirms the slug
curl -sS -b cookies.txt https://www.kaggle.com/competitions/nfl-big-data-bowl-2027/rules
```

Then update rows L1, L3, L6, L7 in place and change the status from UNVERIFIED
to verified, with the fetch date. Until then, **do not** quote a word cap as an
official rule in the writeup.
