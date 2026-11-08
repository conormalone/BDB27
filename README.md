# BDB27 — Turn Coverage and the Cut Card (NFL Big Data Bowl 2027)

**Question.** Can a player run the turns his job requires, at game speed? Estimate
each player's **turning capacity** from Combine tracking, measure the **turn demand**
of his position in games, and score the share of that demand that falls inside his
Combine-derived feasible region (**Turn Coverage**), with **cut gaps** and a one-page
**Cut Card**.

**Binding spec = `TASK.md`.** It supersedes the burst-vs-jump (get-off
decomposition) task at 2026-11-06 23:04, and before that the intra-drive
regeneration and leverage × ceiling tasks. All three are preserved with git history
under `archive/legacy-2026-11-05/` and `archive/legacy-2026-11-06/`.

**Competition.** NFL Big Data Bowl 2027, deadline **2027-01-06 23:59 UTC**. Working
limits: **≤2,000 words, ≤5 figures**; the notebook must run end to end. Official
competition docs are the source of truth — see `notes/rules.md` for what was
verified and what is marked `UNVERIFIED`.

## Where things stand

| stage | file | status |
|---|---|---|
| 00 thresholds | `src/00_thresholds.py` | implemented — placeholder provenance in `--mode sample` |
| 01 audit | `src/01_audit.py` | implemented — items a–m, writes `outputs/audit/` |
| 02 turns | `src/02_turns.py` | implemented — writes the turn table |
| 03 envelope | `src/03_envelope.py` | implemented — variance route + components |
| 04 demand/coverage | `src/04_demand_coverage.py` | implemented — demand map, coverage, cut gaps |
| 05 validate | `src/05_validate.py` | **loud stub** — exits 2 with `STAGE NOT IMPLEMENTED` |
| 06 figures | `src/06_figures.py` | implemented — fourteen figures + `figures_manifest.json` |

Six of the seven stages are implemented; `05_validate` is still a loud stub and is never
counted as a pass. **The notebook is the interface** (`notebooks/bdb27_full_run.ipynb`) —
see `notebooks/README.md`; `README_RUN.md` is the equivalent shell fallback.

Every artefact written in `--mode sample` carries `UNVALIDATED SAMPLE OUTPUT`.
Nothing in this repo may be interpreted, reported or tuned against sample results.
