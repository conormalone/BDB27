# Questions only the full data can answer

Each entry names the artefact, the script that answers it, and what a bad answer would
mean. Everything here is `PENDING FULL RUN` in the audit
(`outputs/audit/audit_items.csv`). Do **not** substitute a sample result for any of them.

| # | Question | Script / artefact | Stop condition if it fails |
|---|---|---|---|
| 1 | Do enough DL players have enough turns in **both** settings? | `src/01_audit.py` item j → `killtest_stats.csv` (real version writes full counts) | turns per player below `validation.min_turns_per_setting` (10) |
| 2 | Is the capacity parameter reliable (ICC ≥ 0.50) in the Combine **and** in games under the prereg smoother? | item j + Phase 3 envelope → `03_envelope.py` per-player parameters, then `05_validate.py` | reliability below threshold in either setting |
| 3 | Do Combine and game entry-speed ranges overlap enough to avoid extrapolating most of the demand map? | item g → `entry_speed_overlap.csv`, Phase 4 bins | overlap below `validation.entry_speed_overlap_min` (0.50) |
| 4 | What is the Combine-game capacity correlation per position, and what is the noise ceiling? | `05_validate.py` (transfer) | none — this is the confirmatory question, reported as a fraction of the ceiling |
| 5 | How often are game turns executed outside the Combine-derived feasible region? | `05_validate.py` (feasibility) | none — reported by position and speed range |
| 6 | Do the outcome labels behave as an event record (null = no pressure)? | item k on full `player_play`, plus `05_validate.py` label audit | outcome side only: continue with transfer/feasibility and log it |
| 7 | Does a complete `player_play` exist with participation for all 22 players? | item f on full data | if not, describe snap counts as role-event counts (blocker TB4) |
| 8 | Are there opponent/blocker IDs in the full release? | item l | if not, drop the opponent random effect and state it in prereg |
| 9 | Is `dis`'s unit documented in the full release's data dictionary? | item c → `dis_reconstruction.csv` | if not, keep blocker TB1; distance stays derived from x/y |
| 10 | Does the 3-cone angle error (49° on estimated geometry) shrink against surveyed cone positions? | item d with real geometry if published | if not, carry angle uncertainty into the envelope rather than reading angles as exact |

## The specific commands

```bash
python src/00_thresholds.py --mode full --force     # refuses unless full inputs exist
python src/01_audit.py      --mode full --force     # items a-m on full data
python src/02_turns.py      --mode full             # after prereg + this cycle's work
python src/05_validate.py   --mode full             # transfer, feasibility, outcomes
```

`01_audit --mode full` refuses unless `outputs/thresholds.json` has provenance `FULL`
and `data/raw/game_tracking.csv` exists. That refusal is the gate, not a bug.

## Power at the real n

`outputs/audit/power.csv` gives the smallest detectable effect on an n grid, so the
answer can be read off once the full-data n is known — never estimated from the sample.
For reference: n=60 players → r=0.355; n=80 → 0.309; n=120 → 0.253.
