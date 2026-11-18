# Multi-candidate attribution — specification: ONE attribution number, two recovery routes

**Originally written 2026-11-17 14:04 and committed alone as `8726120`, before any code existed.
This revision (`Amendment 1 + 2`) was written and committed at 14:40 GMT on 2026-11-17, still before
any count or coefficient from this cycle was produced.** If a result disagrees with a sentence below,
the sentence is not edited afterwards; the disagreement is reported as a disagreement.

**Authorising words, in order.**

1. Conor, 14:04 GMT — *"Ok well we're going to have to do our best to assess the 2 or more on a play"*,
   after being told that the 870 band turns with two or more opposing route-runners are dropped whole
   by the frozen match rule.
2. Conor, 14:10 GMT — *"Include the one match as well, I want 1 number, just more accurate"*. The
   frozen single match is the **base of the pool**, not a rival headline. Recovery is **additive**:
   the frozen 471 all stay in the number, and no already-attributed turn is re-picked.
3. Conor, 14:30 GMT — *"I guess try both but I think the tracking data will be needed"*. Build **both**
   recovery routes: a no-tracking route (target flag, `play_description` name extraction, alignment
   elimination) and a geometric route from frames, shipped as a Run All notebook.

Supersedes nothing outside this cycle. `TASK.md`, the band table, the four figures,
`notes/report-draft.md` and every pre-existing artefact under `outputs/` are untouched.

---

## 0. The deliverable is one number

**N = the number of 7,777 band turns that carry an attribution**, with the composition printed
underneath it and nothing but that composition reported beside it. Not two rival totals, not a
"frozen" column and a "recovered" column competing for headline — one total, one breakdown.

```
N = N_frozen  +  N_geo(5 yd)  +  N_geo(8 yd)  +  N_routeA
```

| Slot | Route | Evidence | Label on every row |
|---|---|---|---|
| `N_frozen` | the frozen rule: exactly one opposing route-runner within **3 yd** at the break frame | rule | `RULE` |
| `N_geo(5)` | the same rule at **5 yd**, only on turns `N_frozen` did not take | frames | `INFERENCE — geometry 5 yd` |
| `N_geo(8)` | the same rule at **8 yd**, only on turns neither took | frames | `INFERENCE — geometry 8 yd` |
| `N_routeA` | the no-tracking route, only on turns no geometric radius took | play file | `INFERENCE — target` / `— description` / `— alignment` |

**Precedence is by evidence strength and is fixed here:** the frozen rule first (it is the primary
attribution and is never re-picked), then geometry at 5 then 8 yd, then the no-tracking route on what
is left. A turn is attributed **at most once**. Every increment is computed only on the turns not
already taken, so the parts sum to `N` with no double counting — an arithmetic invariant, not a hope.

**Why geometry extends the radius rather than tie-breaking inside 3 yd.** The frozen rule refuses to
break a tie, and this cycle does not break one either: a nearest-man pick between two men inside 3 yd
is a coin flip with a bias toward whoever stood closer at that instant. Extending the radius keeps the
rule's own shape — *exactly one candidate inside the radius* — and buys turns where the nearest man is
3.5 or 6 yd away, which the 3-yd rule throws away. That is more turns at the same evidentiary
standard, which is what "just more accurate" asks for, and it adds no new tie-break.

**What the number cannot be.** A turn attributed by a 6-yd match is not as strong as a 2-yd match, and
it is labelled differently for that reason. `N` is a count of *attributions with stated evidence*, not
a count of *known coverages*. The share of `N` that is `RULE` and the share that is `INFERENCE` are
both printed; a reader who wants only rule-attributed turns reads `N_frozen`.

## 1. What is already on the record (to be reproduced, not re-derived)

`[FULL]`, `outputs/outcome_link/feasibility_summary.json`, 2026-11-14:

| Quantity | Value |
|---|---:|
| band turns | 7,777 |
| no opposing `player_play` row | 1,462 (18.8%) |
| opposing rows, no route-runner among them | 3,306 (42.5%) |
| ≥ 1 opposing route-runner | 3,009 |
| with a separation value | 2,601 |
| exactly one candidate | 1,731 |
| two or more candidates | 870 |

Candidate-level artefact: 815 turns with two candidates, 185 with three, 5 with four, 1 with six
(191 at three or more); 270 of the 815 carry `target` on exactly one candidate; 704 of the 815 carry
a separation for both.

D199: β = **+0.05 yd** per unit retention, 95% CI −0.36 to +0.46, n = 1,731, 102 clusters.
D204 geometric run: **471** attributed, 6.1% of the band, `DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE`
— reported from Conor's laptop, **not reproducible on this machine**; every mention carries that
dagger and the final `N` is assembled only where tracking exists.

**One correction carried forward.** `two_candidate_separation_gap_yd` is MAX(sep) − MIN(sep) of the
candidates' `separation_at_pass_forward` values. It is **not** the distance between the two men and
must never be described as one.

## 2. What this cycle may not become

- **Not a re-pick.** No turn already attributed by an earlier slot is re-attributed by a later one.
- **No named man.** No output asserts "the DB was covering X". Every non-frozen pairing is prefixed
  `INFERENCE`.
- **No entry into the claim.** `TASK.md` forbids outcome tests and per-prospect claims; nothing here
  is written into the band table, the four figures or the report draft.
- **No gate.** No minimum n, no minimum share, no abort. A small n prints a wide interval and a
  `DESCRIPTIVE ONLY` label. Only a missing/unreadable required file or a missing required column may
  raise, and it names every path tried first.
- **No new threshold.** 3 yd, 30–60° and the estimator are inherited unchanged. The 5 and 8 yd steps
  are the D204 sensitivity radii, already on the record, not new choices; they are reported as
  separate increments so a reader can take only `N_frozen` if they prefer.

## 3. Route A — no tracking, runs on this machine

Three sub-rules, applied in this order, each only to turns still unattributed after the previous one.
A turn attributed by a sub-rule is never offered to the next.

### A1 `INFERENCE — target`
`target` is True on exactly one of the turn's visible candidates → pair to him. Selection: targeted
passes only. 270 of 815 two-candidate turns qualify; the count for all 870 is printed.

### A2 `INFERENCE — description`
`play_description` names the intended receiver in plain text (`… pass short middle to E.Engram …`).
Parse the first `pass … to X.Surname` token after the pass verb, resolve it against the candidates'
`display_name` from `players.csv` (initial + surname; display names are full names, so the match is
first-initial + surname equality). Fires only when the parse resolves to **exactly one** visible
candidate. Fires on interceptions, where the `target` flag reads False but the text still names him —
that is the reason this sub-rule exists, and it is the widest no-tracking route.
Unresolved parses (0 matches, ≥2 matches, no verb, no name) are **counted and reported**, never
guessed.

### A3 `INFERENCE — alignment`
Only for turns with exactly two visible candidates of **different** `lined_up_position` classes, where
the turning DB's own class maps to exactly one of them under this fixed table, and only where the pair
is one of the two mappings the data can support:

| DB class (recorded) | implicates | and only if the other candidate is |
|---|---|---|
| `CB` | `WR` | `SLOT_WR` or `TE` |
| `SLOT_CB` | `SLOT_WR` | `WR` or `TE` |

Pairs involving `HIGH_SAFETY`/`BOX_SAFETY` are **counted and reported but not used**: "a safety covers
the tight end" is a scheme assumption with no support in this file, and inventing it would be exactly
the kind of method substitution this project forbids. Both players' alignments are observed; only the
pairing implication is inferred, and it is inferred only where the mapping is a positional
correspondence rather than a coverage theory.

### A4 `coverage_assignment` as a pool label, never an attributor
Every attributed turn is stamped `MAN` / `ZONE` / `MISSING` from the DB's own `coverage_assignment`
(`MAN` alone is `MAN`; `NO_ASSIGNMENT` and null are `MISSING`; zone roles such as `DEEP_THIRD_LEFT`
are `ZONE`). It is reported as a composition column and as a stratum. It never selects a candidate —
it names no opponent, and treating it as an attributor would be inventing an identifier the file does
not carry.

## 4. Route B — geometry, needs frames, ships as a notebook

On Conor's laptop, over full tracking. Per turn, at the break frame (frozen definition: the DB's own
row nearest the turn's `(x_centre, y_centre)`, within 0.25 yd; ±0.05 s slice; no interpolation):

- `dist_yd` from the DB to **every** visible candidate — the frozen rule's own distance, applied to
  all candidates rather than only the nearest;
- `n_in_radius_3 / _5 / _8`, `min_distance_yd`, `second_distance_yd`, `nearest_gap_yd` (**the margin**);
- `cand_gap_yd` — the distance **between** the candidates, the physical quantity the artefact's
  `separation gap` is not;
- whether the ordering reverses between snap and break (a man closing from behind is not the man
  standing nearest).

Reported with denominators: the margin distribution (quantiles), the count above 1, 2 and 3 yd and
the total, and the same for `cand_gap_yd`. **No threshold selects anything** — the margin is printed,
and it is the size of the "measurement versus coin flip" distinction rather than an assumption
about it.

### The cross-check that decides whether tracking was needed
Conor's expectation is that tracking carries the bulk and Route A is the fallback. That is testable:
on the turns where **both** routes fire, do they name the same candidate? Report `agree / (agree +
disagree)` with both counts, and the same for each Route A sub-rule separately. This is a stronger
check than the target-known turns alone, which are targeted passes only. **A high disagreement rate is
the error rate to quote for the no-tracking turns**, and it is reported whether or not it flatters
Route A.

## 5. Diagnostic (local, subordinate): what the 870 can move

Not an attribution number and never a second headline. For a specification whose design matrix `D` is
built only from the DB's turn and the DB's own row, `c = row 1 of (D'D)⁻¹D'`, and

```
β(pairing) = c · y = const + Σ_{i ∈ the 870} c_i · y_i ,   y_i ∈ {separations of turn i's candidates}
β_min/max  = const + Σ_i min/max ( c_i · y_ij )
```

encloses the D199 coefficient under **every** admissible pairing at once, exactly, with no search. It
is reported for `primary`, `player_fe`, `pass_only` and `man_only`, beside the frozen-only coefficient
and its CI, and it is **conditional on the true receiver being visible** — the cohort hides candidates,
so the true set can only be wider and this bound is optimistic in the direction of precision. It
contains no sampling error and is never combined with the CI. One row per specification, one line of
reading: does the interval sit inside the CI, straddle it, or exclude zero.

Route-FE specifications depend on the attached candidate, so `c` is not fixed and no bound exists;
they are reported as variants and the output says so on the row.

## 6. Acceptance criteria — mechanical, checked by test

- **AC1 — the frozen base reproduces.** The single-candidate frame is identical, on the 25 shared
  columns and all 1,731 rows, to `outputs/outcome_test/attributed_turns.csv`; its fit gives β
  `+0.05`, CI `[−0.36, +0.46]`, n = 1,731, 102 clusters.
- **AC2 — the 870 is the same 870.** 1,731 + 870 = 2,601 and the per-turn candidate counts
  (815 / 185 / 5 / 1) match the committed artefact.
- **AC3 — additivity.** The four slots are disjoint and sum to `N`; a turn appears in exactly one
  slot; recomputing the slots in a different order gives the same `N` and the same per-slot counts.
- **AC4 — closed form is the estimator.** For a fixed assignment, `c · y` equals `outcome_test.fit`'s
  β to 1e-9, and 20 fixed-seed random pairings all land inside `[β_min, β_max]`.
- **AC5 — Route A is single-valued.** The three sub-rules are applied in order and a turn is
  attributed at most once by Route A; every unresolved parse is counted with its reason.
- **AC6 — no bare counts.** Every printed count carries its denominator; every share is of a named
  total; `RULE` and `INFERENCE` shares are both printed.
- **AC7 — no gates.** Zero ambiguous turns, zero candidate separations, an empty band, a
  `play_description` with no name: each returns zeros plus `NOT FITTED` / `DESCRIPTIVE ONLY`, and
  nothing raises.
- **AC8 — the notebook is portable.** `notebooks/bdb27_multi_candidate.ipynb` runs end-to-end with
  Run All on whatever tracking it finds, including this machine's 1,048,575-row truncation, which it
  prints as a **finding** (players per play, plays present, shares) and does not stop on. If it finds
  no tracking it names every path it tried and still reports `N_routeA` and the composition.
- **AC9 — provenance.** Every input and output sha256'd into
  `outputs/ambiguity_attribution/manifest.json` with the git HEAD and a UTC stamp; a `run_log.csv` row.
- **AC10 — reconciliation anchor.** The all-two-receiver play count (`9,341`, of which `7,009` mixed:
  WR+slot 4,585, WR+TE 1,626, slot+TE 609; same-class 2,332) is reproduced from `player_play.csv` and
  printed with its denominators, or the difference is explained.
- **AC11 — suite green.**

## 7. What the result will not be able to say

1. **Which man he covered.** No field identifies the opponent; every pairing outside `RULE` is an
   inference.
2. **That `N` is a coverage count.** It is a count of attributions with stated evidence; the
   `INFERENCE` share is printed beside it.
3. **Anything about the 1,462 + 3,306 turns where the cohort hides candidates.** They are outside this
   cycle and stay outside every denominator of every claim here.
4. **That a recovered turn is as good as a rule-attributed one.** It is not, which is why the slots
   are separate increments and not one lump.
5. **That the 5 and 8 yd increments are free.** A 8-yd match is a weak claim; the agreement rate
   against Route A on the overlap is the check on that, and it ships whether or not it is flattering.

## 8. Files

| Path | Role |
|---|---|
| `notes/multi-candidate-spec.md` | this file; committed before any run |
| `src/bdb27/ambiguity_attribution.py` | frame builders, Route A, closed-form bound, composition, geometry tables |
| `scripts/ambiguity_attribution.py` | headless `--mode full`, writes `outputs/ambiguity_attribution/` |
| `notebooks/bdb27_multi_candidate.ipynb` | **the interface**: Run All, one config cell, assembles `N` where tracking exists |
| `tests/test_ambiguity_attribution.py`, `tests/test_multi_candidate_notebook.py` | AC1–AC11 |
| `outputs/ambiguity_attribution/{funnel.csv,route_a_turns.csv,composition.csv,assembly.csv,bounds.csv,geometry.csv,summary.json,manifest.json}` | the artefacts |
| `notes/multi-candidate-result.md` | the result, after the run |
| `notes/decisions.md` | D207 |

## 9. API contract

```python
build_frame(turns_path, player_play_path, *, status="single"|"multiple") -> pd.DataFrame
    # "single"  : the D199 sample exactly (AC1); "multiple": one row per (turn, candidate), n_sep>=2
route_a(frame_all, player_play_path, *, rules=("target","description","alignment")) -> pd.DataFrame
    # one row per turn Route A can attribute, with `route_a_rule` and `attributed_nfl_id`
    # ordered, mutually exclusive, unresolved parses counted with `unresolved_reason`
assemble_attribution(counts: dict) -> dict      # the single N + composition + RULE/INFERENCE shares
identified_set(frame, spec="primary"|"player_fe"|"pass_only"|"man_only") -> dict
candidate_distance_table(frames, turns, player_play) -> pd.DataFrame   # Route B, needs tracking
alignment_counts(player_play_path) -> pd.DataFrame                     # AC10 anchor
funnel(turns_path, player_play_path) -> pd.DataFrame
```

`build_frame` reuses `bdb27.outcome_test`'s SQL rather than re-implementing it, so AC1 is a
comparison and not a coincidence. Every returned frame carries `attribution = "INFERENCE — …"` except
the frozen single frame, which carries `RULE`.

---

## Amendment 3 — the precedence in §0 was specified, not executed · 2026-11-18

**Written after the first run that had tracking. Nothing above this line is edited.** §0's
precedence rule, AC3's disjointness clause and the four slots stand exactly as frozen; this block
records that the code did not implement them, what the numbers were, and what now enforces them.

### 3.1 What was observed

Conor ran `notebooks/bdb27_multi_candidate.ipynb` with full tracking on 2026-11-18 07:17. His
`assembly.csv`: frozen **1,731** · geo_5 **411** (`tracked`) · geo_8 **178** (`tracked`) · Route A
**564** → **N = 2,884** (37.1% of 7,777). The committed no-tracking artefact is unchanged
(`funnel.csv`, `composition.csv`, `bounds.csv` all reproduce; `bounds.csv` differs at 1e-13).

### 3.2 The defect

Two places, one cause. §0 fixes the order **frozen → geo(5) → geo(8) → Route A** and says a turn is
attributed **at most once**; AC3 requires the slots to be disjoint and to sum to `N`.

1. **The passes shared one pool.** The notebook's Route A cell called `route_a_tables(...,
   exclude=frozen_keys)` and its geometry cell called `geometry_slots(..., exclude=frozen_keys)` —
   the *same* 1,277-turn pool, each blind to the other (`route_a_audit.csv`'s denominator is 1,277,
   which is 3,008 turns with a visible opposing route-runner minus the 1,731 frozen). Route A also
   ran **before** geometry, the reverse of the specified order. `scripts/ambiguity_attribution.py`
   had the identical two lines.
2. **The assembly did not check.** `assemble_attribution` sums four integers. Given two overlapping
   sets it reports their sum as `N`, and nothing in the pipeline ever tested that the four slots
   were a partition, so AC3's invariant was asserted in prose and never computed.

**What that does to the number.** From his tables the two recovering routes claimed 589 + 564 =
1,153 of a 1,277-turn pool. The overlap is bounded but not measured: 0 ≤ overlap ≤ 564, so
`2,884` is an upper bound and `N` lies in **[2,320, 2,884]**. `2,884` cannot be quoted as `N`, and
neither can any other single value, until the overlap is counted on the machine that has the frames.

### 3.3 The fix, and what it is not

One function, `ambiguity_attribution.attribution_pipeline()`, computes the four slots in the
specified order and both surfaces call it — the notebook's cells and the script's section — so the
order exists in one place and cannot drift between them:

```
geo      = geometry_slots(pool = frame_all − frozen)                    # 5 then 8, inside the call
route_a  = route_a_tables(pool = frame_all − frozen − geo_5 − geo_8)    # the slot
reference= route_a_tables(pool = frame_all − frozen)                    # §4 cross-check ONLY
N        = |frozen| + n_geo_5 + n_geo_8 + |route_a|
overlap  = | turn keys in reference ∩ turn keys geo took |
```

`reference` is a **counterfactual, never a slot**: it is the pass the defect ran as if it were the
slot, kept because §4's cross-check ("on the turns where both routes fire, do they name the same
candidate?") needs exactly those turns, and because it is what makes the overlap a stated count
instead of an assumption. It is added to nothing and it never enters `N`. With the overlap printed,
the reconciliation is arithmetic: `2,884 − overlap = N` on his run.

**Corrected formula.**

```
N = N_frozen + N_geo(5) + N_geo(8) + ( Route A on what the three earlier slots left )
  = 1,731 + N_geo(5) + N_geo(8) + ( Route A_reference − overlap )
```

**Not changed.** The 3 yd / 5 yd / 8 yd ladder, the 30–60° band, `outcome_link.PRIMARY_RADIUS_YD`,
the estimator, the four bounded specifications, the label rule, Route A's three sub-rules and their
order, the refusal to break a tie inside a radius, and the definition of the break frame. No new
attribution rule is introduced; the only thing that moved is *which turns each existing rule is
allowed to see*.

**One clause that cannot be satisfied as written.** AC3 says recomputing the slots in a different
order gives "the same `N` and the same per-slot counts". For a precedence partition with a non-empty
overlap that is impossible: Route A reordered before geometry would claim 564 and geometry would
lose whatever it had taken. What is enforced, and what the tests check, is the rest of AC3 —
disjointness, one turn in one slot, and `Σ slots = N` — plus the statement of the difference: the
counterfactual order's `N` is `2,884 − overlap` only if geometry and Route A never attribute
*different* men to the same turn, which the §4 agreement rate measures. The disagreement is reported,
not reconciled away.

### 3.4 What the run now prints and writes

- `precedence.csv` — one new artefact, fifteen tables per run instead of fourteen: frozen, geo_5,
  geo_8, the Route A slot, the Route A reference pass with its own denominator, `overlap`,
  `route_a_lost_to_geometry`, `n_double_counted` (must be 0) and `N`.
- The Route A cell states both denominators: the reference pool (1,277 on his run) and the slot pool
  (1,277 minus the turns geometry took).
- The notebook's final print names all four slots. The line it replaced — "the headline N unchanged:
  N = 1,731 frozen + 564 Route A" — was true only while the geometric slots were zero, which is how
  it survived the no-tracking run and misled the tracked one.

### 3.5 Acceptance

| # | Check |
|---|---|
| A3-1 | With no tracking: `N = 2,295`, geo slots `0` with `NOT EVALUABLE`, Route A 564 of 1,277, overlap 0 — the committed artefact reproduces exactly. |
| A3-2 | With a stubbed geometry claiming turn keys Route A also attributes, Route A's slot excludes every one of them and `N` does not double count. |
| A3-3 | `n_double_counted == 0` and `Σ slots == N` on every run, printed, in `precedence.csv`. |
| A3-4 | The notebook computes geometry **before** Route A and passes no slot's `exclude` by hand: source-order and no-hand-assembly tests, so the order cannot silently flip back. |
| A3-5 | Suite green. |

### 3.6 What Conor runs

Run All on `notebooks/bdb27_multi_candidate.ipynb` with `TRACKING` unchanged (his full files). Send
back **`precedence.csv`** — it carries the overlap and the corrected `N` in one file — and, if a
second is wanted, `route_a_audit.csv`, whose denominator now shows the residual pool rather than
1,277. Nothing else is needed to state the overlap; `geometry.csv` and `route_a_turns.csv` remain
the detail behind it.
