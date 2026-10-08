# src

Reusable / production code — **coder-owned**, implemented via the PM pipeline.

## Rules
- No analysis decisions here — those live in `../specs/`.
- Reusable functionality that notebooks import (loaders, signal extraction, modelling helpers).
- Memory-efficient for the ARM64 Pi: chunk / stream the big tracking files.
- Notebooks **call** these modules; they don't duplicate the logic.
