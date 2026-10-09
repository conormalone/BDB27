"""Shared utilities for the BDB27 pipeline.

Provides: config loading, path resolution, structured logging, a peak-RAM /
wall-time run-logger, deterministic seeding, provenance strings, and a
memory-safe CSV->Parquet converter (DuckDB streaming).

Design rules (TASK.md):
- Never load full CSVs into pandas; convert to Parquet then query with DuckDB.
- Explicit column selection; params come from config.yaml (no magic numbers).
- Every stage appends wall time + peak RAM to outputs/run_log.csv.
"""

from __future__ import annotations

import csv
import logging
import os
import resource
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Sequence

import duckdb
import numpy as np
import yaml

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG: Path = REPO_ROOT / "config.yaml"

_LOGGER_NAME = "bdb27"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load config.yaml (defaults to the repo-root config)."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg["_config_path"] = str(cfg_path)
    return cfg


def resolve(cfg: dict[str, Any], key: str, extra: str | None = None) -> Path:
    """Resolve a path from cfg['paths'][key] (optionally joined with `extra`)."""
    base = REPO_ROOT / cfg["paths"][key]
    return base if extra is None else base / extra


def get_logger(level: int = logging.INFO) -> logging.Logger:
    """Return a process-wide console logger."""
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(h)
    logger.setLevel(level)
    return logger


def provenance(cfg: dict[str, Any], mode: str) -> str:
    """Return the provenance label for a run mode ('UNVALIDATED SAMPLE OUTPUT'|'FULL')."""
    return str(cfg["mode"][mode]["provenance"])


def seed_everything(seed: int) -> None:
    """Seed numpy (and stdlib) RNGs for determinism."""
    np.random.seed(seed)
    import random

    random.seed(seed)


# --------------------------------------------------------------------------- #
# Peak RAM + wall time
# --------------------------------------------------------------------------- #
def peak_ram_mb() -> float:
    """Process peak resident set size in MiB (Linux ru_maxrss is in KiB)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


@contextmanager
def timed_stage(cfg: dict[str, Any], stage: str, mode: str, **notes: Any):
    """Context manager: time a stage and append a row to outputs/run_log.csv."""
    t0 = time.perf_counter()
    err: str = ""
    try:
        yield
    except Exception as exc:  # log then re-raise so failures are visible
        err = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        wall_s = time.perf_counter() - t0
        append_run_log(cfg, stage, mode, wall_s, peak_ram_mb(), err, notes)


def append_run_log(
    cfg: dict[str, Any],
    stage: str,
    mode: str,
    wall_s: float,
    peak_mb: float,
    err: str = "",
    notes: dict[str, Any] | None = None,
) -> None:
    """Append one row to the run log (header written on first use)."""
    log_path = REPO_ROOT / cfg["logging"]["run_log"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["timestamp", "stage", "mode", "wall_s", "peak_ram_mb", "error", "notes"]
    notes = notes or {}
    note_str = "; ".join(f"{k}={v}" for k, v in notes.items())
    write_header = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if write_header:
            w.writeheader()
        w.writerow(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "stage": stage,
                "mode": mode,
                "wall_s": round(wall_s, 3),
                "peak_ram_mb": round(peak_mb, 1),
                "error": err,
                "notes": note_str,
            }
        )


# --------------------------------------------------------------------------- #
# I/O: CSV -> Parquet (memory-safe, streamed by DuckDB)
# --------------------------------------------------------------------------- #
def connect(threads: int | None = None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with a bounded thread count."""
    con = duckdb.connect()
    if threads is not None:
        con.execute(f"PRAGMA threads={int(threads)}")
    return con


def csv_path(cfg: dict[str, Any], key_or_name: str) -> Path:
    """Resolve a CSV path: if `key_or_name` is under cfg['files'], use that value."""
    name = cfg["files"].get(key_or_name, key_or_name)
    return resolve(cfg, "raw", name)


def to_parquet(
    con: duckdb.DuckDBPyConnection,
    src_csv: Path,
    dst_parquet: Path,
    columns: Sequence[str] | None = None,
    force: bool = False,
    logger: logging.Logger | None = None,
    nullstr: Sequence[str] | None = ("NA", ""),
) -> Path:
    """Convert a CSV to Parquet via DuckDB COPY (streamed, low memory).

    Skips when `dst_parquet` exists unless `force=True` (checkpointing).
    `nullstr` maps placeholder tokens to SQL NULL; the BDB CSVs use "NA" for
    opted-out / unavailable values, which must NOT be kept as literal strings.
    """
    dst_parquet.parent.mkdir(parents=True, exist_ok=True)
    if dst_parquet.exists() and not force:
        if logger:
            logger.info("parquet cache hit: %s", dst_parquet.name)
        return dst_parquet
    if not src_csv.exists():
        raise FileNotFoundError(f"missing source CSV: {src_csv}")
    cols = "*" if not columns else ", ".join(f'"{c}"' for c in columns)
    opts = "header=true"
    if nullstr:
        opts += ", nullstr=[" + ", ".join(f"'{s}'" for s in nullstr) + "]"
    sql = (
        f"COPY (SELECT {cols} FROM read_csv_auto('{src_csv}', {opts})) "
        f"TO '{dst_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    if logger:
        logger.info("converting %s -> %s", src_csv.name, dst_parquet.name)
    con.execute(sql)
    return dst_parquet


def pq(path: Path) -> str:
    """Return a DuckDB read_parquet() call string for `path`."""
    return f"read_parquet('{path}')"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: Sequence[str] | None = None) -> None:
    """Write an iterable of row dicts to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if not rows and fieldnames is None:
        raise ValueError(f"cannot infer header for empty table: {path}")
    fields = list(fieldnames) if fieldnames else list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
