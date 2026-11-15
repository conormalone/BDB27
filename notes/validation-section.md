# What we did not establish

*(Previously titled "Validation"; rewritten 2026-11-15 because the earlier draft defended a claim the paper does not make. The drafting spec it answered, `notes/validation-section-spec.md`, is superseded.)*

The claim is a gap claim: the Combine never asks a defensive back to turn and run with a receiver. A gap matters only if the missing movement does, so separation at pass forward is its one defender-attributable measure: `EPA` and `pass_result` fold in the throw and the decision, tackles come after the break, injuries are free text. The consequence test would ask whether a defensive back's 30–60° break retention predicts the separation of the receiver he covers, match fixed at the break frame before the fit. It cannot run here: 61.3% of 7,777 band turns carry no labelled opponent, `player_play` holding only the 497 prospects (115 defensive backs). Coverage caps at 38.7%; the 30% share arm needs 2,333 of 3,009 candidates to resolve uniquely: full-roster identification and a match rule resolving most to one defender, both, not just the first. On what we hold, the 2.0 yd threshold was already excludable: minimum detectable effects 1.01 yd (geometric; 411 turns, 84 players) and 0.59 yd (elimination; 1,731 turns, 102 players). We had power for half the bar and found nothing: −0.38 [−1.08, +0.33], +0.05 [−0.36, +0.46]. The paper's claim needs none of this: a disclosure, not a validation.

## Provenance

Word count of the paragraph above: **198** (body only, excluding this heading, the note and the table).

| Number in paragraph | Value(s) | Artefact |
|---|---|---|
| Band | 30–60° | `TASK.md`; `notes/fallback-validation-spec.md` §1.2 |
| Band turns | 7,777 | `outputs/fallback_validation/reliability.json` (`sample.n_turns`); `TASK.md` |
| Turns with no labelled opposing route-runner | 4,768 | `notes/outcome-link-feasibility.md` (coverage table, both `[FULL]` blocks); `notes/outcome-link-correction-spec.md` (coverage table) |
| Share with none | 61.3% | 4,768 / 7,777 = 61.309% (`notes/outcome-link-feasibility.md`) |
| Cohort | 497 prospects; 115 defensive backs | `notes/outcome-link-feasibility.md` (cohort); `TASK.md` §2; `notes/fallback-validation-spec.md` §5 |
| Turns with ≥1 candidate | 3,009 | `notes/outcome-link-feasibility.md` (coverage table); `notes/outcome-link-correction-spec.md` |
| Share with ≥1 | 38.7% | 3,009 / 7,777 = 38.691% |
| Gate, share arm | ≥30% of band turns | D197 (gate proposal); `notes/geometric-attribution-spec.md` §10 |
| Gate in turns | 2,333 | 0.30 × 7,777 = 2,333.1, printed as 2,333 (the brief's figure; strictly ≥30% needs 2,334 — a one-turn difference); also printed in `notes/outcome-link-feasibility.md` |
| Pre-declared finding bar | \|β\| ≥ 2.0 yd per unit retention | `notes/outcome-test-spec.md` §6(e); `notes/geometric-attribution-spec.md` §11; D199 |
| Minimum detectable effect — geometric | 1.01 yd | derived, see (A) below |
| Minimum detectable effect — elimination | 0.59 yd | derived, see (A) below |
| Elimination-matched turns | 1,731 | D199; `notes/outcome-test-result.md` |
| Elimination-matched players | 102 | D199; `notes/outcome-test-result.md` |
| Elimination point estimate | +0.05 yd per unit retention | D199; `notes/outcome-test-result.md` |
| Elimination 95% CI | −0.36 to +0.46 | D199; `notes/outcome-test-result.md` |
| Estimator sample — turns | 411 | D204; `notes/geometric-attribution-result.md` |
| Estimator sample — clusters | 84 | D204; `notes/geometric-attribution-result.md` |
| Geometric point estimate | −0.38 yd per unit retention | D204; `notes/geometric-attribution-result.md` |
| Geometric 95% CI | −1.08 to +0.33 | D204; `notes/geometric-attribution-result.md` |
| Confidence level | 95% | D199 and D204 (both published intervals) |

### Derived quantities — formula and inputs

**(A) Minimum detectable effect.** `MDE = 2.8016 × SE`, where `2.8016 = z(0.975) + z(0.80)` (two-sided 5%, 80% power). `SE` is recovered from each published interval as `(upper − lower) / (2 × 1.959964)`:

- geometric: `(0.33 + 1.08) / 3.919928 = 0.360` → `MDE = 2.8016 × 0.360 = 1.008` → **1.01 yd** (input: D204 CI −1.08 to +0.33);
- elimination: `(0.46 + 0.36) / 3.919928 = 0.209` → `MDE = 2.8016 × 0.209 = 0.586` → **0.59 yd** (input: D199 CI −0.36 to +0.46).

Both figures are computed at the achieved sample, so neither depends on any scaling assumption.
