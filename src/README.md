# src

Reusable / production code — **coder-owned**, implemented via the PM pipeline.

## Rules
- No analysis decisions here — those live in `../notes/` (`decisions.md`, `prereg.md`).
- `common.py` is the shared engine: config loader, path resolution, run-logger, DuckDB/Parquet helpers.
- Stage scripts (`01_audit.py`, `02_combine_envelope.py`, `00_thresholds.py`, `03_game_demand.py`,
  `00b_external_proxies.py`, `04_models.py`, `05_validate.py`) implement the `TASK.md` pipeline, one code
  path for `--mode sample|full`.
- Memory-efficient for the ARM64 Pi: chunk / stream the big tracking files (Parquet + DuckDB).
- Notebooks **call** these modules; they don't duplicate the logic.

## Files
- `common.py` — shared engine.
- `combine_features.py` — Combine kinematics shared by audit + Phase 2: x/y-derived features, the
  exponential envelope fit (`v(t)=vmax(1-e^{-t/tau})`), cut metrics, ICC(1,1), VIF, hierarchical shrinkage.
- `game_features.py` — game-side kinematics shared by Phase 3: x/y-derived per-play effort peaks
  (speed + accel), route-snap extraction (`route_ran IS NOT NULL`), streamed percentile histograms,
  the top-k tie rule, and the Combine-ceiling helpers.
- `01_audit.py` — Phase 1 audit (a–i), the go/no-go gate. `--mode sample|full`, `--force`, `--synthetic`.
- `02_combine_envelope.py` — Phase 2 Combine envelope + ICC/VIF feature selection + per-player table.
- `00_thresholds.py` — Phase 3a route-effort cut-offs. `--mode sample` = a-priori PLACEHOLDER; `--mode
  full` = derived (provenance `FULL`); `--synthetic` exercises the derivation on the fixture.
- `03_game_demand.py` — Phase 3b in-game demand: top-k efforts per fixed route-snap count, per-effort
  speed/accel, envelope fraction (effort ÷ Phase-2 ceiling). `--mode sample|full`, `--force`, `--synthetic`.
- `00b_external_proxies.py` — Phase 0.5 external proxies: map Kaggle `nflId` → Pre-Combine Expected Draft
  Position (Grinding The Mocks, February snapshots, fuzzy match). `--mode sample|full`, `--force`,
  `--synthetic`; writes `outputs/pre_combine_expectation.parquet` (EXACT `nflId, Pre_Combine_EDP,
  match_confidence_score`). **Gates `04_models --mode full`** (existence + schema).
- `gate.py` — pre-registration freeze / audit go-no-go / **Phase-0.5 external-proxy** runtime gate
  (`--mode full` only).
- `synthetic_fixture.py` — builds the committed, schema-compliant fixture with a **planted binding
  ceiling** (`fixtures/synthetic/{binding,null}/`); never a finding.
- `synthetic_gtm_fixture.py` — builds the committed, schema-compliant synthetic **GTM** fixture
  (`fixtures/synthetic/gtm/`); never a finding.
- Tests (`run python src/tests/<file>.py`, no pytest required): `test_audit.py` (schemas/units + planted-
  ceiling recovery), `test_gate.py` (freeze/go-no-go gate), `test_envelope.py` (Phase 2 selection +
  determinism), `test_thresholds.py` (placeholders vs derived + gate wiring), `test_game_demand.py`
  (top-k ties + planted-ceiling binding/null end-to-end + full-run discipline), `test_external_proxies.py`
  (Phase 0.5 ingest/February-filter/matching + strict schema + Phase-4 gate).
