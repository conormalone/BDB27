# Received artefacts — not produced by any code in this repository

**This directory is not a pipeline output.** No script, notebook or test in this repo writes here,
and nothing here was computed on this machine. It holds three files, exactly as they arrived.

| File | sha256 | Bytes | Rows | Line endings |
|---|---|---:|---:|---|
| `precedence.csv` | `896a1f5c189ff4c409cf72d8051b85c9dd0c52dc0c09074ceeb32edbea3aaeb8` | 1,345 | header + 12 counted rows | CRLF |
| `geometry.csv` | `d93eab30a790ca7646359202d4d284045515e151aad16b80d4cda6e30b270074` | 636,148 | header + 870 | CRLF |
| `route_a_turns.csv` | `79ed9a087a59e3a6fc13f3de02c2fd83ea8bcc7b9a31bc59eabf068d8fa4af51` | 114,073 | header + 564 | CRLF |

**The sha256 values above identify the bytes that were sent. They verify nothing about the run.**
No manifest came with any of them: no hash of the full turns table, no hash of the tracking file, no
git HEAD, no run timestamp.

---

## Section 1 — `precedence.csv`

| Field | Value |
|---|---|
| File | `precedence.csv` |
| What it is | `precedence.csv` from Conor's laptop run of `notebooks/bdb27_multi_candidate.ipynb` against the **full** tracking files, 2026-11-18, after `49a6b0f` |
| sha256 | `896a1f5c189ff4c409cf72d8051b85c9dd0c52dc0c09074ceeb32edbea3aaeb8` |
| Bytes | 1,345 |
| Line endings | CRLF (a Windows write; the local no-tracking `outputs/ambiguity_attribution/precedence.csv` is LF) |
| Rows | header + 12 counted rows |
| Received as | a chat attachment, `/home/conormalone/.openclaw/media/inbound/precedence---eb729971-9cf2-488e-a33d-3d2c12c3526e.csv` |
| Uploaded | 2026-11-18 11:07 UTC |

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

---

## Section 2 — `geometry.csv`

| Field | Value |
|---|---|
| File | `geometry.csv` (committed as `outputs/ambiguity_attribution_received/geometry.csv`) |
| What it is | `geometry.csv` from the same laptop run of `notebooks/bdb27_multi_candidate.ipynb` against the **full** tracking files on 2026-11-18 — one row per band turn with two or more visible candidates, carrying each candidate's nearest and second-nearest distance at the break frame |
| sha256 | `d93eab30a790ca7646359202d4d284045515e151aad16b80d4cda6e30b270074` |
| Bytes | 636,148 |
| Line endings | CRLF (a Windows write) |
| Rows | header + 870 data rows (`n_candidates` 2/3/4 = 704 / 161 / 5; `geometry_status` 869 `tracked` + 1 `break_frame_unrecovered`) |
| Received as | three identical uploads, byte-for-byte (`sha256sum` equal): `geometry_1---f56d991e-4a45-4ea7-9398-c78da19ace26.csv` (2026-11-18 07:17 UTC, the original), `geometry_1---06af1294-1018-4ffd-937b-45b7e4865709.csv` (07:27 UTC) and `geometry_1---93fc49a7-0d0b-4eae-a599-6642dc2e7b60.csv` (07:28 UTC, the copy committed here) |

**The sha256 above identifies the bytes that were sent. It verifies nothing about the run.** No
manifest came with it, and no per-turn tracking rows came with it: the distances cannot be
recomputed on this machine, which has no game tracking file.

**It predates the D208 fix.** This table is from the 07:17 UTC upload, before the slot-precedence
fix; the precedence table in Section 1 is from the later 11:07 UTC run. They are used together
because they reconcile: `676 (multi-candidate union) + 1,731 (N_frozen) + 151 (residual) = 2,558 =
N`, the post-fix total. The distances in this table and the Route-A reference pass do not depend on
slot order, so the two runs can be combined for this purpose — the PM's reconciliation, recorded in
figure 22's sidecar and D225. Anything that *does* depend on slot order must not mix the two.

The copy here exists so figure 22's bar-4 segments and bar-5 `200` are traceable to bytes rather
than to a chat attachment, and so a future run's `geometry.csv` can be diffed against it. Its sha256
is pinned in `src/bdb27/figures.py` (figure 22); changed bytes refuse with both digests printed.

---

## Section 3 — `route_a_turns.csv`

| Field | Value |
|---|---|
| File | `route_a_turns.csv` (committed as `outputs/ambiguity_attribution_received/route_a_turns.csv`) |
| What it is | `route_a_turns.csv` from the same laptop run — the Route-A reference pass's per-turn table: which turn it takes, which man it attributes, and the rule it used (`route_a_rule` `target` 365 / `alignment` 199) |
| sha256 | `79ed9a087a59e3a6fc13f3de02c2fd83ea8bcc7b9a31bc59eabf068d8fa4af51` |
| Bytes | 114,073 |
| Line endings | CRLF (a Windows write) |
| Rows | header + 564 data rows (532 of them inside `geometry.csv`'s 870; 32 outside the multi-candidate pool) |
| Received as | three identical uploads, byte-for-byte: `route_a_turns---dc464255-80ca-4ea3-b383-c2754a04a844.csv` (2026-11-17 20:54 UTC), `route_a_turns_1---c8c8306a-22d6-4528-91a1-42f30f24d682.csv` (2026-11-18 07:16 UTC) and `route_a_turns_1---4d95fd86-3ec9-427a-9180-d84790a8ca48.csv` (07:23 UTC, the copy committed here) |

**The sha256 above identifies the bytes that were sent. It verifies nothing about the run.** No
manifest came with it, and it is the *reference* pass from `precedence.csv` (`route_a_reference`,
564 of 1,277), not the Route-A slot: it is added to no slot's count. Its only role in figure 22 is
to define, together with `geometry.csv`, the multi-candidate union whose four segments and 200
within-3 turns are drawn — and to show that on the 220 measured Route-A-only turns the attributed
man is not always the nearest one.

Its sha256 is pinned in `src/bdb27/figures.py` (figure 22); changed bytes refuse with both digests
printed.

---

## What figure 22 takes from here

| Bar / element | File | Field |
|---|---|---|
| bar 4 survives | `precedence.csv` | `N` (2,558) |
| bar 4 segment 1 (1,882) | `geometry.csv` + `route_a_turns.csv` (union) with the `N_frozen` residual | `n_candidates`; residual `N − N_frozen − union` |
| bar 4 segments 2/3/4 (534 / 137 / 5) | `geometry.csv` + `route_a_turns.csv` | `n_candidates` over the union |
| bar 5 (413) | `single_turn_geometry.csv` (213, elsewhere) + `geometry.csv` ∪ `route_a_turns.csv` (200) | `within_radius`; `dist_nearest_yd` |
| +151 | not on this directory's rows — a residual of `N`, `N_frozen` and the union | no exported distance exists |

No `nfl_id`, no player name and no opponent identifier is carried into any file figure 22 writes.
