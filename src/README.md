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
- `01_audit.py` — Phase 1 audit (a–i), the go/no-go gate. `--mode sample|full`, `--force`, `--synthetic`.
- `synthetic_fixture.py` — builds the committed, schema-compliant fixture with a **planted binding
  ceiling** (`fixtures/synthetic/{binding,null}/`); never a finding.
- `tests/test_audit.py` — schema/units + behaviour + planted-ceiling-recovery + determinism tests
  (run `python src/tests/test_audit.py`, no pytest required).
