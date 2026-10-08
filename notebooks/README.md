# Notebooks

Runnable, self-contained `.ipynb` files — the **primary interface** for this project (no command-line steps).

## Conventions
- Numbered prefixes: `01_data_load.ipynb`, `02_eda.ipynb`, `03_prototype_decay.ipynb`, …
- **Self-contained**: each opens with its imports + data paths and runs top-to-bottom without hidden state.
- **Data path** points at `../data/raw` (symlink to the USB) — never commit data.
- Saved figures go to `../outputs/figures/`.
- Keep heavy logic in `../src/` and import it, so notebooks stay readable.
- Memory discipline (ARM64 Pi): chunk / stream the large tracking files.
