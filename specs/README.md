# Specs

Methodology and design specs — **PM-owned**. One file per decision, upstream of code.

Examples:
- **Identification strategy** — how we separate a real fatigue effect from the confounds (combine day = position group; drill order vs drill type collinearity; non-random opt-outs).
- Model design, window / attempt definitions, covariates.
- Nulls and robustness checks.

`src/` implements what is specified here — decisions live here, not in the code.
