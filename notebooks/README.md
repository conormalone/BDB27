# Notebooks

Runnable, self-contained `.ipynb` files. The **competition deliverable is a public Kaggle notebook +
writeup** (≤2,000 words, ≤5 figures); this folder holds that notebook (and any local-run variant).

## Conventions
- **Self-contained**: each opens with its imports + data paths and runs top-to-bottom without hidden state.
- **Data path** points at `../data/raw` (symlink to the mounted drive) — never commit data.
- Saved figures go to `../outputs/figures/` (the ≤5 writeup figures).
- Keep heavy logic in `../src/` and import it, so notebooks stay readable and the engine stays testable.
- Memory discipline (ARM64 Pi): chunk / stream the large tracking files.
- Sample outputs are labelled **`UNVALIDATED SAMPLE OUTPUT`**.

> **Status:** Phase 0 only — the pipeline stages are not implemented yet, so the deliverable notebook is a
> later cycle.
