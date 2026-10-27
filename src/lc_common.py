"""lc_common — shared helpers for the BDB27 leverage x ceiling task.

One code path for ``--mode sample|full``.  Everything numeric comes from
``config.yaml`` under the ``bdb27_lc:`` block; this module holds no design
constants beyond dtype names and the provenance stamp.

Provenance contract
-------------------
* :func:`provenance` returns the *level*: ``"PLACEHOLDER_SAMPLE"`` or ``"FULL"``.
* :func:`write_csv` / :func:`write_json` are the only supported writers for
  deliverables.  They stamp the provenance themselves, keyed off ``mode``, so a
  caller cannot accidentally emit an unmarked sample artefact.  The stamp value
  is ``"<level>: UNVALIDATED SAMPLE OUTPUT"`` in sample mode and ``"FULL"`` in
  full mode.
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

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"

SAMPLE_STAMP = "UNVALIDATED SAMPLE OUTPUT"

# --------------------------------------------------------------------------- #
# Schema declarations (canonical dtype families, see notes/schema.md)
# --------------------------------------------------------------------------- #
#: Canonical families: int, float, str, bool, timestamp, date, time.
EXPECTED_SCHEMAS: dict[str, dict[str, Any]] = {
    "combine_tracking": {
        "columns": {
            "draft_year": "int", "event_id": "str", "nfl_id": "int",
            "entity_type": "str", "time": "timestamp", "drill_type": "str",
            "drill_name": "str", "attempt": "int", "x": "float", "y": "float",
            "s": "float", "a": "float", "dis": "float", "dir": "float",
        },
        "units": {"time": "timestamp (absolute wall clock)",
                  "x": "yd", "y": "yd", "s": "yd/s", "a": "yd/s^2",
                  "dis": "UNRESOLVED (NOT yards; do not use)",
                  "dir": "degrees"},
    },
    "combine_results": {
        "columns": {
            "draft_year": "int", "nfl_id": "int", "combine_position": "str",
            "combine_height": "float", "combine_weight": "int",
            "hand_size": "float", "arm_length": "float", "wing_span": "float",
            "ten_yd_split": "float", "forty": "float", "vertical": "float",
            "broad_jump": "int", "three_cone": "float",
            "short_shuttle": "float", "bench_reps": "int",
            "ngs_athleticism_score": "int",
            "ngs_college_production_score": "int", "ngs_final_score": "int",
        },
        "units": {"combine_height": "in", "combine_weight": "lb",
                  "hand_size": "in", "arm_length": "in", "wing_span": "in"},
    },
    "players": {
        "columns": {
            "nfl_id": "int", "display_name": "str", "draft_year": "int",
            "nfl_position": "str", "birth_date": "date", "college_name": "str",
            "college_conference": "str", "draft_round": "int",
            "draft_pick_within_round": "int", "draft_overall_pick": "int",
        },
        "units": {},
    },
    "games": {
        "columns": {
            "game_id": "int", "game_key": "int", "season": "int",
            "season_type": "str", "week": "int", "home_team_abbr": "str",
            "visitor_team_abbr": "str",
        },
        "units": {},
    },
    "game_tracking": {
        "columns": {
            "game_id": "int", "play_id": "int", "nfl_id": "int",
            "time": "timestamp", "x": "float", "y": "float", "s": "float",
            "a": "float", "dis": "float", "o": "float", "dir": "float",
            "event": "str",
        },
        "units": {"time": "timestamp", "x": "yd", "y": "yd", "s": "yd/s",
                  "a": "yd/s^2", "dis": "UNRESOLVED (do not use)",
                  "o": "yd", "dir": "degrees"},
    },
    "player_play": {
        "columns": {
            "game_id": "int", "play_id": "int", "team_abbr": "str",
            "nfl_id": "int", "lined_up_position": "str",
            "play_description": "str", "quarter": "int", "down": "int",
            "yards_to_go": "int", "possession_team": "str",
            "yardline_side": "str", "yardline_number": "int",
            "game_clock": "time", "pre_snap_home_score": "int",
            "pre_snap_visitor_score": "int", "play_direction": "str",
            "pre_snap_home_team_win_probability": "float",
            "pre_snap_visitor_team_win_probability": "float",
            "home_team_win_probability_added": "float",
            "visitor_team_win_probility_added": "float",
            "expected_points_added": "float", "expected_points": "float",
            "offense_formation": "str", "receiver_alignment": "str",
            "pass_result": "str", "pass_length": "int",
            "play_nullified_by_penalty": "str", "penalty_yards": "int",
            "pre_penalty_yards_gained": "int", "yards_gained": "int",
            "team_coverage_man_zone": "str", "team_coverage_type": "str",
            "in_motion_at_ball_snap": "bool", "target": "bool",
            "rec_yards": "int", "rush_yards": "int",
            "yards_after_catch": "int", "fumble": "int",
            "fumble_lost": "int", "route_ran": "str",
            "separation_at_pass_forward": "float", "cushion": "float",
            "expected_yards_after_catch": "float", "dropback_duration": "float",
            "extended_sack_allowed": "float",
            "pass_rushers_encountered": "int",
            "peak_pressure_probability_allowed": "float",
            "pressure_allowed": "bool", "sack_allowed": "float",
            "time_to_pressure_allowed": "float", "quick_pressure": "bool",
            "unblocked_pressure": "bool", "blitzing": "bool",
            "player_get_off": "float", "sack": "float", "tackle": "int",
            "assist": "int", "caused_forced_fumble": "int",
            "recovered_fumble": "bool", "tackle_for_loss": "bool",
            "time_to_pressure": "float", "time_to_qb_hurry": "float",
            "coverage_assignment": "str",
            "coverage_assignment_at_snap": "str",
        },
        "units": {"game_clock": "time of day (mm:ss remaining)",
                  "yardline_number": "yd from own goal line",
                  "pre_snap_home_team_win_probability": "probability 0-1",
                  "expected_points": "points"},
    },
}

#: DuckDB read types used by tests to prove the declared schema accepts every
#: value in the real files (``NA`` treated as NULL, ``""`` preserved).
DUCKDB_TYPES = {
    "int": "BIGINT", "float": "DOUBLE", "str": "VARCHAR", "bool": "BOOLEAN",
    "timestamp": "TIMESTAMP", "date": "DATE", "time": "TIME",
}

_ALIASES = {
    "int": {"int", "int8", "int16", "int32", "int64", "uint8", "uint16",
            "uint32", "uint64", "bigint", "integer", "smallint", "tinyint",
            "hugeint", "long", "i", "u"},
    "float": {"float", "float8", "float16", "float32", "float64", "double",
              "decimal", "f", "real", "numeric"},
    "str": {"str", "string", "object", "varchar", "text", "category", "utf8"},
    "bool": {"bool", "boolean", "b"},
    "timestamp": {"timestamp", "datetime", "datetime64", "timestamp_ns",
                  "timestamp_us", "timestamp_ms", "datetime64[ns]"},
    "date": {"date", "date32", "date64"},
    "time": {"time", "time64"},
}


class SchemaError(AssertionError):
    """Raised by :func:`schema_check` with a diff of the offending columns."""


# --------------------------------------------------------------------------- #
# Config / mode / paths
# --------------------------------------------------------------------------- #
def load_config(path: str | os.PathLike | None = None) -> dict:
    """Load ``config.yaml`` and assert the active task block exists."""
    import yaml

    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict) or "bdb27_lc" not in cfg:
        raise KeyError(f"{cfg_path} has no top-level 'bdb27_lc' block")
    cfg["_config_path"] = str(cfg_path)
    return cfg


def lc(cfg: Mapping) -> dict:
    """Return the active-task config block."""
    try:
        return cfg["bdb27_lc"]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError("config has no 'bdb27_lc' block") from exc


def mode_from_argv(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the common CLI: ``--mode sample|full``, ``--force``, ``--config``."""
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--mode", choices=("sample", "full"), default=None,
                        help="sample (row-capped provided data) or full (final run)")
    parser.add_argument("--force", action="store_true",
                        help="ignore checkpoints / rebuild parquet")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    args = parser.parse_args(argv)
    if args.mode is None:
        cfg = load_config(args.config)
        args.mode = lc(cfg)["mode"]["default"]
    return args


def _abs(cfg: Mapping, value: str | os.PathLike) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (REPO_ROOT / p)


def resolve_paths(cfg: Mapping, mode: str) -> dict[str, Path]:
    """Resolve configured paths to absolute ``Path`` objects.

    Only directories under ``data/interim`` are created here (interim, parquet,
    checkpoints, tmp).  Small output directories are created lazily by the
    writers so nothing bulky can be created outside the interim volume.
    """
    if mode not in ("sample", "full"):
        raise ValueError(f"mode must be 'sample' or 'full', got {mode!r}")
    p = lc(cfg)["paths"]
    paths = {key: _abs(cfg, val) for key, val in p.items()}
    for key in ("interim", "parquet", "checkpoints", "tmp"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def source_paths(cfg: Mapping, key: str, mode: str | None = None) -> list[Path]:
    """Resolve a ``files:`` key to one or more raw source paths.

    A key whose value is a list (``game_tracking_full``) expands to that list.
    """
    files = lc(cfg)["files"]
    if key not in files:
        raise KeyError(f"unknown files key {key!r}; have {sorted(files)}")
    raw = _abs(cfg, lc(cfg)["paths"]["raw"])
    value = files[key]
    values = value if isinstance(value, list) else [value]
    return [(raw / v) if not Path(v).is_absolute() else Path(v) for v in values]


def tracking_source(cfg: Mapping, mode: str) -> list[Path]:
    """Tracking file(s) for the given mode."""
    key = lc(cfg)["mode"][mode]["tracking_key"]
    return source_paths(cfg, key, mode)


# --------------------------------------------------------------------------- #
# Disk guard
# --------------------------------------------------------------------------- #
def assert_disk_space(paths: Mapping[str, Path], cfg: Mapping) -> dict[str, float]:
    """Fail loudly unless root and interim volumes have enough free space.

    The root filesystem and the interim (USB) volume are measured separately:
    ``shutil.disk_usage("/")`` describes the root fs, *not* the USB mount.
    Returns the measured free MB per volume.
    """
    rt = lc(cfg)["runtime"]
    root_path = REPO_ROOT
    interim_path = Path(paths["interim"])
    if not interim_path.exists():
        interim_path = interim_path.parent
    root = shutil.disk_usage(root_path)
    interim = shutil.disk_usage(interim_path)
    root_free = root.free / (1024 ** 2)
    interim_free = interim.free / (1024 ** 2)
    need_root = float(rt["min_free_mb_root"])
    need_interim = float(rt["min_free_mb_interim"])
    problems = []
    if root_free < need_root:
        problems.append(
            f"root filesystem {root_path}: {root_free:.1f} MB free < required "
            f"{need_root:.0f} MB")
    if interim_free < need_interim:
        problems.append(
            f"interim volume {interim_path}: {interim_free:.1f} MB free < required "
            f"{need_interim:.0f} MB")
    if problems:
        raise RuntimeError(
            "DISK GUARD FAILED — refusing to run.\n  " + "\n  ".join(problems) +
            f"\n  (root device free: {root_free:.1f} MB; interim device free: "
            f"{interim_free:.1f} MB)")
    return {"root_free_mb": root_free, "interim_free_mb": interim_free}


# --------------------------------------------------------------------------- #
# DuckDB
# --------------------------------------------------------------------------- #
def duckdb_connect(cfg: Mapping):
    """Open DuckDB with configured threads/memory and interim temp directory.

    Also redirects ``TMPDIR``/``TMP``/``TEMP`` and ``tempfile.tempdir`` to the
    interim volume.  The root tmpfs has roughly 100 MB free, so a DuckDB spill
    or a numpy temp file must never land there.
    """
    import duckdb

    rt = lc(cfg)["runtime"]
    tmp = _abs(cfg, rt["temp_directory"])
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


def _csv_reader_sql(path: Path, columns: Sequence[str] | Mapping[str, str] | None,
                    cfg: Mapping) -> str:
    rt = lc(cfg)["runtime"]
    null_marker = rt.get("csv_null_marker", "NA")
    base = f"read_csv_auto('{Path(path).as_posix()}', nullstr=['{null_marker}'])"
    if columns is None:
        return base
    if isinstance(columns, Mapping):
        select = ", ".join(f"{expr} AS {name}" for name, expr in columns.items())
    else:
        select = ", ".join(columns)
    return f"(SELECT {select} FROM {base})"


def to_parquet(cfg: Mapping, csv_key: str, out_name: str,
               columns: Sequence[str] | Mapping[str, str] | None = None,
               mode: str = "sample", force: bool = False,
               source: Sequence[Path] | Path | None = None) -> Path:
    """Convert raw CSV(s) to a column-pruned Parquet file under ``parquet``.

    Idempotent: skips when the parquet is newer than every source unless
    ``force``.  ``columns`` is required in spirit — pass the exact columns the
    downstream stage reads.  A mapping form allows SQL expressions
    (``{"wp": "TRY_CAST(pre_snap_home_team_win_probability AS DOUBLE)"}``).
    """
    paths = resolve_paths(cfg, mode)
    rt = lc(cfg)["runtime"]
    out = paths["parquet"] / (out_name if str(out_name).endswith(".parquet")
                              else f"{out_name}.parquet")

    if source is None:
        srcs = source_paths(cfg, csv_key, mode)
    elif isinstance(source, (str, os.PathLike)):
        srcs = [Path(source)]
    else:
        srcs = [Path(s) for s in source]
    missing = [str(s) for s in srcs if not s.exists()]
    if missing:
        raise FileNotFoundError(f"source file(s) missing: {missing}")
    newest_src = max(s.stat().st_mtime for s in srcs)
    if out.exists() and not force and out.stat().st_mtime >= newest_src:
        return out

    if len(srcs) == 1:
        reader = _csv_reader_sql(srcs[0], columns, cfg)
    else:
        # Explicit schema comes from the first file; all files must match.
        list_sql = "[" + ", ".join(f"'{s.as_posix()}'" for s in srcs) + "]"
        if columns is None:
            columns = list(EXPECTED_SCHEMAS["game_tracking"]["columns"])
        select = ", ".join(columns)
        reader = (f"(SELECT {select} FROM read_csv_auto({list_sql}, "
                  f"nullstr=['{rt.get('csv_null_marker', 'NA')}'], union_by_name=true))")

    con = duckdb_connect(cfg)
    try:
        tmp_out = out.with_suffix(".parquet.tmp")
        con.execute(
            f"COPY (SELECT * FROM {reader}) TO '{tmp_out.as_posix()}' "
            f"(FORMAT PARQUET, COMPRESSION {rt['parquet_compression']}, "
            f"ROW_GROUP_SIZE {int(rt['parquet_row_group_size'])})")
        os.replace(tmp_out, out)
    finally:
        con.close()
    return out


# --------------------------------------------------------------------------- #
# Provenance / writers
# --------------------------------------------------------------------------- #
def provenance(cfg: Mapping, mode: str) -> str:
    """Provenance level: ``PLACEHOLDER_SAMPLE`` | ``FULL``."""
    return lc(cfg)["mode"][mode]["provenance"]


def provenance_stamp(cfg: Mapping, mode: str) -> str:
    """Exact value written into artefacts."""
    level = provenance(cfg, mode)
    return f"{level}: {SAMPLE_STAMP}" if mode == "sample" else level


def require_full_provenance(cfg: Mapping, mode: str, stage: str) -> str:
    """Assert full-mode provenance is genuinely FULL; returns the level.

    Scripts 03-05 call this before writing a reportable number.  In sample mode
    it returns ``PLACEHOLDER_SAMPLE`` without raising (sample runs are allowed,
    their outputs are stamped and non-reportable).
    """
    level = provenance(cfg, mode)
    if mode == "full" and level != "FULL":
        raise RuntimeError(
            f"STAGE {stage!r}: refusing to run — mode=full but provenance is "
            f"{level!r}, not 'FULL'. Re-run src/00_thresholds.py with the full "
            f"tracking input present, and check config bdb27_lc.mode.full.")
    if level not in ("PLACEHOLDER_SAMPLE", "FULL"):
        raise RuntimeError(f"unknown provenance {level!r}")
    return level


def _ensure_parent(path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def write_csv(path: str | os.PathLike, rows: Iterable, fieldnames: Sequence[str],
              *, mode: str, cfg: Mapping) -> Path:
    """Write a CSV with a mandatory leading ``provenance`` column.

    The stamp is applied here, keyed off ``mode``, so no caller can produce an
    unstamped sample artefact.  ``rows`` may be mappings or sequences matching
    ``fieldnames``.  Output is deterministic for identical input.
    """
    path = Path(path)
    _ensure_parent(path)
    stamp = provenance_stamp(cfg, mode)
    header = ["provenance"] + list(fieldnames)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            if isinstance(row, Mapping):
                payload = {k: row.get(k, "") for k in fieldnames}
            else:
                payload = dict(zip(fieldnames, row))
            payload["provenance"] = stamp
            writer.writerow(payload)
    os.replace(tmp, path)
    return path


def write_json(path: str | os.PathLike, obj: Mapping, *, mode: str,
               cfg: Mapping) -> Path:
    """Write deterministic JSON with a mandatory leading ``provenance`` key."""
    if not isinstance(obj, Mapping):
        raise TypeError("write_json expects a mapping so provenance can be added")
    path = Path(path)
    _ensure_parent(path)
    payload = {"provenance": provenance_stamp(cfg, mode)}
    payload.update({k: v for k, v in obj.items() if k != "provenance"})
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False, default=str)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def write_markdown(path: str | os.PathLike, text: str, *, mode: str,
                   cfg: Mapping) -> Path:
    """Write Markdown with a machine-readable provenance comment on line 1.

    Human-facing summaries must be stamped too; keeping this here means the
    caller cannot emit an unstamped sample document.
    """
    path = Path(path)
    _ensure_parent(path)
    stamp = provenance_stamp(cfg, mode)
    body = f"<!-- provenance: {stamp} -->\n" + text
    if not body.endswith("\n"):
        body += "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# Run log / timing
# --------------------------------------------------------------------------- #
RUN_LOG_FIELDS = ["stage", "mode", "provenance", "wall_s", "peak_rss_mb",
                  "when", "notes"]


def _current_rss_bytes() -> int:
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except Exception:
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


class _Stage:
    def __init__(self, cfg: Mapping, stage: str, mode: str):
        self.cfg, self.stage, self.mode = cfg, stage, mode
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(str(text))


@contextlib.contextmanager
def timed_stage(cfg: Mapping, stage: str, mode: str, note: str = ""):
    """Time a stage, sample peak RSS, and append to ``outputs/run_log.csv``."""
    paths = resolve_paths(cfg, mode)
    # `paths["run_log"]` is the configured file; `paths["outputs"]` is a dir.
    log_path = _abs(cfg, lc(cfg)["paths"]["run_log"])
    state = _Stage(cfg, stage, mode)
    if note:
        state.note(note)
    interval = lc(cfg)["runtime"].get("rss_sample_interval_s", 0.2)
    sampler = _RssSampler(interval)
    sampler.peak_bytes = _current_rss_bytes()
    started = time.perf_counter()
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sampler.start()
    error: BaseException | None = None
    try:
        yield state
    except BaseException as exc:  # record the failure, then re-raise
        error = exc
        state.note(f"FAILED: {type(exc).__name__}: {exc}")
        raise
    finally:
        sampler.stop()
        wall = time.perf_counter() - started
        peak_mb = sampler.peak_bytes / (1024 ** 2)
        _ensure_parent(log_path)
        new_file = not log_path.exists() or log_path.stat().st_size == 0
        with open(log_path, "a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=RUN_LOG_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow({
                "stage": stage, "mode": mode,
                "provenance": provenance(cfg, mode),
                "wall_s": f"{wall:.3f}", "peak_rss_mb": f"{peak_mb:.1f}",
                "when": when, "notes": "; ".join(state.notes),
            })


# --------------------------------------------------------------------------- #
# Determinism helpers
# --------------------------------------------------------------------------- #
def seed_everything(seed: int) -> int:
    """Seed ``random``, NumPy and the hash seed."""
    seed = int(seed)
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:  # pragma: no cover
        pass
    return seed


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str,
                      ensure_ascii=True)


def stable_hash(obj: Any) -> str:
    """SHA-256 of a canonical JSON rendering (sorted keys, stable separators)."""
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def config_fingerprint(cfg: Mapping, stage: str) -> str:
    """Fingerprint of the stage-relevant config (task block + seed)."""
    payload = {
        "stage": stage,
        "seed": cfg.get("seed"),
        "pipeline_version": cfg.get("pipeline_version"),
        "bdb27_lc": cfg.get("bdb27_lc"),
    }
    return stable_hash(payload)


# --------------------------------------------------------------------------- #
# Checkpoints
# --------------------------------------------------------------------------- #
def checkpoint_path(cfg: Mapping, stage: str, mode: str) -> Path:
    paths = resolve_paths(cfg, mode)
    fp = config_fingerprint(cfg, stage)[:16]
    return paths["checkpoints"] / f"{stage}__{mode}__{fp}.json"


def is_done(cfg: Mapping, stage: str, mode: str, force: bool = False) -> bool:
    """True when a matching, complete checkpoint exists and ``force`` is False."""
    if force:
        return False
    path = checkpoint_path(cfg, stage, mode)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(payload.get("done"))


def mark_done(cfg: Mapping, stage: str, mode: str,
              extra: Mapping | None = None) -> Path:
    """Write a deterministic checkpoint (sorted keys) for stage+mode+fingerprint."""
    path = checkpoint_path(cfg, stage, mode)
    payload = {
        "stage": stage,
        "mode": mode,
        "provenance": provenance(cfg, mode),
        "config_fingerprint": config_fingerprint(cfg, stage),
        "when_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "done": True,
        "extra": dict(extra or {}),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# Schema checking
# --------------------------------------------------------------------------- #
def _canonical_dtype(dtype: Any) -> str:
    raw = str(dtype).strip().lower()
    raw = raw.replace("dtype('", "").replace("')", "")
    for family, aliases in _ALIASES.items():
        if raw in aliases:
            return family
    for family, aliases in _ALIASES.items():
        if any(raw.startswith(a + "[") or raw.startswith(a + "<") for a in aliases):
            return family
    if "int" in raw:
        return "int"
    if "float" in raw or "double" in raw or "decimal" in raw:
        return "float"
    if "bool" in raw:
        return "bool"
    if "date" in raw and "time" in raw:
        return "timestamp"
    if "date" in raw:
        return "date"
    if "time" in raw:
        if "varchar" in raw or "object" in raw or "string" in raw:
            return "str"
        return "time"
    if "str" in raw or "object" in raw or "varchar" in raw or "utf8" in raw:
        return "str"
    return raw


def _actual_columns(obj: Any) -> tuple[list[str], dict[str, str]]:
    import pandas as pd  # local import; cheap relative to data work

    if isinstance(obj, pd.DataFrame):
        return list(obj.columns), {c: _canonical_dtype(obj[c].dtype) for c in obj.columns}
    # DuckDB relation: .columns + .types, no .dtypes
    if hasattr(obj, "types") and hasattr(obj, "columns") and not hasattr(obj, "dtypes"):
        cols = list(obj.columns)
        return cols, {c: _canonical_dtype(t) for c, t in zip(cols, obj.types)}
    # polars DataFrame: .columns + .dtypes
    if hasattr(obj, "dtypes") and hasattr(obj, "columns"):
        cols = list(obj.columns)
        return cols, {c: _canonical_dtype(d) for c, d in zip(cols, obj.dtypes)}
    if isinstance(obj, Mapping):
        cols = list(obj.keys())
        dtypes = {}
        for c in cols:
            vals = [v for v in obj[c] if v is not None]
            sample = vals[0] if vals else ""
            if isinstance(sample, bool):
                dtypes[c] = "bool"
            elif isinstance(sample, int):
                dtypes[c] = "int"
            elif isinstance(sample, float):
                dtypes[c] = "float"
            else:
                dtypes[c] = "str"
        return cols, dtypes
    raise TypeError(f"schema_check cannot inspect {type(obj)!r}")


def schema_check(df_or_table: Any, expected: Mapping[str, Any]) -> None:
    """Raise :class:`SchemaError` with a diff of missing/extra/dtype mismatches.

    ``expected`` is either ``{column: canonical_dtype}`` or a schema dict with a
    ``"columns"`` key (as in :data:`EXPECTED_SCHEMAS`).  Dtype families:
    int, float, str, bool, timestamp, date, time.
    """
    if "columns" in expected and isinstance(expected["columns"], Mapping):
        expected_cols = dict(expected["columns"])
    else:
        expected_cols = dict(expected)
    actual_cols, actual_types = _actual_columns(df_or_table)
    missing = [c for c in expected_cols if c not in actual_types]
    extra = [c for c in actual_cols if c not in expected_cols]
    mismatched = []
    for col, want in expected_cols.items():
        if col not in actual_types:
            continue
        want_family = _canonical_dtype(want)
        got = actual_types[col]
        if got != want_family:
            mismatched.append(f"{col}: expected {want_family} ({want!r}), got {got}")
    if missing or extra or mismatched:
        lines = ["SCHEMA MISMATCH"]
        if missing:
            lines.append(f"  missing columns ({len(missing)}): {missing}")
        if extra:
            lines.append(f"  unexpected columns ({len(extra)}): {extra}")
        for item in mismatched:
            lines.append(f"  dtype: {item}")
        raise SchemaError("\n".join(lines))
