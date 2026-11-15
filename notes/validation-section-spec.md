# Drafting spec — the Validation section paragraph

**Written 2026-11-15, before the Writer drafts a word.** Authorising words: Conor, 2026-11-15
18:21 GMT — *"So get the writer to draft me a paragraph for inclusion in the paper titled
'validation' that explains what we would do and how much data we would need to do it"*.

Scope: one paragraph in `notes/validation-section.md`, under the heading **Validation**, plus a
provenance block beneath it. `notes/report-draft.md` is **not** edited this cycle — Conor reads the
paragraph first and insertion is a separate instruction. `TASK.md`, the band table and the four
figures are untouched.

---

## 1. The paragraph's job

State, for a paper section headed Validation, two things and only two:

1. **What the validation would be.** A DB-level link from 30–60° break retention to the matched
   receiver's separation, with the attribution fixed before the fit and the intervals clustered by
   player.
2. **How much data it needs**, said as a number of turns and a number of players, and where the
   data has to come from for the share gate to be reachable at all.

It is a *design* statement. It reports nothing new, fits nothing, and interprets no existing
estimate as a finding.

## 2. Required facts — every one of these appears, or the draft fails

1. The unit is the DB turn; the exposure is 30–60° break retention (`apex_retention`); the outcome
   is the matched opposing receiver's separation at pass forward.
2. Attribution is a **proximity match at the break frame** — the single opposing route-runner
   within 3 yd, ties dropped, unmatched turns dropped, every match flagged as an inference.
3. The estimator carries **player-clustered intervals** and a **player-fixed-effects** form, with
   **MAN-only** and **one-turn-per-play** as pre-declared sensitivities, and all of it **fixed
   before the fit**.
4. The binding constraint is **cohort coverage and the number of independent players, not the
   volume of plays**.
5. **61.3% of band turns (4,768 of 7,777) have no labelled opposing route-runner at all**, because
   `player_play` holds only the 497 draft prospects (115 DBs). 3,009 turns (38.7%) have at least
   one; 2,601 (33.4%) have one carrying separation.
6. The pre-declared gate is **≥500 attributed turns and ≥30% of band turns** (2,333 of 7,777). The
   share gate is **reachable in principle but demanding**: it needs 2,333 of the 3,009 candidates
   to resolve uniquely at 3 yd — **77.5%**.
7. Clearing it needs **all-22 identification, which this dataset does not carry**. That is the
   honest ceiling, and the sentence says so plainly. It must **not** be called impossible or
   arithmetically unreachable.
8. What we already hold: elimination match 1,731 turns / 102 players → **+0.05 yd per unit
   retention, 95% CI [−0.36, +0.46]**; geometric match at 3 yd on FULL → **471 attributed turns
   (6.1% of the band), 0 of 213 misattributed, −0.38 yd, 95% CI [−1.08, +0.33], 411 turns in 84
   player clusters**.
9. The pre-declared bar for a finding is **|β| ≥ 2.0 yd per unit retention**, sign, CI excluding
   zero in primary and FE form, and sign stability.
10. **How much data.** Detectable-effect arithmetic from the published intervals, with the formula
    printed in the provenance block:
    - MDE at 80% power, two-sided 5%: **1.01 yd** (geometric) and **0.59 yd** (elimination) against
      the 2.0 yd bar — so an effect at the bar was **already excludable on the turns we hold**.
    - Detecting a 0.38 yd effect at 80% power: **≈2,890 turns or ≈590 player clusters** — about
      six times the 471 attributed turns we hold and **seven times the 84 clusters**.
    - Reaching the 30% share gate asks for **2,333 attributed turns, ≈5× the 471** we hold.
11. Cohort: 497 draft prospects, 115 DBs with game turns. 7,777 turns from 113 players, 7,198
    plays, 809 games.

## 3. Banned moves — any one of these invalidates the draft

- **No underpowered claim.** Never write that the study was underpowered to detect an effect of the
  size that would matter. It was not: the intervals exclude effects at the 2.0 yd bar, and the MDEs
  (1.01, 0.59 yd) sit below it. The study is underpowered for a *small* effect (≈0.38 yd), and that
  distinction is the one sentence that must be right.
- **No promotion.** Neither null becomes a finding. Not "no relationship exists", not "retention
  does not matter", not "the drill would not work". Descriptive numbers stay descriptive; the
  geometric estimate stays labelled below the pre-declared gate.
- **No claim that the Combine tests the shallow break.** The drain is supply against demand; the
  validation is about the recommendation, not the claim.
- **No local-artefact line.** The geometric run happened, on Conor's laptop, on FULL. It is not a
  sample fluke and is not described as one.
- **No invented numbers.** Every figure in the paragraph is in §2. No derived quantity without its
  formula in the provenance block. No p-values, no R², no new confidence intervals.
- **No hedging boilerplate.** No "it is important to note", "further work is needed",
  "future research should". Say what would be done and what it costs.
- **No meta-commentary.** No "this section will show". The paragraph is the section.
- **No CLI or notebook instructions** — this is prose for a paper.

## 4. Voice and length

- House voice from `notes/report-draft.md`: short declarative sentences, numbers in the sentence,
  technical terms used exactly. No bullet lists inside the paragraph; it is one paragraph.
- **Length: 180–260 words, hard ceiling 260.** Count the paragraph body only, excluding heading and
  provenance block.
- Provenance block: a compact table, one row per number, each with the artefact it came from
  (`outputs/fallback_validation/reliability.json`, D199, D204, `notes/fallback-validation-spec.md`,
  `TASK.md`, or the derived-quantity derivation with formula and inputs).

## 5. Derived quantities — the rule

The MDE and sample-size numbers are the PM's derivation this cycle, not a committed artefact. They
may appear in the paragraph **only because** the provenance block prints:

- `MDE = 2.8016 × SE`, with `2.8016 = z(0.975) + z(0.80)`;
- `SE` recovered from each published CI as `(upper − lower) / (2 × 1.959964)`: geometric
  `(0.33 + 1.08)/3.919928 = 0.360`, elimination `(0.46 + 0.36)/3.919928 = 0.209`;
- the scaling assumption `SE ∝ 1/√n`, with the caveat that with 84 clusters the player count is the
  binding unit, so scaling turns is the optimistic reading.

If the formula is not printed, the paragraph does not quote the derived numbers.

## 6. Acceptance

1. `notes/report-draft.md` byte-identical to HEAD after the cycle.
2. Paragraph length inside 180–260 words; heading `Validation`.
3. Every number in the paragraph appears in the provenance block with its artefact.
4. The three banned-claim checks — underpowered, promotion, Combine-tests-the-break — are greppably
   absent.
5. `./.venv/bin/pytest -q` green before the commit that adds these notes.
