# BDB27 — NFL Big Data Bowl 2027

**Question.** Split between-player differences in a defensive lineman's **get-off**
into a **physical** part, measured at the Combine (where there is no snap to react
to), and a **residual "jump"** — everything the legs do not explain — then test
whether the residual is a stable trait that matters on the field.

Four questions, in the spec's own order:

1. **Primary (confirmatory).** What share of between-player variance in
   `player_get_off` is explained by Combine-predicted physical time versus a
   residual player effect? With intervals, DL pooled and edge vs. interior.
2. **Primary (second).** Is the residual reliable across games, and does it agree
   with a direct measure of reaction latency (snap to first movement)?
3. **Secondary.** Does the residual predict quick pressure beyond physical burst,
   in held-out games?
4. **Exploratory.** Within a player, does get-off improve year to year through the
   residual while physical time stays flat?

**Claim limits (hard).** Claims apply only to players who both attended the
Combine and recorded an NFL pass-rush rep. "Jump" is a **residual**, not a
measurement of anticipation or football IQ — it also contains stance, technique,
alignment and onset-definition differences. Never claim more than the data
support.

**Competition.** NFL Big Data Bowl 2027, deadline **2027-01-06 23:59 UTC**.
Working limits: **≤2,000 words, ≤5 figures** — the figure cap is binding from
`TASK.md`; the word cap is **unverified** against the 2027 rules page, which is
not machine-readable from this host (`notes/rules.md`).

> **Binding spec = `TASK.md`** (commit `bcfd48b`). It supersedes the
> intra-drive-regeneration spec and, before that, the leverage × ceiling task.
> Both are preserved under `archive/legacy-2026-11-05/` with git history.

## Where things stand

**Phase 0 + Phase 1 are complete.** Phases 2–5 exist as loud stubs.

| stage | status |
|---|---|
| `01_audit` | complete — items a–i, gate `CONDITIONAL GO`, 2 conditions pending full data |
| `02_combine_physical_time` | not implemented |
| `03_game_snaps` | not implemented |
| `04_models` | not implemented |
| `05_validate` | not implemented |

Read, in this order:

1. `notes/phase1-audit.md` — the audit and the gate decision.
2. `notes/schema.md` — what the data actually contains (and what it does not).
3. `notes/blockers.md` — B1–B7, and **Q1** on the smallest effect of interest.
4. `README_RUN.md` — how to run it.

## Deliverable model

`src/` is the development engine: small, tested, config-driven stage scripts.
The competition deliverable is a **public Kaggle notebook + writeup** (≤5
figures) that runs the same logic end to end and renders results from
`outputs/`. No ad-hoc analysis cells.

## Layout

```
src/01_audit.py -> 02_combine_physical_time.py -> 03_game_snaps.py
                -> 04_models.py -> 05_validate.py
```

`data/raw` and `data/interim` are symlinks to a USB volume and are gitignored.
`outputs/` is generated and gitignored except the deliverable figures.
`archive/` holds retired analyses, preserved with history.
