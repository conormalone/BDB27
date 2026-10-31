"""dl_common — shared engine for the BDB27 DL task (combine ceiling x decline).

One code path for ``--mode sample|full``.  Every path, threshold, size and seed
comes from ``config.yaml`` under the ``bdb27_dl:`` block; this module holds no
design constants beyond dtype names and the provenance stamp.

This is a deliberate fork of :mod:`lc_common`: the pure helpers (schema
declarations, ``seed_everything``, ``stable_hash``) are imported from
``lc_common`` rather than duplicated, but *nothing* here reads ``bdb27_lc``.
``lc_common.lc()`` is never called, so an LC config change cannot alter a DL
checkpoint fingerprint (see :func:`config_fingerprint`).

Provenance contract
-------------------
* :func:`provenance` returns the *level*: ``PLACEHOLDER_SAMPLE`` or ``FULL``.
* :func:`write_csv` / :func:`write_json` / :func:`write_markdown` are the only
  supported writers for deliverables.  They stamp the provenance themselves,
  keyed off ``mode``, so no caller can emit an unmarked sample artefact.

Full-run gates
--------------
* :func:`require_prereg` refuses ``--mode full`` unless the prereg file exists
  and contains the token ``FROZEN``.
* :func:`require_full_tracking` refuses ``--mode full`` unless the tracking
  input parses to more rows than the provided sample row cap.
* :func:`require_full_provenance` refuses reportable numbers on non-FULL data.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # ``src`` is on sys.path in both supported invocation styles.
    import lc_common as _lc
except ImportError:  # pragma: no cover - direct script execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import lc_common as _lc

# --------------------------------------------------------------------------- #
# Re-exported pure helpers (single source of truth for schema declarations)
# --------------------------------------------------------------------------- #
SAMPLE_STAMP = _lc.SAMPLE_STAMP
EXPECTED_SCHEMAS = _lc.EXPECTED_SCHEMAS
SchemaError = _lc.SchemaError
schema_check = _lc.schema_check
seed_everything = _lc.seed_everything
stable_hash = _lc.stable_hash

#: Both chains append to the same configured run log, so the column set must be
#: identical or the shared CSV header would be corrupted.
RUN_LOG_FIELDS = _lc.RUN_LOG_FIELDS

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"

_MODES = ("sample", "full")
_TASK_KEY = "bdb27_dl"
_STRUCTURAL_KEYS = ("interim", "parquet", "checkpoints", "tmp")


# --------------------------------------------------------------------------- #
# Config / mode / paths
# --------------------------------------------------------------------------- #
def load_config(path: str | os.PathLike | None = None) -> dict:
    """Load ``config.yaml`` and assert the ``bdb27_dl`` block exists."""
    import yaml

    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict) or _TASK_KEY not in cfg:
        raise KeyError(f"{cfg_path} has no top-level {_TASK_KEY!r} block")
    cfg["_config_path"] = str(cfg_path)
    return cfg


def dl(cfg: Mapping) -> dict:
    """Return the active DL config block."""
    try:
        return cfg[_TASK_KEY]
    except KeyError as exc:
        raise KeyError(f"config has no {_TASK_KEY!r} block") from exc


def _check_mode(mode: str) -> str:
    if mode not in _MODES:
        raise ValueError(f"mode must be 'sample' or 'full', got {mode!r}")
    return mode


def parse_args(argv: Sequence[str] | None = None, *,
               with_stage: bool = False) -> argparse.Namespace:
    """Parse the common CLI: ``--mode sample|full``, ``--force``, ``--config``.

    With ``with_stage=True`` an optional positional ``stage`` name is also
    accepted (used by :mod:`dl.run`); the three documented attributes are
    always present.
    """
    parser = argparse.ArgumentParser(add_help=True)
    if with_stage:
        parser.add_argument("stage", nargs="?", default=None,
                            help="stage name, e.g. 01_audit")
    parser.add_argument("--mode", choices=_MODES, default=None,
                        help="sample (row-capped provided data) or full (final run)")
    parser.add_argument("--force", action="store_true",
                        help="ignore checkpoints / rebuild parquet")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    args = parser.parse_args(argv)
    if args.mode is None:
        args.mode = dl(load_config(args.config))["mode"]["default"]
    return args


def _abs(value: str | os.PathLike) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (REPO_ROOT / p)


def resolve_paths(cfg: Mapping, mode: str) -> dict[str, Path]:
    """Resolve configured paths to absolute ``Path`` objects.

    Only directories under ``data/interim`` are created here (interim, parquet,
    checkpoints, tmp).  Output directories are created lazily by the writers so
    nothing bulky can be created outside the interim volume.
    """
    _check_mode(mode)
    paths = {key: _abs(val) for key, val in dl(cfg)["paths"].items()}
    for key in _STRUCTURAL_KEYS:
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def source_paths(cfg: Mapping, key: str, mode: str | None = None) -> list[Path]:
    """Resolve a ``files:`` key to one or more raw source paths.

    A key whose value is a list (``game_tracking_full``) expands to that list.
    """
    files = dl(cfg)["files"]
    if key not in files:
        raise KeyError(f"unknown files key {key!r}; have {sorted(files)}")
    raw = _abs(dl(cfg)["paths"]["raw"])
    value = files[key]
    values = value if isinstance(value, list) else [value]
    return [(raw / v) if not Path(v).is_absolute() else Path(v) for v in values]


def tracking_sources(cfg: Mapping, mode: str) -> list[Path]:
    """Tracking file(s) for the given mode."""
    key = dl(cfg)["mode"][mode]["tracking_key"]
    return source_paths(cfg, key, mode)


# --------------------------------------------------------------------------- #
# Disk guard
# --------------------------------------------------------------------------- #
def assert_disk_space(cfg: Mapping, mode: str | None = None) -> dict[str, float]:
    """Fail loudly unless root and interim volumes have enough free space.

    The root filesystem and the interim (USB) volume are measured separately:
    ``shutil.disk_usage("/")`` describes the root fs, *not* the USB mount.
    Returns the measured free MB per volume.
    """
    rt = dl(cfg)["runtime"]
    paths = resolve_paths(cfg, mode or dl(cfg)["mode"]["default"])
    root_path = REPO_ROOT
    interim_path = Path(paths["interim"])
    if not interim_path.exists():
        interim_path = interim_path.parent
    root_free = shutil.disk_usage(root_path).free / (1024 ** 2)
    interim_free = shutil.disk_usage(interim_path).free / (1024 ** 2)
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
    """Open DuckDB single-threaded with the configured limit and temp directory.

    Also redirects ``TMPDIR``/``TMP``/``TEMP`` and ``tempfile.tempdir`` to the
    interim volume: the root tmpfs has roughly 100 MB free, so a DuckDB spill or
    a numpy temp file must never land there.
    """
    import duckdb

    rt = dl(cfg)["runtime"]
    tmp = _abs(rt["temp_directory"])
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


def _reader_sql(srcs: Sequence[Path], columns: Mapping[str, str] | None,
                null_marker: str) -> str:
    quoted = "[" + ", ".join(f"'{Path(s).as_posix()}'" for s in srcs) + "]"
    if len(srcs) == 1:
        base = f"read_csv_auto({quoted[1:-1]}, nullstr=['{null_marker}'])"
    else:
        base = (f"read_csv_auto({quoted}, nullstr=['{null_marker}'], "
                f"union_by_name=true)")
    select = ", ".join(f"{expr} AS {name}" for name, expr in columns.items())
    return f"(SELECT {select} FROM {base})"


def _declared_columns(cfg: Mapping, key: str) -> list[str]:
    """Explicit column list for a key whose schema is declared in lc_common."""
    declared = EXPECTED_SCHEMAS.get(key)
    if declared is None:
        raise ValueError(
            f"to_parquet: an explicit column list is required for key {key!r} "
            f"(no declared schema; pass columns=[...])")
    return list(declared["columns"])


def to_parquet(cfg: Mapping, key: str, out_name: str,
               columns: Sequence[str] | Mapping[str, str] | None = None,
               mode: str = "sample", force: bool = False,
               source: Sequence[Path] | Path | None = None) -> Path:
    """Convert raw CSV(s) to a column-pruned Parquet file under ``parquet``.

    Never copies a whole CSV: the column list is always explicit.  When
    ``columns`` is omitted the declared schema from
    :data:`lc_common.EXPECTED_SCHEMAS` supplies it.  A mapping form allows SQL
    expressions (``{"wp": "TRY_CAST(pre_snap_home_team_win_probability AS DOUBLE)"}``).

    Idempotent: skips when the parquet is newer than every source unless
    ``force``.
    """
    _check_mode(mode)
    paths = resolve_paths(cfg, mode)
    rt = dl(cfg)["runtime"]
    if columns is None:
        columns = _declared_columns(cfg, key)
    if isinstance(columns, Mapping):
        wanted = {str(name): str(expr) for name, expr in columns.items()}
    else:
        wanted = {str(name): str(name) for name in columns}
    if not wanted:
        raise ValueError("to_parquet: refusing to write a zero-column parquet")

    out = paths["parquet"] / (out_name if str(out_name).endswith(".parquet")
                              else f"{out_name}.parquet")

    if source is None:
        srcs = source_paths(cfg, key, mode)
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

    reader = _reader_sql(srcs, wanted, rt.get("csv_null_marker", "NA"))
    con = duckdb_connect(cfg)
    try:
        tmp_out = out.with_suffix(".parquet.tmp")
        con.execute(
            f"COPY (SELECT * FROM {reader}) TO '{tmp_out.as_posix()}' "
            f"(FORMAT PARQUET, COMPRESSION {rt['parquet_compression']}, "
            f"ROW_GROUP_SIZE {int(rt['parquet_row_group_size'])})")
        got = [row[0] for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{tmp_out.as_posix()}')").fetchall()]
        if got != list(wanted):
            raise RuntimeError(
                f"to_parquet({key!r}) wrote columns {got}, expected "
                f"{list(wanted)}")
        os.replace(tmp_out, out)
    finally:
        con.close()
    return out


# --------------------------------------------------------------------------- #
# Provenance / writers
# --------------------------------------------------------------------------- #
def provenance(cfg: Mapping, mode: str) -> str:
    """Provenance level: ``PLACEHOLDER_SAMPLE`` | ``FULL``."""
    _check_mode(mode)
    return dl(cfg)["mode"][mode]["provenance"]


def provenance_stamp(cfg: Mapping, mode: str) -> str:
    """Exact value written into artefacts."""
    level = provenance(cfg, mode)
    return f"{level}: {SAMPLE_STAMP}" if mode == "sample" else level


def require_full_provenance(cfg: Mapping, mode: str, stage: str) -> str:
    """Assert full-mode provenance is genuinely FULL; returns the level.

    In sample mode it returns ``PLACEHOLDER_SAMPLE`` without raising (sample
    runs are allowed, their outputs are stamped and non-reportable).
    """
    level = provenance(cfg, mode)
    if mode == "full" and level != "FULL":
        raise RuntimeError(
            f"STAGE {stage!r}: refusing to run — mode=full but provenance is "
            f"{level!r}, not 'FULL'. Check config {_TASK_KEY}.mode.full and the "
            f"full tracking input.")
    if level not in ("PLACEHOLDER_SAMPLE", "FULL"):
        raise RuntimeError(f"unknown provenance {level!r}")
    return level


def require_prereg(cfg: Mapping, mode: str, stage: str) -> Path:
    """Gate full-mode runs on a frozen pre-registration.

    Full mode: the configured prereg file must exist *and* contain the token
    ``FROZEN``.  Sample mode: returns the path without raising (sample work is
    explicitly allowed before the prereg is written).
    """
    path = _abs(dl(cfg)["paths"]["prereg"])
    if mode != "full":
        return path
    if not path.exists():
        raise RuntimeError(
            f"STAGE {stage!r}: refusing to run — mode=full requires the "
            f"pre-registration file {path}, which does not exist. Write it "
            f"(see TASK.md) before the first full-data run.")
    text = path.read_text(encoding="utf-8")
    if "FROZEN" not in text:
        raise RuntimeError(
            f"STAGE {stage!r}: refusing to run — {path} exists but does not "
            f"contain the token 'FROZEN'. An unfrozen prereg cannot gate a "
            f"confirmatory full run.")
    return path


def _ensure_parent(path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def write_csv(path: str | os.PathLike, rows: Iterable, fieldnames: Sequence[str],
              *, mode: str, cfg: Mapping) -> Path:
    """Write a CSV with a mandatory leading ``provenance`` column.

    The stamp is applied here, keyed off ``mode``, so no caller can produce an
    unstamped sample artefact.  Output is deterministic for identical input.
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
    """Write Markdown with a machine-readable provenance comment on line 1."""
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
def _current_rss_bytes() -> int:  # thin wrapper, keeps the sampler importable
    return _lc._current_rss_bytes()


class _Stage:
    def __init__(self, cfg: Mapping, stage: str, mode: str):
        self.cfg, self.stage, self.mode = cfg, stage, mode
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(str(text))


@contextlib.contextmanager
def timed_stage(cfg: Mapping, stage: str, mode: str, note: str = ""):
    """Time a stage, sample peak RSS, and append to the configured run log."""
    _check_mode(mode)
    log_path = _abs(dl(cfg)["paths"]["run_log"])
    state = _Stage(cfg, stage, mode)
    if note:
        state.note(note)
    interval = dl(cfg)["runtime"].get("rss_sample_interval_s", 0.2)
    sampler = _lc._RssSampler(interval)
    sampler.peak_bytes = _current_rss_bytes()
    started = time.perf_counter()
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sampler.start()
    try:
        yield state
    except BaseException as exc:  # record the failure, then re-raise
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
# Fingerprint / checkpoints
# --------------------------------------------------------------------------- #
def config_fingerprint(cfg: Mapping, stage: str) -> str:
    """Fingerprint of the DL stage config: seed + ``bdb27_dl`` + stage only.

    ``bdb27_lc`` and ``pipeline_version`` are deliberately excluded: a change
    to the LC chain must not invalidate a DL checkpoint (and vice versa).
    """
    payload = {
        "stage": stage,
        "seed": cfg.get("seed"),
        _TASK_KEY: cfg.get(_TASK_KEY),
    }
    return stable_hash(payload)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# Full-run evidence gate
# --------------------------------------------------------------------------- #
def tracking_row_count(cfg: Mapping, mode: str) -> int:
    """Parsed row count of the tracking file(s) for ``mode``."""
    files = tracking_sources(cfg, mode)
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"tracking input missing: {missing}")
    null = dl(cfg)["runtime"].get("csv_null_marker", "NA")
    quoted = "[" + ", ".join(f"'{f.as_posix()}'" for f in files) + "]"
    if len(files) == 1:
        reader = f"read_csv_auto({quoted[1:-1]}, nullstr=['{null}'])"
    else:
        reader = (f"read_csv_auto({quoted}, nullstr=['{null}'], "
                  f"union_by_name=true)")
    con = duckdb_connect(cfg)
    try:
        return int(con.execute(f"SELECT count(*) FROM {reader}").fetchone()[0])
    finally:
        con.close()


def require_full_tracking(cfg: Mapping, mode: str = "full") -> dict[str, Any]:
    """Refuse a full run whose tracking input is only sample-sized.

    Mirrors the LC evidence gate: the parsed row count must strictly exceed
    ``mode.sample.sample_row_cap``, otherwise ``FULL`` provenance cannot be
    asserted for this input.
    """
    files = tracking_sources(cfg, mode)
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise RuntimeError(
            "FULL MODE REFUSED: tracking input is missing:\n  " +
            "\n  ".join(missing) +
            f"\n  configured under {_TASK_KEY}.files."
            f"{dl(cfg)['mode'][mode]['tracking_key']}")
    rows = tracking_row_count(cfg, mode)
    cap = int(dl(cfg)["mode"]["sample"]["sample_row_cap"])
    if rows <= cap:
        raise RuntimeError(
            f"FULL MODE REFUSED: parsed tracking row count is {rows:,}, which "
            f"does not exceed the provided sample row cap of {cap:,}. This "
            f"input is sample-sized, so FULL provenance cannot be asserted.")
    return {"files": [str(f) for f in files], "rows": rows, "sample_row_cap": cap}
