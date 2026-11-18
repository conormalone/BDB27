# Received artefact — not produced by any code in this repository

**This directory is not a pipeline output.** No script, notebook or test in this repo writes here,
and nothing here was computed on this machine. It holds one file, exactly as it arrived.

| Field | Value |
|---|---|
| File | `precedence.csv` |
| What it is | `precedence.csv` from Conor's laptop run of `notebooks/bdb27_multi_candidate.ipynb` against the **full** tracking files, 2026-11-18, after `49a6b0f` |
| sha256 | `896a1f5c189ff4c409cf72d8051b85c9dd0c52dc0c09074ceeb32edbea3aaeb8` |
| Bytes | 1,345 |
| Line endings | CRLF (a Windows write; the local no-tracking `outputs/ambiguity_attribution/precedence.csv` is LF) |
| Rows | header + 12 counted rows |
| Received as | a chat attachment, `/home/conormalone/.openclaw/media/inbound/precedence---eb729971-9cf2-488e-a33d-3d2c12c3526e.csv` |

**The sha256 above identifies the bytes that were sent. It verifies nothing about the run.** No
manifest came with it: there is no hash of the full turns table, no hash of the tracking file, no
git HEAD, no run timestamp, and no per-turn detail (`geometry.csv`, `route_a_audit.csv` are not
here). The counts in this file cannot be checked against the run that produced them from anything on
this machine, and cannot be regenerated here — this machine has no full tracking file, so its own
run reports the geometric slots `NOT EVALUABLE`.

The numbers it carries, the arithmetic that reconciles them, and what they do and do not establish
are recorded in `notes/multi-candidate-result.md` §1–§3 and D210. The run this machine **does**
hold is `outputs/ambiguity_attribution/` (N = 2,295, no tracking) — a different run, left untouched.

The copy here exists so the received table is durable rather than living in a chat transcript, and
so a future run's `precedence.csv` can be diffed against it line by line. It is deliberately not
named `*_full` and is deliberately not under `outputs/ambiguity_attribution/`.
