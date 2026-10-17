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
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # POSIX-only stdlib module — it does not exist on Windows.
    import resource as _resource
except ImportError:  # pragma: no cover - platform dependent (raises on win32)
    _resource = None

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
# Documented sentinel written to `peak_ram_mb` in run_log.csv when no portable
# measurement is available (e.g. an exotic platform where neither the POSIX
# `resource` API nor the Windows psapi fallback can be used). A stage must NEVER
# fail because memory measurement is unavailable (TASK.md logging contract), so
# this is recorded instead of raising. Negative => "unknown", distinguishable
# from a genuine 0 MiB reading.
RAM_UNKNOWN_MB: float = -1.0


def _peak_ram_mb_posix() -> float | None:
    """Peak RSS in MiB via POSIX ``getrusage``, or ``None`` if unavailable.

    ``ru_maxrss`` units are platform-specific: **KiB on Linux**, **bytes on
    macOS/BSD**; both are normalised to MiB here.
    """
    if _resource is None:
        return None
    try:
        raw = float(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss)
    except Exception:  # noqa: BLE001 - never let measurement kill a stage
        return None
    if sys.platform == "darwin":  # macOS/BSD report bytes
        return raw / (1024.0 * 1024.0)
    return raw / 1024.0  # Linux/other POSIX report KiB


def _peak_ram_mb_windows() -> float | None:
    """Peak working set in MiB via Windows ``psapi``, or ``None`` if unavailable.

    Uses only the stdlib ``ctypes`` module (no new runtime dependency) and
    returns ``None`` on non-Windows platforms or if the API call fails.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        psapi = ctypes.WinDLL("psapi")  # type: ignore[attr-defined]
        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
        ok = psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        )
        if not ok:
            return None
        return counters.PeakWorkingSetSize / (1024.0 * 1024.0)  # bytes -> MiB
    except Exception:  # noqa: BLE001 - never let measurement kill a stage
        return None


def peak_ram_mb() -> float:
    """Process peak memory footprint in MiB — portable across platforms.

    Measurement chain (first success wins):

    * **POSIX** (``resource.getrusage``; Linux/macOS) — KiB on Linux, bytes on
      macOS/BSD, normalised to MiB.
    * **Windows** (``psapi.GetProcessMemoryInfo`` via stdlib ``ctypes``) —
      ``PeakWorkingSetSize`` in bytes, normalised to MiB.
    * otherwise the documented sentinel :data:`RAM_UNKNOWN_MB` (``-1.0``).

    Never raises: if every probe fails the sentinel is returned so a stage can
    still log its run (wall time + a clearly labelled unknown RAM value).
    """
    for probe in (_peak_ram_mb_posix, _peak_ram_mb_windows):
        value = probe()
        if value is not None:
            return value
    return RAM_UNKNOWN_MB


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
# Stage checkpoints — a marker from one mode must never satisfy another mode
# --------------------------------------------------------------------------- #
def read_done_marker(path: Path | str) -> dict:
    """Read a stage ``_DONE``/``_DONE.json`` marker; an absent/unreadable marker is ``{}``."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return rec if isinstance(rec, dict) else {}


def checkpoint_match(rec: dict, *, mode: str, provenance: str | None = None,
                     synthetic: bool | None = None, regime: str | None = None) -> bool:
    """True only when the recorded marker matches the requested run exactly.

    Mode is always required. A legacy/plain-text marker (parsed as ``{}``) never matches, so the
    stage rebuilds instead of silently reusing outputs of unknown provenance.
    """
    if not isinstance(rec, dict) or rec.get("mode") != mode:
        return False
    if provenance is not None and rec.get("provenance") != provenance:
        return False
    if synthetic is not None and bool(rec.get("synthetic")) != bool(synthetic):
        return False
    if regime is not None and rec.get("regime") != regime:
        return False
    return True


def checkpoint_mismatch_note(rec: dict, *, mode: str, provenance: str | None = None,
                             synthetic: bool | None = None, regime: str | None = None) -> str:
    """Human-readable marker-vs-request description for the rebuild log line."""
    return (f"marker mode={rec.get('mode')!r} provenance={rec.get('provenance')!r} "
            f"synthetic={rec.get('synthetic')!r} regime={rec.get('regime')!r} != requested "
            f"mode={mode!r} provenance={provenance!r} synthetic={synthetic!r} regime={regime!r}")


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
