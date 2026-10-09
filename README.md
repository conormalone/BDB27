# BDB27 — NFL Big Data Bowl 2027

**Question:** Is there a *fatigue signal* within NFL Combine events — does measurable performance degrade as the combine progresses (across days, drill order, repeated attempts within a session) — and does that within-combine signal show up later in NFL game data?

**Competition:** NFL Big Data Bowl 2027, theme **"Combine to NFL Performance"** — the first combine-themed edition.
- Final deadline **6 Jan 2027**; results ~late Jan 2027.
- Tracks: Open ($45k) + University ($45k), plus a $10k grand prize.
- Judged: Football 30 / Data Science 30 / Writeup 20 / Viz 20, with pass/fail gates (explicit Combine→NFL linkage; public Kaggle notebook + writeup).

**Framing:** a **mechanism / measurement** paper. Not "do combine numbers predict NFL success?" — that angle is saturated (combine tests explain ~2.6% of year-1 game performance; Cook et al. 2020; Rishis et al. 2023).

## Data (2027 BDB, Kaggle)
Raw data lives on the USB drive (`data/raw` → `/mnt/project_data/conor_downloads/bdb27/`) — never committed here.

- `combine_tracking.csv` — 10 Hz optical tracking, ~6,310 drill attempts. Fields: `event_id`, `attempt`, ISO-8601 `time`, `drill_type`, `x/y/s/a/dis/dir`. **The key asset.**
- `combine_results.csv` — anthropometrics + electronic splits (10-yd split, 40, vertical, broad, 3-cone, shuttle, bench reps).
- `player_play.csv` + `game_tracking_2023/24/25.csv` — in-game NGS "target" side.

## Layout
| Dir | Purpose |
|---|---|
| `research/` | Prior-work scan, literature notes, method surveys |
| `notebooks/` | Runnable `.ipynb` — the primary interface (no CLI) |
| `specs/` | Methodology + identification strategy (PM-owned) |
| `src/` | Reusable code, implemented by coder sub-agents via the PM |
| `outputs/` | Generated figures, tables, metrics (gitignored) |
| `data/` | Pointer to the raw data on the USB (gitignored) |

## Status
- [x] Prior-work scan — `research/prior-work-scan.md` (8 Oct 2026)
- [x] De-risk prototype: drill-order / time-of-day recoverability; attempt-to-attempt 40-yd decay; how much survives controls — `notebooks/01_de_risk_prototype.ipynb`, `specs/de-risk-findings.md`
- [x] Phase 0 setup (`notes/rules.md`, `notes/schema.md`) + **Phase 1 audit (a–g) — gate = GO** — `src/01_audit.py`, `outputs/audit/SUMMARY.md` (9 Oct 2026)
- [x] Drill family = **ALL drills** (D12, supersedes D3's drill pick); position scope = **DB only, LOCKED** (D13; WR excluded — scheme-dependent in-game intensity) — `notes/decisions.md`, `outputs/audit/h_family_scope.csv`
- [x] Position-scope sign-off (Conor) — **LOCKED to DB only** (D13, 2026-10-09)
- [ ] Identification strategy spec
- [ ] Full analysis + writeup

## Key risk
Confounding — combine **day = position group**; **drill order may be collinear with drill type**; opt-outs are non-random. Isolating a true fatigue effect is make-or-break.
