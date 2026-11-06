# Kaggle submission checklist — BDB27

**Status: NOT READY.** The public notebook exists and runs end to end in sample
mode, but no full-data run has happened. This file lists what the submission
needs and, for every item this host cannot verify, says so plainly. Nothing here
is a claim of compliance that has not been tested.

Companion files: `notes/rules.md` (limit register and what was fetched),
`notes/prereg.md` (frozen design), `notes/writeup-draft.md` (the writeup
skeleton), `notes/limitations.md` (threats).

---

## 1. What the public notebook needs

| # | Requirement | Where it lives | State |
|---|---|---|---|
| N1 | Runs end to end from a clean kernel with **Run All** | `notebooks/bdb27_full_run.ipynb` | Tested in sample mode (`nbconvert`, zero error outputs, D119). Full mode `UNVERIFIED ON THIS HOST` — needs the three tracking files. |
| N2 | Data attached as a competition dataset, not uploaded with the notebook | `DATA_DIR` in the config cell; default `data/raw` | **`UNVERIFIED ON THIS HOST`** — the 2027 dataset slug and mount path (`/kaggle/input/<slug>`) cannot be read without authenticated Kaggle access (API returns 401). Set `DATA_DIR` to the mount path in the submitted copy. |
| N3 | No private data, no external data | `config.yaml` paths; `.gitignore` | Satisfied in the repo: `data/` is gitignored, no external source is read. `UNVERIFIED ON THIS HOST` that the attached Kaggle dataset contains the three files named in `config.yaml` (`game_tracking_2023/2024/2025.csv`) — they are not present here. |
| N4 | No network access required | all stages; `notebook_report`; `writeup_render` | Satisfied: no stage imports a network client; artefacts are read from disk only. |
| N5 | Pinned dependencies | `requirements.txt` | Pinned to `duckdb 2.0.0`, `pandas 3.1.1`, `numpy 2.5.4`, `pyarrow 26.0.1`, `PyYAML 6.0.3`, `psutil 7.2.2` (ARM64/Python 3.13.5 venv). `UNVERIFIED ON THIS HOST` on Kaggle's image: the pins are not tested there, and a pinned install may fail if the image is offline. |
| N6 | Python version stated | `requirements.txt` header | **3.13.5** here. `UNVERIFIED ON THIS HOST` what the 2027 Kaggle image runs; the notebook itself uses only 3.10+ syntax. |
| N7 | Writes land somewhere writable | config `paths.interim`, `paths.outputs` (relative) | `UNVERIFIED ON THIS HOST` — Kaggle is writable only under `/kaggle/working`. The stages write relative to CWD, so the repo must be cloned/copied there, or the path block overridden. Not tested on Kaggle. |
| N8 | Outputs committed / "Save & Run All" | notebook §4–§6 prints and displays | `UNVERIFIED ON THIS HOST` — whether the submission requires committed outputs for this competition is not readable (rules page serves a JS shell). |
| N9 | Figures display in the notebook | notebook §6 calls `render_figures` | Works in sample mode as a **skip** message. The render path is proven only on the synthetic fixture (D120); it has never run on real FULL artefacts. |

## 2. Caps

| Cap | Value | Provenance |
|---|---|---|
| Writeup words | **2,000** working ceiling | **`UNVERIFIED` for 2027.** Widely reported for earlier editions; the 2027 `/rules` page returns ~31 characters of extractable text. `notes/rules.md` L1. |
| Figures | **4** | `TASK.md` line 115 (2026-11-05 amendment dropped the three-model outcome figure). `notes/rules.md` L2 still says 5 from the pre-amendment text; **4 is the binding number** in this repo. |
| Notebook runtime / CPU / GPU / internet | unknown | **`UNVERIFIED ON THIS HOST`** — not readable without auth. The notebook assumes CPU only and no internet. |

## 3. Artefacts that must be FULL-stamped before submission

The notebook's own gates read these stamps. A `PLACEHOLDER_SAMPLE` stamp is not a
style problem: `render_headline` puts the loud `NOT A RESULT` banner on it and
`render_figures` refuses to draw, so a submission built on a sample artefact would
be visibly unvalidated — which is the design working, but it is not a submission.

| Artefact | Read by | Stamp now | Must be |
|---|---|---|---|
| `outputs/audit/gate.json` | §4 headline context, audit record | SAMPLE | FULL |
| `outputs/audit/audit_items.csv` | audit record | SAMPLE | FULL |
| `outputs/phase2/physical_time_by_player.csv` | figure 2, figure 4, Phase 4 | SAMPLE | FULL |
| `outputs/phase2/phase2_summary.json` | audit/report | SAMPLE | FULL |
| `outputs/phase3/snaps.parquet` | figures 2 and 4, Phase 4/5 | SAMPLE | FULL (per-row `provenance` column) |
| `outputs/phase3/phase3_summary.json` | exclusion counts, null provenance | SAMPLE | FULL |
| `outputs/phase3b/reliability_by_player.csv` | resolution statement | FULL | FULL (already) |
| `outputs/phase4/decomposition.json` | §4 headline, figures 1 and 3, writeup | SAMPLE | FULL |
| `outputs/phase4/player_effects.csv` | figure 4 annotation, report | SAMPLE | FULL |
| `outputs/phase4/variance_components.csv` | report | SAMPLE | FULL |
| `outputs/phase4/reliability_agreement.json` | §4 headline | SAMPLE | FULL |
| `outputs/validation/validation.json` | §5 block, writeup §6 | SAMPLE | FULL |
| `outputs/prereg/power.json` | resolution statement | FULL | FULL (already) |
| `outputs/figures/fig1…fig4.png` | §6, writeup figures | absent | four PNGs from a FULL run |
| `outputs/calibration.json` | Phase 2 gate | **PLACEHOLDER** | see B1 below |

**B1 — blocking.** `common.require_calibration_full` refuses a full run while
`outputs/calibration.json` is a placeholder, and it must stay a placeholder:
stance and start type are not labelled anywhere in the release (B8), so no
global calibration offset is estimable. The writeup states that no offset is
applied and that the residual absorbs stance. Until Conor decides whether a full
run may proceed with the calibration artefact explicitly recorded as
not-estimable, **no full run can start**. This is a decision, not a code fix.

## 4. Items that cannot be checked here — and why

Every one of these is marked `UNVERIFIED ON THIS HOST` above; this is the
consolidated list with the reason.

1. **Writeup word cap.** Kaggle's 2027 rules page returns a JavaScript shell
   (~31 chars of extractable text); the API is unauthenticated (401). Working
   ceiling 2,000 is inherited from earlier editions, not read from 2027.
2. **Figure cap.** Same source problem. 4 is binding from `TASK.md`, not Kaggle.
3. **Dataset slug / mount path.** Requires an authenticated Kaggle session.
4. **Presence and naming of the three full tracking files in the dataset.**
   They are not on this host (`notes/blockers.md`).
5. **Kaggle image Python version and offline install behaviour.** No Kaggle
   runtime access from here.
6. **Writable paths and whether `/kaggle/working` layout matches the repo's
   relative paths.** Not tested on Kaggle.
7. **Whether committed outputs are required.** Not readable from the rules page.
8. **Runtime and memory limits.** Not readable; the notebook prints per-stage
   wall time and peak RSS from `outputs/run_log.csv` so the numbers can be
   compared against the limit once it is known.

Nothing in this file claims a limit was met. A fabricated cap is worse than an
unknown one: it would silently constrain the deliverable.

## 5. Pre-submission sequence (when the data arrives)

1. Run the full chain on the bigger machine: `01_audit → 02 → 03 → 03b → 04 →
   05`, `--mode full`, after B1 is decided and the prereg is still frozen.
2. Confirm every artefact in §3 reads `FULL`; `grep -L FULL` is not sufficient —
   the notebook's banner is the check, and `render_figures` refusing is the
   second check.
3. Render the writeup: `python src/writeup_render.py --draft
   notes/writeup-draft.md --outputs outputs --out <writeup>.md --strict`. It
   must print `status: OK`, zero unresolved fields, and a word count ≤ 2,000.
4. Confirm four figures exist and are the four in `TASK.md` §6; no fifth.
5. Re-read §4 above and fill in the limits from an authenticated browser before
   submitting.
