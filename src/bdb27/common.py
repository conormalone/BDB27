"""Shared runtime for the BDB27 Turn Coverage pipeline.

One code path for ``--mode sample`` and ``--mode full``. Everything that could
differ between the two lives in ``config.yaml``; this module only decides *where*
the files are and *what provenance stamp* an artefact carries.

Rules enforced here (TASK.md "Engineering"):
  * paths, thresholds and sizes come from config.yaml — no magic numbers above;
  * sample outputs are stamped ``UNVALIDATED SAMPLE OUTPUT``;
  * every stage logs wall time and peak RAM to ``outputs/run_log.csv``;
  * full CSVs are never loaded into pandas — they are converted to Parquet and
    queried (DuckDB) with explicit column selection.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import duckdb
import pandas as pd
import yaml

SAMPLE_LABEL = "UNVALIDATED SAMPLE OUTPUT"
PROVENANCE_SAMPLE = "PLACEHOLDER_SAMPLE"
PROVENANCE_FULL = "FULL"

#: config key holding every task-specific setting
BLOCK = "turn_coverage"


class StageRefused(RuntimeError):
    """Raised when a stage cannot honestly run (missing inputs, bad provenance)."""


@dataclass(frozen=True)
class Config:
    raw: dict
    path: Path

    # ---- convenience accessors -------------------------------------------------
    @property
    def block(self) -> dict:
        return self.raw[BLOCK]

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.block
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is not None:
                    return default
                raise KeyError(f"config.yaml: missing {BLOCK}.{dotted}")
            node = node[part]
        return node

    def repo_path(self, dotted: str) -> Path:
        """Resolve a path entry (relative paths are repo-root relative)."""
        p = Path(self.get(f"paths.{dotted}"))
        return p if p.is_absolute() else self.path.parent / p

    def raw_file(self, mode: str, key: str) -> Path:
        """Resolve a raw input file for the requested mode.

        ``files.<key>`` is the full-data filename; ``sample_files.<key>`` (when
        present) is the sample filename. A missing file is *named* in the error.
        """
        table = self.block["files"] if mode == "full" else {**self.block["files"], **self.block.get("sample_files", {})}
        if key not in table:
            raise StageRefused(f"config.yaml has no entry for raw file '{key}'")
        return self.repo_path("raw") / table[key]

    @property
    def seed(self) -> int:
        return int(self.block["seed"])

    def thresholds_path(self) -> Path:
        return self.repo_path("outputs") / "thresholds.json"

    def audit_dir(self) -> Path:
        return self.repo_path("audit")

    def parquet_dir(self) -> Path:
        return self.repo_path("parquet")


def load_config(path: str | os.PathLike | None = None) -> Config:
    p = Path(path or os.environ.get("BDB27_CONFIG", "config.yaml"))
    if not p.is_absolute():
        # resolve against cwd, then against the repo root (parent of src/)
        cand = Path.cwd() / p
        if not cand.exists():
            cand = Path(__file__).resolve().parents[2] / p
        p = cand
    if not p.exists():
        raise StageRefused(f"config not found: {p}")
    with open(p, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if BLOCK not in raw:
        raise StageRefused(f"config.yaml has no '{BLOCK}:' block")
    return Config(raw=raw, path=p.resolve())


def config_fingerprint(cfg: Config) -> str:
    """Stable hash of the task block — recorded in every provenance stamp."""
    payload = json.dumps(cfg.block, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def provenance(mode: str) -> str:
    if mode not in ("sample", "full"):
        raise ValueError(f"mode must be sample|full, got {mode!r}")
    return PROVENANCE_SAMPLE if mode == "sample" else PROVENANCE_FULL


def stamp(mode: str) -> str:
    """The banner that must appear on every sample-mode artefact."""
    return SAMPLE_LABEL if mode == "sample" else "FULL DATA OUTPUT"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def require_inputs(paths: Iterable[Path], *, stage: str) -> None:
    """Refuse honestly: name every missing input, run nothing."""
    missing = [str(p) for p in paths if not Path(p).exists()]
    if missing:
        raise StageRefused(
            f"{stage}: required inputs are absent — refusing to run.\n  missing:\n"
            + "\n".join(f"    - {m}" for m in missing)
            + "\n  (sample mode uses sample_files.*; full mode uses files.*)"
        )


# ---------------------------------------------------------------- run logging
_RUN_LOG_LOCK = None


def log_run(stage: str, mode: str, wall_s: float, rows: int, status: str,
            detail: str = "", cfg: Config | None = None) -> None:
    """Append one row to outputs/run_log.csv (peak RSS of this process)."""
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    row = {
        "utc": utc_now(),
        "stage": stage,
        "mode": mode,
        "provenance": provenance(mode),
        "status": status,
        "wall_s": round(wall_s, 3),
        "peak_rss_mb": round(peak_mb, 1),
        "rows": int(rows),
        "detail": detail,
    }
    if cfg is None:
        cfg = load_config()
    path = cfg.repo_path("outputs") / "run_log.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)


class StageTimer:
    """Context manager: wall time + peak RAM + run-log row."""

    def __init__(self, stage: str, mode: str, cfg: Config, detail: str = ""):
        self.stage, self.mode, self.cfg, self.detail = stage, mode, cfg, detail
        self.rows = 0
        # Callers may override the success status inside the `with` block (e.g.
        # NONCONVERGED) without changing the context-manager contract.
        self.status = "OK"

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        status = self.status if exc_type is None else (
            "REFUSED" if exc_type is StageRefused else "ERROR")
        log_run(self.stage, self.mode, time.perf_counter() - self.t0, self.rows,
                status, self.detail or (str(exc) if exc else ""), cfg=self.cfg)
        return False


# ------------------------------------------------------------------ data access
def connect(cfg: Config) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{cfg.get('resources.duckdb_memory_limit')}'")
    con.execute(f"SET threads={int(cfg.get('resources.duckdb_threads'))}")
    return con


def raw_csv_sql(cfg: Config, mode: str, key: str, columns: Sequence[str] | str = "*") -> str:
    """A DuckDB read_csv_auto expression with explicit column selection."""
    path = cfg.raw_file(mode, key)
    cols = columns if isinstance(columns, str) else ", ".join(columns)
    # 'NA' is the vendor's null token in these files; without this, boolean
    # columns that are partly 'NA' are inferred as VARCHAR.
    return (f"read_csv_auto('{path}', header=true, "
            f"sample_size={int(cfg.get('resources.csv_sniff_rows'))}, nullstr='NA')")


def ensure_parquet(cfg: Config, mode: str, key: str, columns: Sequence[str],
                   force: bool = False) -> Path:
    """Convert one raw CSV to Parquet once; return the parquet path."""
    out = cfg.parquet_dir() / mode / f"{key}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not force:
        return out
    src = cfg.raw_file(mode, key)
    require_inputs([src], stage=f"parquet:{key}")
    con = connect(cfg)
    tmp = out.with_suffix(".parquet.tmp")
    con.execute(
        f"COPY (SELECT {', '.join(columns)} FROM {raw_csv_sql(cfg, mode, key)}) "
        f"TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    tmp.replace(out)
    con.close()
    return out


def write_csv(df: pd.DataFrame, path: Path, mode: str, cfg: Config,
              extra: dict | None = None) -> Path:
    """Write an evidence CSV with the provenance stamp as leading columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out.insert(0, "provenance", provenance(mode))
    out.insert(1, "label", stamp(mode))
    out.insert(2, "config_sha", config_fingerprint(cfg))
    for k, v in (extra or {}).items():
        out[k] = v
    out.to_csv(path, index=False)
    return path


def write_json(obj: dict, path: Path, mode: str, cfg: Config) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provenance": provenance(mode),
        "label": stamp(mode),
        "config_sha": config_fingerprint(cfg),
        "generated_utc": utc_now(),
        **obj,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


# -------------------------------------------------------------- parquet output
def _arrow_metadata(mode: str, cfg: Config) -> dict[bytes, bytes]:
    """File-level metadata for an evidence Parquet file.

    No timestamp: the turn table itself must be byte-identical when the same
    inputs and seed are re-run, so the volatile fields live in the JSON manifest.
    """
    return {
        b"provenance": provenance(mode).encode(),
        b"label": stamp(mode).encode(),
        b"config_sha": config_fingerprint(cfg).encode(),
    }


def _arrow_array(column, typ):
    """Build a typed Arrow array from a pandas column with nulls."""
    import pyarrow as pa

    values = column.tolist()
    if pa.types.is_boolean(typ):
        clean = []
        for v in values:
            if v is None or v is pd.NA:
                clean.append(None)
            elif isinstance(v, float) and pd.isna(v):
                clean.append(None)
            else:
                clean.append(bool(v))
        return pa.array(clean, type=typ)
    if pa.types.is_string(typ):
        return pa.array([None if v is None or v is pd.NA else str(v) for v in values], type=typ)
    return pa.array(values, type=typ, from_pandas=True)


class ParquetSink:
    """Stream row groups to one Parquet file with a fixed schema.

    Game tracking is processed in chunks and never all at once, so the turn table
    is written incrementally instead of concatenating every chunk in RAM. The
    schema is fixed up front: every chunk is cast to it, so chunk order cannot
    change the file's columns.
    """

    def __init__(self, path: Path, schema, mode: str, cfg: Config):
        import pyarrow.parquet as pq

        self.path = path
        self.rows = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = schema.with_metadata(_arrow_metadata(mode, cfg))
        self._writer = pq.ParquetWriter(str(path), self.schema, compression="zstd")

    def write(self, df: pd.DataFrame) -> None:
        import pyarrow as pa

        arrays = [_arrow_array(df[f.name], f.type) for f in self.schema]
        self._writer.write_table(pa.Table.from_arrays(arrays, schema=self.schema))
        self.rows += len(df)

    def close(self) -> None:
        self._writer.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def write_parquet(df: pd.DataFrame, path: Path, schema, mode: str, cfg: Config) -> Path:
    """Write a small table with the same stamped, deterministic schema as the sink."""
    with ParquetSink(path, schema, mode, cfg) as sink:
        if len(df):
            sink.write(df)
    return path


def git_rev(repo: Path | None = None) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo or Path(__file__).resolve().parents[2]), text=True).strip()
    except Exception:  # pragma: no cover - only outside a git checkout
        return "unknown"


def die(stage: str, msg: str, code: int = 2) -> None:
    print(f"[{stage}] {msg}", file=sys.stderr)
    raise SystemExit(code)
