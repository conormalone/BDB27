# src

Reusable / production code — **coder-owned**, implemented via the PM pipeline.

## Rules
- No analysis decisions here — those live in `../notes/` (`decisions.md`, `prereg.md`).
- `common.py` is the shared engine: config loader, path resolution, run-logger, DuckDB/Parquet helpers.
- Stage scripts (`01_audit.py`, `02_combine_envelope.py`, `00_thresholds.py`, `03_game_demand.py`,
  `04_models.py`, `05_validate.py`) implement the `TASK.md` pipeline, one code path for `--mode sample|full`.
- Memory-efficient for the ARM64 Pi: chunk / stream the big tracking files (Parquet + DuckDB).
- Notebooks **call** these modules; they don't duplicate the logic.

## Files
- `common.py` — shared engine.
- `combine_features.py` — Combine kinematics shared by audit + Phase 2: x/y-derived features, the
  exponential envelope fit (`v(t)=vmax(1-e^{-t/tau})`), cut metrics, ICC(1,1), VIF, hierarchical shrinkage.
- `01_audit.py` — Phase 1 audit (a–i), the go/no-go gate. `--mode sample|full`, `--force`, `--synthetic`.
- `02_combine_envelope.py` — Phase 2 Combine envelope + ICC/VIF feature selection + per-player table.
- `gate.py` — pre-registration freeze / audit go-no-go runtime gate (`--mode full` only).
- `synthetic_fixture.py` — builds the committed, schema-compliant fixture with a **planted binding
  ceiling** (`fixtures/synthetic/{binding,null}/`); never a finding.
- `tests/test_audit.py`, `tests/test_gate.py`, `tests/test_envelope.py` — schema/units + behaviour +
  planted-ceiling-recovery + gate-freeze + selection-recovery + determinism tests
  (run `python src/tests/<file>.py`, no pytest required).
