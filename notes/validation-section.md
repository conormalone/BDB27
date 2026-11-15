# Validation

Validation is a defensive-back-level link: 30–60° break retention (`apex_retention`) against the separation, at pass forward, of the receiver he covers. Attribution is fixed before the fit: a proximity match at the break frame takes the single opposing route-runner within 3 yd, drops ties and unmatched turns, and flags every match an inference. Intervals cluster by player, a player-fixed-effects form runs beside the primary, and MAN-only and one-turn-per-play are pre-declared sensitivities. The binding constraint is cohort coverage and independent players, not play volume. Of 7,777 band turns, 4,768 (61.3%) carry no labelled opposing route-runner, because `player_play` holds only the 497 prospects, 115 defensive backs; 3,009 (38.7%) hold at least one, and 2,601 (33.4%) one carrying separation. The gate — ≥500 attributed turns and ≥30% of the band, 2,333 turns — is reachable but demanding: it needs 2,333 of the 3,009 candidates to resolve uniquely at 3 yd, 77.5%. That is all-22 identification, which this dataset does not carry. Minimum detectable effects are 1.01 yd (geometric) and 0.59 yd (elimination), below the pre-declared 2.0 yd bar, so an effect at the bar was already excludable on what we hold: +0.05 yd per unit retention across 1,731 turns from 102 players (95% CI −0.36 to +0.46), and −0.38 yd across 471 turns, 6.1% of the band, 0 of 213 misattributed, 411 in 84 clusters (95% CI −1.08 to +0.33). Detecting 0.38 yd at 80% power takes ≈2,890 turns or ≈590 player clusters, seven times the 84 held; reaching the 30% share gate takes 2,333 turns, five times the 471.

## Provenance

Word count of the paragraph above: **256** (body only, excluding this heading and table; ceiling 260).

| Number in paragraph | Value(s) | Artefact |
|---|---|---|
| Band | 30–60° | `TASK.md`; `notes/fallback-validation-spec.md` §1.2 |
| Match radius | 3 yd | D197 (frozen rule); `notes/geometric-attribution-spec.md` §4 |
| Band turns | 7,777 | `outputs/fallback_validation/reliability.json` (`sample.n_turns`); `TASK.md` |
| Turns with no labelled opposing route-runner | 4,768 | `notes/outcome-link-feasibility.md` (coverage table, both `[FULL]` blocks); `notes/outcome-link-correction-spec.md` (coverage table) |
| Share with none | 61.3% | 4,768 / 7,777 = 61.309% (`notes/outcome-link-feasibility.md`) |
| Cohort size | 497 prospects; 115 defensive backs | `notes/outcome-link-feasibility.md` (cohort); `TASK.md` §2; `notes/fallback-validation-spec.md` §5 |
| Turns with ≥1 candidate | 3,009 | `notes/outcome-link-feasibility.md` (coverage table); `notes/outcome-link-correction-spec.md` |
| Share with ≥1 | 38.7% | 3,009 / 7,777 = 38.691% |
| Turns with ≥1 carrying separation | 2,601 | `notes/outcome-link-feasibility.md` coverage table |
| Share carrying separation | 33.4% | 2,601 / 7,777 = 33.445% |
| Gate, attributed-turn arm | ≥500 attributed turns | D197 (gate proposal); `notes/geometric-attribution-spec.md` §10 |
| Gate, share arm | ≥30% of band turns | D197; `notes/geometric-attribution-spec.md` §10 |
| Gate in turns | 2,333 | 0.30 × 7,777 = 2,333.1, printed as 2,333 (the brief's figure; strictly ≥30% needs 2,334 — off by one, and both clear the gate arithmetic below); also printed in `notes/outcome-link-feasibility.md` |
| Share of candidates that must resolve uniquely at 3 yd | 77.5% | 2,333 / 3,009 = 77.534% |
| Minimum detectable effect — geometric | 1.01 yd | derived, see (A) below |
| Minimum detectable effect — elimination | 0.59 yd | derived, see (A) below |
| Pre-declared finding bar | \|β\| ≥ 2.0 yd per unit retention | `notes/outcome-test-spec.md` §6(e); `notes/geometric-attribution-spec.md` §11; D199 |
| Elimination-matched turns | 1,731 | D199; `notes/outcome-test-result.md` |
| Elimination-matched players | 102 | D199; `notes/outcome-test-result.md` |
| Elimination point estimate | +0.05 yd per unit retention | D199; `notes/outcome-test-result.md` |
| Elimination 95% CI | −0.36 to +0.46 | D199; `notes/outcome-test-result.md` |
| Geometric attributed turns | 471 | D204; `notes/geometric-attribution-result.md` |
| Geometric attributed share | 6.1% | 471 / 7,777 = 6.056%; D204 |
| Geometric point estimate | −0.38 yd per unit retention | D204; `notes/geometric-attribution-result.md` |
| Misattribution | 0 of 213 | D204; `notes/geometric-attribution-result.md` |
| Estimator sample — turns | 411 | D204; `notes/geometric-attribution-result.md` |
| Estimator sample — clusters | 84 | D204; `notes/geometric-attribution-result.md` |
| Geometric 95% CI | −1.08 to +0.33 | D204; `notes/geometric-attribution-result.md` |
| Effect size to detect | 0.38 yd | D204 (magnitude of the geometric point estimate, 471-turn set) |
| Power | 80% | derivation convention below |
| Turns needed at 80% power | ≈2,890 | derived, see (B) below |
| Player clusters needed at 80% power | ≈590 | derived, see (B) below |
| Cluster multiple | seven times the 84 held | 590 / 84 = 7.03 |
| Gate multiple | five times the 471 held | 2,333 / 471 = 4.95 |
| Confidence level | 95% | D199 and D204 (both published intervals) |

### Derived quantities — formula and inputs

**(A) Minimum detectable effect.** `MDE = 2.8016 × SE`, where `2.8016 = z(0.975) + z(0.80)` (two-sided 5%, 80% power). `SE` is recovered from each published interval as `(upper − lower) / (2 × 1.959964)`:

- geometric: `(0.33 + 1.08) / 3.919928 = 0.360` → `MDE = 2.8016 × 0.360 = 1.008` → **1.01 yd** (input: D204 CI −1.08 to +0.33);
- elimination: `(0.46 + 0.36) / 3.919928 = 0.209` → `MDE = 2.8016 × 0.209 = 0.586` → **0.59 yd** (input: D199 CI −0.36 to +0.46).

**(B) Sample size for a 0.38 yd effect at 80% power.** Assumes `SE ∝ 1/√n`, so `n₂ = n₁ × (MDE₁ / 0.38)²`:

- turns: `411 × (1.0077 / 0.38)² = 411 × 7.034 = 2,891` → **≈2,890 attributed turns**;
- clusters: `84 × (1.0077 / 0.38)² = 84 × 7.034 = 591` → **≈590 player clusters**.

**Caveat on (B).** With 84 clusters the player count, not the turn count, binds: turns within a player are correlated, and the interval is clustered by player. Scaling turns is therefore the optimistic reading of the arithmetic; the cluster figure (≈590) is the one to plan against, and it is roughly seven times the 84 clusters held. This caveat does not affect (A), which is computed at the achieved sample.
