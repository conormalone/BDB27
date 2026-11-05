"""common — shared infrastructure for the BDB27 burst-vs-jump pipeline.

Deliberately small and analysis-free: config, paths, DuckDB, Parquet, provenance
writers, run log, checkpoints, and full-run gates. Nothing in this module knows
what a get-off is.

Provenance contract
-------------------
Every artefact written through :func:`write_csv` / :func:`write_json` /
:func:`write_markdown` carries a provenance stamp keyed off ``mode``. In sample
mode the stamp contains the literal string ``UNVALIDATED SAMPLE OUTPUT``, so a
sample artefact cannot be mistaken for a finding.

Full-run gates
--------------
``--mode full`` refuses to run unless (a) the tracking input is genuinely
bigger than the provided sample, (b) provenance is ``FULL``, and (c) a frozen
prereg exists. There is no override flag.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SAMPLE_STAMP = "UNVALIDATED SAMPLE OUTPUT"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"
TASK_KEY = "bdb27"
MODES = ("sample", "full")

RUN_LOG_FIELDS = ["stage", "mode", "provenance", "wall_s", "peak_rss_mb",
                  "when", "notes"]


class SchemaError(AssertionError):
    """Raised when an input file's actual schema does not match the registry."""


# --------------------------------------------------------------------------- #
# Config / paths
# --------------------------------------------------------------------------- #
def load_config(path: str | os.PathLike | None = None) -> dict:
    import yaml

    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict) or TASK_KEY not in cfg:
        raise KeyError(f"{cfg_path} has no top-level {TASK_KEY!r} block")
    cfg["_config_path"] = str(cfg_path)
    return cfg


def block(cfg: Mapping) -> dict:
    try:
        return cfg[TASK_KEY]
    except KeyError as exc:  # pragma: no cover
        raise KeyError(f"config has no {TASK_KEY!r} block") from exc


def section(cfg: Mapping, name: str) -> dict:
    return block(cfg)[name]


def check_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    return mode


def parse_args(argv: Sequence[str] | None = None, *, with_stage: bool = False):
    parser = argparse.ArgumentParser(add_help=True)
    if with_stage:
        parser.add_argument("stage", nargs="?", default=None)
    parser.add_argument("--mode", choices=MODES, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)
    if args.mode is None:
        args.mode = block(load_config(args.config))["mode"]["default"]
    return args


def abs_path(value: str | os.PathLike) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (REPO_ROOT / p)


def paths(cfg: Mapping, mode: str = "sample") -> dict[str, Path]:
    check_mode(mode)
    p = {k: abs_path(v) for k, v in block(cfg)["paths"].items()}
    for key in ("interim", "parquet", "checkpoints", "tmp"):
        p[key].mkdir(parents=True, exist_ok=True)
    return p


def source_path(cfg: Mapping, key: str) -> list[Path]:
    files = block(cfg)["files"]
    if key not in files:
        raise KeyError(f"unknown files key {key!r}; have {sorted(files)}")
    raw = abs_path(block(cfg)["paths"]["raw"])
    value = files[key]
    values = value if isinstance(value, list) else [value]
    return [Path(v) if Path(v).is_absolute() else raw / v for v in values]


def tracking_sources(cfg: Mapping, mode: str) -> list[Path]:
    return source_path(cfg, block(cfg)["mode"][mode]["tracking_key"])


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def seed_everything(seed: int) -> int:
    seed = int(seed)
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:  # pragma: no cover
        pass
    return seed


def stable_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str,
                         ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# DuckDB
# --------------------------------------------------------------------------- #
def duckdb_connect(cfg: Mapping):
    import duckdb

    rt = section(cfg, "runtime")
    tmp = abs_path(rt["temp_directory"])
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(tmp)
    os.environ["TMP"] = str(tmp)
    os.environ["TEMP"] = str(tmp)
    tempfile.tempdir = str(tmp)

    con = duckdb.connect()
    con.execute(f"SET threads={int(rt['duckdb_threads'])}")
    con.execute(f"SET memory_limit='{rt['duckdb_memory_limit']}'")
    con.execute(f"SET temp_directory='{tmp}'")
    return con


def read_csv_sql(files: Sequence[Path], cfg: Mapping, *,
                 columns: Sequence[str] | None = None) -> str:
    """A read_csv_auto(...) expression honouring the NA null marker."""
    null = section(cfg, "runtime")["csv_null_marker"]
    quoted = "[" + ", ".join(f"'{Path(f).as_posix()}'" for f in files) + "]"
    if len(files) == 1:
        base = f"read_csv_auto({quoted[1:-1]}, nullstr=['{null}'])"
    else:
        base = f"read_csv_auto({quoted}, nullstr=['{null}'], union_by_name=true)"
    if columns:
        return f"(SELECT {', '.join(columns)} FROM {base})"
    return base


def assert_disk_space(cfg: Mapping, mode: str = "sample") -> dict[str, float]:
    rt = section(cfg, "runtime")
    p = paths(cfg, mode)
    interim = Path(p["interim"])
    root_free = shutil.disk_usage(REPO_ROOT).free / 1024 ** 2
    interim_free = shutil.disk_usage(interim).free / 1024 ** 2
    problems = []
    if root_free < float(rt["min_free_mb_root"]):
        problems.append(f"root {root_free:.0f} MB < {rt['min_free_mb_root']} MB")
    if interim_free < float(rt["min_free_mb_interim"]):
        problems.append(f"interim {interim_free:.0f} MB < {rt['min_free_mb_interim']} MB")
    if problems:
        raise RuntimeError("DISK GUARD FAILED: " + "; ".join(problems))
    return {"root_free_mb": root_free, "interim_free_mb": interim_free}


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def provenance(cfg: Mapping, mode: str) -> str:
    check_mode(mode)
    return block(cfg)["mode"][mode]["provenance"]


def provenance_stamp(cfg: Mapping, mode: str) -> str:
    level = provenance(cfg, mode)
    return f"{level}: {SAMPLE_STAMP}" if mode == "sample" else level


def require_full_provenance(cfg: Mapping, mode: str, stage: str) -> str:
    level = provenance(cfg, mode)
    if mode == "full" and level != "FULL":
        raise RuntimeError(
            f"STAGE {stage}: refusing to run — mode=full but provenance is "
            f"{level!r}. Check {TASK_KEY}.mode.full.")
    if level not in ("PLACEHOLDER_SAMPLE", "FULL"):
        raise RuntimeError(f"unknown provenance {level!r}")
    return level


_STATUS_LINE = r"^\s*\*\*\s*Status\s*:(?P<status>.*)$"


def prereg_is_frozen(text: str) -> bool:
    import re
    m = re.search(_STATUS_LINE, text or "", re.I | re.M)
    if not m:
        return False
    raw = m.group("status")
    end = raw.find("**")
    if end != -1:
        raw = raw[:end]
    status = raw.strip().strip("*").strip()
    if not status:
        return False
    if re.search(r"\b(?:NOT|NEVER|UN)[\s_\-]*FROZEN\b", status, re.I):
        return False
    if "draft" in status.lower() or "pending" in status.lower():
        return False
    return bool(re.search(r"\bFROZEN\b", status, re.I))


def require_prereg(cfg: Mapping, mode: str, stage: str) -> Path:
    path = abs_path(block(cfg)["paths"]["prereg"])
    if mode != "full":
        return path
    if not path.exists():
        raise RuntimeError(
            f"STAGE {stage}: refusing to run — mode=full requires {path}, which "
            f"does not exist. Write and freeze the prereg first (TASK.md Rules).")
    if not prereg_is_frozen(path.read_text(encoding="utf-8")):
        raise RuntimeError(
            f"STAGE {stage}: refusing to run — {path} does not declare "
            f"'**Status: FROZEN.**'. A missing, draft or negated status is not a "
            f"freeze.")
    return path


# --------------------------------------------------------------------------- #
# Writers (provenance is mandatory)
# --------------------------------------------------------------------------- #
def _ensure_parent(path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def write_csv(path, rows: Iterable, fieldnames: Sequence[str], *,
              mode: str, cfg: Mapping) -> Path:
    path = Path(path)
    _ensure_parent(path)
    stamp = provenance_stamp(cfg, mode)
    header = ["provenance"] + list(fieldnames)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="raise")
        w.writeheader()
        for row in rows:
            payload = ({k: row.get(k, "") for k in fieldnames}
                       if isinstance(row, Mapping) else dict(zip(fieldnames, row)))
            payload["provenance"] = stamp
            w.writerow(payload)
    os.replace(tmp, path)
    return path


def write_json(path, obj: Mapping, *, mode: str, cfg: Mapping) -> Path:
    if not isinstance(obj, Mapping):
        raise TypeError("write_json expects a mapping")
    path = Path(path)
    _ensure_parent(path)
    payload = {"provenance": provenance_stamp(cfg, mode)}
    payload.update({k: v for k, v in obj.items() if k != "provenance"})
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def write_markdown(path, text: str, *, mode: str, cfg: Mapping) -> Path:
    path = Path(path)
    _ensure_parent(path)
    body = f"<!-- provenance: {provenance_stamp(cfg, mode)} -->\n" + text
    if not body.endswith("\n"):
        body += "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# Run log / timing
# --------------------------------------------------------------------------- #
def _current_rss_bytes() -> int:
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except Exception:  # pragma: no cover
        try:
            import resource
            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
        except Exception:
            return 0


class _RssSampler(threading.Thread):
    def __init__(self, interval_s: float):
        super().__init__(daemon=True)
        self.interval_s = max(0.02, float(interval_s))
        self.peak_bytes = 0
        self._stop = threading.Event()

    def run(self) -> None:  # pragma: no cover - timing thread
        self.peak_bytes = max(self.peak_bytes, _current_rss_bytes())
        while not self._stop.is_set():
            self.peak_bytes = max(self.peak_bytes, _current_rss_bytes())
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=2.0)


class Stage:
    def __init__(self, cfg, stage, mode):
        self.cfg, self.stage, self.mode = cfg, stage, mode
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(str(text))


@contextlib.contextmanager
def timed_stage(cfg: Mapping, stage: str, mode: str, note: str = ""):
    """Time a stage, sample peak RSS, append one row to outputs/run_log.csv."""
    log_path = abs_path(block(cfg)["paths"]["run_log"])
    state = Stage(cfg, stage, mode)
    if note:
        state.note(note)
    interval = section(cfg, "runtime").get("rss_sample_interval_s", 0.2)
    sampler = _RssSampler(interval)
    sampler.peak_bytes = _current_rss_bytes()
    started = time.perf_counter()
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sampler.start()
    try:
        yield state
    except BaseException as exc:
        state.note(f"FAILED: {type(exc).__name__}: {exc}")
        raise
    finally:
        sampler.stop()
        wall = time.perf_counter() - started
        _ensure_parent(log_path)
        new = not log_path.exists() or log_path.stat().st_size == 0
        with open(log_path, "a", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=RUN_LOG_FIELDS)
            if new:
                w.writeheader()
            w.writerow({
                "stage": stage, "mode": mode,
                "provenance": provenance(cfg, mode),
                "wall_s": f"{wall:.3f}",
                "peak_rss_mb": f"{sampler.peak_bytes / 1024 ** 2:.1f}",
                "when": when, "notes": "; ".join(state.notes),
            })


# --------------------------------------------------------------------------- #
# Checkpoints
# --------------------------------------------------------------------------- #
def config_fingerprint(cfg: Mapping, stage: str) -> str:
    return stable_hash({"stage": stage, "seed": block(cfg).get("seed"),
                        TASK_KEY: block(cfg)})


def checkpoint_path(cfg: Mapping, stage: str, mode: str) -> Path:
    fp = config_fingerprint(cfg, stage)[:16]
    return paths(cfg, mode)["checkpoints"] / f"{stage}__{mode}__{fp}.json"


def is_done(cfg: Mapping, stage: str, mode: str, force: bool = False) -> bool:
    if force:
        return False
    p = checkpoint_path(cfg, stage, mode)
    if not p.exists():
        return False
    try:
        return bool(json.loads(p.read_text(encoding="utf-8")).get("done"))
    except (json.JSONDecodeError, OSError):
        return False


def mark_done(cfg: Mapping, stage: str, mode: str, extra: Mapping | None = None) -> Path:
    p = checkpoint_path(cfg, stage, mode)
    payload = {
        "stage": stage, "mode": mode,
        "provenance": provenance(cfg, mode),
        "config_fingerprint": config_fingerprint(cfg, stage),
        "when_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "done": True, "extra": dict(extra or {}),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


# --------------------------------------------------------------------------- #
# Full-run evidence gates
# --------------------------------------------------------------------------- #
def tracking_row_count(cfg: Mapping, mode: str) -> int:
    files = tracking_sources(cfg, mode)
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"tracking input missing: {missing}")
    con = duckdb_connect(cfg)
    try:
        return int(con.execute(
            f"SELECT count(*) FROM {read_csv_sql(files, cfg)}").fetchone()[0])
    finally:
        con.close()


def require_full_tracking(cfg: Mapping, mode: str = "full") -> dict[str, Any]:
    files = tracking_sources(cfg, mode)
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise RuntimeError(
            "FULL MODE REFUSED: tracking input missing:\n  " + "\n  ".join(missing))
    rows = tracking_row_count(cfg, mode)
    cap = int(block(cfg)["mode"]["sample"]["sample_row_cap"])
    if rows <= cap:
        raise RuntimeError(
            f"FULL MODE REFUSED: parsed tracking rows {rows:,} do not exceed the "
            f"sample row cap {cap:,}; this input is sample-sized, so FULL "
            f"provenance cannot be asserted.")
    return {"files": [str(f) for f in files], "rows": rows, "sample_row_cap": cap}


# --------------------------------------------------------------------------- #
# Stage bootstrap shared by 01-05
# --------------------------------------------------------------------------- #
def stage_bootstrap(argv, stage: str):
    """Parse args, load config, apply gates. Returns (cfg, mode, force)."""
    args = parse_args(argv)
    cfg = load_config(args.config)
    mode = check_mode(args.mode)
    seed_everything(int(block(cfg)["seed"]))
    require_full_provenance(cfg, mode, stage)
    require_prereg(cfg, mode, stage)
    if mode == "full":
        require_full_tracking(cfg, mode)
    return cfg, mode, bool(args.force)


def fail_not_implemented(stage: str, why: str) -> int:
    """Loud, non-zero exit for a stage that exists only as a layout stub."""
    print(f"STAGE NOT IMPLEMENTED: {stage}\n{why}", file=sys.stderr)
    return 3
