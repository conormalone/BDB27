"""DL pass-rush chain (combine acceleration ceiling -> in-game decline).

This package is a *fork* of the leverage-x-ceiling engine that reads its own
config block, ``bdb27_dl:``.  It never touches ``bdb27_lc:`` state: paths,
checkpoints and config fingerprints are disjoint, so an LC run and a DL run can
share the repo without invalidating each other.

Modules
-------
dl_common        engine (config, paths, parquet, provenance, checkpoints)
stage_00_thresholds .. stage_05_validate   stage entry points
run              dispatcher: ``python -m dl.run <stage> --mode sample``
"""

from __future__ import annotations

__all__ = ["dl_common"]
