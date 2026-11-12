"""Render figure 1 from the uploaded full-data turn table (D185).

The full run was executed in ``notebooks/bdb27_full_game_turns.ipynb`` on the
three per-season tracking files. It produced a **game-only, DB-only** turn
table plus its own band table and manifest. Figure 1's other half — the Combine
turns — is unchanged data: ``config.yaml``'s ``sample_files`` block overrides
only ``game_tracking``, so the sample run already read the complete Combine
files. This module joins the two, verifies that the join reproduces the full
run's own band table, and draws figure 1 through the existing stage-06 code.

Nothing here draws or decides a figure: ``figures.band_table``,
``figures.build_plot`` and ``figures.render`` are called unchanged. What this
module adds is provenance — pinned sha256 checks on the uploaded files, a
manifest identity check, a band-table cross-check, the per-season coverage
record, and a rendered-text dump.

Refusals are deliberate: a checksum mismatch, a row-count disagreement, a band
that does not match the uploaded band table, or a `config_sha` / detection-code
drift stops the render instead of drawing a number nobody can trace.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import pandas as pd

from bdb27 import common, figures
from bdb27.figure_text_scan import BANNED_TOKENS, figure_texts

STAGE = "07_full_figure1"
FIGURE_KEY = figures.FIGURE_KEY

#: The uploaded run, relative to the repo root.
ARTEFACT_DIR = Path("data/interim/full_run_20261111")
ARTEFACT_PARQUET = "bdb27_full_db_turns.parquet"
ARTEFACT_BANDS = "bdb27_full_game_bands.csv"
ARTEFACT_MANIFEST = "bdb27_full_game_manifest.json"

#: Pins from the Windows run's own manifest and from Main's verification.
PINNED = {
    ARTEFACT_PARQUET: {
        "bytes": 9_773_407,
        "sha256": "f2e7cd01db214b7d88ab3f029876fe0fe7ced1530d2f014b9ad55b1b2dfb8e04",
        "role": "game-only, DB-only turn table (2023-2025)",
    },
    ARTEFACT_BANDS: {
        "bytes": 4_249,
        "sha256": "7cd10b9db2b9ccae21099c2bab8fe5d5f1420e295aec6e9dc09ed2c6ade454a1",
        "role": "the notebook's own band table, all positions, both settings",
    },
    ARTEFACT_MANIFEST: {
        "bytes": 35_281,
        "sha256": "dc8888a0e98d0d67a165106c29638fbe75f6b29d8dbce9a77e87c3dcc3c9de73",
        "role": "run manifest: inputs, counts, chunks, artefact checksums",
    },
}

#: Files that define turn detection. If any differs from the run manifest's
#: ``git_rev``, the uploaded turns were produced by different code and may not
#: be drawn as this config's output.
#:
#: ``src/bdb27/figures.py`` is deliberately NOT in this list. It draws and
#: aggregates; it does not detect a turn. Its band assignment is proved instead
#: by the row-for-row cross-check against the upload's own band table
#: (:func:`check_band_csv`), which a drawing change could not pass if it moved
#: a count or a band edge. Keeping the renderer here would make any figure
#: change look like detection drift — the check exists to catch the latter.
DETECTION_FILES = [
    "src/bdb27/common.py",
    "src/bdb27/turns.py",
    "src/bdb27/kinematics.py",
    "src/bdb27/geometry.py",
    "src/bdb27/envelope.py",
    "src/02_turns.py",
]

COMBINE_TURNS = Path("outputs/turns/turns.parquet")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel(path: Path, root: Path) -> str:
    try:
        # Do not resolve: ``data/interim`` is a symlink out of the repo, and a
        # provenance record must name the path an operator can open here.
        return str(Path(path).relative_to(Path(root)))
    except ValueError:
        return str(path)


def _visible_strings(fig) -> list[str]:
    """Every string actually drawn on the canvas, in draw order.

    ``bdb27.figure_text_scan.figure_texts`` is the hedging scanner and
    deliberately reads every text object, visible or not. The proof dump is a
    different artefact: it must show what a reader sees, so hidden duplicate
    tick labels (one set per facet panel) are excluded.
    """
    from matplotlib.text import Text

    return [text.get_text() for text in fig.findobj(Text)
            if text.get_text().strip() and text.get_visible()]


def verify_sources(source_dir: Path, root: Path,
                   pins: dict | None = None) -> list[dict]:
    """Hash every uploaded file and refuse unless it matches its pin."""
    pins = pins or PINNED
    records: list[dict] = []
    problems: list[str] = []
    for name, pin in pins.items():
        path = Path(source_dir) / name
        if not path.exists():
            problems.append(f"{name} is absent ({path})")
            continue
        size = path.stat().st_size
        digest = sha256_file(path)
        if size != pin["bytes"]:
            problems.append(f"{name}: {size} bytes, expected {pin['bytes']}")
        if digest != pin["sha256"]:
            problems.append(
                f"{name}: sha256 {digest}, expected {pin['sha256']}")
        records.append({
            "name": name,
            "path": _rel(path, root),
            "bytes": size,
            "sha256": digest,
            "role": pin.get("role", ""),
        })
    if problems:
        raise common.StageRefused(
            f"{STAGE}: the uploaded full-run artefacts do not match their pins — "
            + "; ".join(problems))
    return records


def load_run_manifest(source_dir: Path) -> dict:
    path = Path(source_dir) / ARTEFACT_MANIFEST
    return json.loads(path.read_text(encoding="utf-8"))


def verify_run_identity(manifest: dict, cfg: common.Config,
                        root: Path) -> dict:
    """Config fingerprint and detection code must match the run's own record."""
    expected = str(manifest.get("config_sha", ""))
    actual = common.config_fingerprint(cfg)
    if expected != actual:
        raise common.StageRefused(
            f"{STAGE}: config_sha is {actual} but the run manifest says "
            f"{expected!r} — the thresholds or config have changed since the run")

    rev = str(manifest.get("git_rev", ""))
    if not rev:
        raise common.StageRefused(
            f"{STAGE}: the run manifest carries no git_rev; cannot prove the "
            "detection code is unchanged")
    try:
        diff = subprocess.run(
            ["git", "diff", "--quiet", rev, "--", *DETECTION_FILES],
            cwd=str(root), capture_output=True, text=True)
    except Exception as exc:  # pragma: no cover - only outside a git checkout
        raise common.StageRefused(
            f"{STAGE}: cannot check detection-code identity against {rev}: {exc}")
    if diff.returncode == 1:
        raise common.StageRefused(
            f"{STAGE}: turn-detection sources differ from the run's git_rev "
            f"{rev}; refusing to draw turns produced by other code")
    if diff.returncode != 0:
        raise common.StageRefused(
            f"{STAGE}: git could not compare {rev} ({diff.stderr.strip()})")
    return {
        "run_git_rev": rev,
        "render_git_rev": common.git_rev(root),
        "config_sha": actual,
        "detection_files_unchanged_since_run": True,
        "detection_files": list(DETECTION_FILES),
    }


def load_game_turns(source_dir: Path) -> pd.DataFrame:
    """Read the game-only, DB-only full turn table and check the manifest's counts."""
    path = Path(source_dir) / ARTEFACT_PARQUET
    columns = list(figures.TURN_COLUMNS) + ["season"]
    df = pd.read_parquet(path, columns=columns)
    settings = set(df["setting"].dropna().astype(str).unique())
    positions = set(df["position_group"].dropna().astype(str).unique())
    if settings != {"game"} or positions != {"DB"}:
        raise common.StageRefused(
            f"{STAGE}: expected game-only, DB-only turns; got settings "
            f"{sorted(settings)} and positions {sorted(positions)}")
    return df


def load_combine_turns(path: Path) -> pd.DataFrame:
    """The Combine DB turns, which the sample run already read in full."""
    path = Path(path)
    if not path.exists():
        raise common.StageRefused(
            f"{STAGE}: the turn table holding the Combine side is absent ({path})")
    df = pd.read_parquet(path, columns=figures.TURN_COLUMNS)
    combine = df[(df["setting"] == "combine")
                 & (df["position_group"] == "DB")].copy()
    if combine.empty:
        raise common.StageRefused(
            f"{STAGE}: no valid Combine DB turns in {path}")
    combine["season"] = pd.NA
    return combine


def assemble_turns(game: pd.DataFrame, combine: pd.DataFrame) -> pd.DataFrame:
    """The figure's input: full game DB turns + the unchanged Combine DB turns."""
    return pd.concat([game, combine], ignore_index=True)


def check_manifest_counts(manifest: dict, game: pd.DataFrame) -> dict:
    """The parquet must carry exactly the counts the run manifest publishes."""
    counts = manifest.get("counts", {})
    valid = game["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(game["abs_turn_angle"], errors="coerce")
    got = {
        "turn_rows": int(len(game)),
        "valid_db_turns": int(valid.sum()),
        "valid_db_players": int(game.loc[valid, "nfl_id"].nunique()),
        "valid_db_player_games": int(
            game.loc[valid].groupby(["nfl_id", "game_id"]).ngroups),
        "valid_db_30_60": int((valid & (angle >= 30) & (angle < 60)).sum()),
        "by_season": {
            str(int(season)): int(n)
            for season, n in game.loc[valid].groupby("season").size().items()
        },
    }
    expected = {
        "turn_rows": counts.get("turn_rows"),
        "valid_db_turns": counts.get("valid_db_turns"),
        "valid_db_players": counts.get("valid_db_players"),
        "valid_db_player_games": counts.get("valid_db_player_games"),
        "valid_db_30_60": counts.get("valid_db_30_60"),
        "by_season": counts.get("by_season", {}).get("valid_db_turns"),
    }
    mismatches = {
        key: (got[key], expected[key])
        for key in ("valid_db_turns", "valid_db_players",
                    "valid_db_player_games", "valid_db_30_60")
        if expected[key] is not None and got[key] != int(expected[key])
    }
    if expected["by_season"] is not None:
        want = {str(k): int(v) for k, v in expected["by_season"].items()}
        if got["by_season"] != want:
            mismatches["by_season"] = (got["by_season"], want)
    if mismatches:
        raise common.StageRefused(
            f"{STAGE}: the turn table disagrees with the run manifest on "
            + "; ".join(f"{k}: table={v[0]!r} manifest={v[1]!r}"
                        for k, v in mismatches.items()))
    return got


def check_band_csv(path: Path, table: pd.DataFrame, context: dict) -> dict:
    """The assembled DB table must reproduce the run's own band table exactly."""
    uploaded = pd.read_csv(path)
    db = uploaded[uploaded["position_group"] == "DB"]
    if db.empty:
        raise common.StageRefused(
            f"{STAGE}: the uploaded band table has no DB rows ({path})")
    checked = 0
    for _, row in table.iterrows():
        match = db[(db["setting"] == row["setting"])
                   & (db["band"] == row["band"])]
        if len(match) != 1:
            raise common.StageRefused(
                f"{STAGE}: uploaded band table has {len(match)} rows for "
                f"{row['setting']} {row['band']}, expected exactly one")
        other = match.iloc[0]
        if int(other["count"]) != int(row["count"]):
            raise common.StageRefused(
                f"{STAGE}: {row['setting']} {row['band']} count is "
                f"{int(row['count'])} here but {int(other['count'])} in the "
                "uploaded band table")
        if not math.isclose(float(other["share_within_setting"]),
                            float(row["share_within_setting"]),
                            rel_tol=0, abs_tol=1e-12):
            raise common.StageRefused(
                f"{STAGE}: {row['setting']} {row['band']} share differs from "
                "the uploaded band table")
        if int(other["setting_total"]) != int(row["setting_total"]):
            raise common.StageRefused(
                f"{STAGE}: {row['setting']} setting_total differs from the "
                "uploaded band table")
        checked += 1
    if checked != len(table):
        raise common.StageRefused(
            f"{STAGE}: cross-checked {checked} rows, expected {len(table)}")
    return {"rows_checked": checked, "matches_uploaded_band_table": True}


def season_coverage(game: pd.DataFrame, manifest: dict) -> dict:
    """Valid DB turns, players and games per season, plus the run's game turns."""
    valid = game["valid"].fillna(False).astype(bool)
    frame = game.loc[valid]
    run_counts = manifest.get("counts", {}).get("by_season", {})
    game_turns = run_counts.get("game_turns", {})
    by_season: dict[str, dict] = {}
    for season, part in frame.groupby("season"):
        key = str(int(season))
        by_season[key] = {
            "valid_db_turns": int(len(part)),
            "players": int(part["nfl_id"].nunique()),
            "games": int(part["game_id"].nunique()),
            "game_turns": int(game_turns.get(key, 0)),
        }
    for key, value in game_turns.items():
        by_season.setdefault(str(key), {})["game_turns"] = int(value)
    ordered = {key: by_season[key] for key in sorted(by_season)}
    return {
        "pooled": True,
        "by_season": ordered,
        "note": (
            "Figure 1 pools 2023-2025. Valid DB game turns are 8,016 / 21,275 / "
            "29,373 and whole-file game turns 25,019 / 53,511 / 74,917. The 2023 "
            "tracking file is about a third the size of the 2025 file for the "
            "same number of games (333 vs 334), so season-level rates are not "
            "comparable. The pooled figure is reported; the unevenness is an "
            "open question, not resolved here."),
    }


def _json_clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_clean(v) for v in value]
    return value


def _write_json(document: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_clean(document), indent=2, default=str,
                   allow_nan=False),
        encoding="utf-8")


def merge_manifest(path: Path, payload: dict, cfg: common.Config) -> dict:
    """Insert this figure's entry without dropping the other stage-06 figures."""
    path = Path(path)
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
    else:
        document = {"stage": figures.STAGE, "artefact_version": 1,
                    "figures": {}}
    document.setdefault("figures", {})[payload["figure"]] = payload
    entries = list(document["figures"].values())
    stamps = [str(entry.get("stamp", "")) for entry in entries]
    full = sum(1 for s in stamps if s == figures.stamp_text("full"))
    sample = sum(1 for s in stamps if s == figures.stamp_text("sample"))
    schematic = sum(1 for entry in entries
                    if entry.get("status") == "SCHEMATIC")
    other = len(stamps) - full - sample - schematic
    if full == len(stamps) and stamps:
        provenance, label = "FULL", "FULL DATA OUTPUT"
    elif sample == len(stamps) and stamps:
        provenance, label = common.PROVENANCE_SAMPLE, common.SAMPLE_LABEL
    else:
        provenance = "MIXED"
        label = (f"MIXED PROVENANCE — {full} FULL, {sample} sample"
                 + (f", {schematic} schematic" if schematic else "")
                 + (f", {other} other" if other else ""))
    document.update({
        "provenance": provenance,
        "label": label,
        "stamp": label,
        "config_sha": common.config_fingerprint(cfg),
        "generated_utc": common.utc_now(),
        "stage": figures.STAGE,
        "artefact_version": figures.ARTEFACT_VERSION,
    })
    _write_json(document, path)
    return document


def render_figure(cfg: common.Config, turns: pd.DataFrame, *,
                  source_records: list[dict], manifest_doc: dict,
                  identity: dict, crosscheck: dict | None = None,
                  out_dir: Path | None = None) -> dict:
    """Draw figure 1 from ``turns`` and write every full-provenance artefact."""
    edges = [float(x) for x in cfg.get("figures.hole.plot_bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    position = str(cfg.get("figures.hole.position_group"))
    table, context = figures.band_table(turns, edges, overflow, position)
    coverage = season_coverage(turns[turns["setting"] == "game"],
                               manifest_doc)

    outdir = Path(out_dir) if out_dir else cfg.repo_path("figures")
    png_path = outdir / f"{FIGURE_KEY}.png"
    csv_path = outdir / f"{FIGURE_KEY}.csv"
    manifest_path = outdir / "figures_manifest.json"
    provenance_path = outdir / f"{FIGURE_KEY}.provenance.json"
    text_path = outdir / f"{FIGURE_KEY}.drawn_text.json"

    common.write_csv(figures.csv_frame(table), csv_path, "full", cfg,
                     extra={"stamp": figures.stamp_text("full"),
                            "figure": FIGURE_KEY})
    plot = figures.build_plot(table, context, cfg)
    fig = figures.render(plot, cfg, png_path)
    texts = figure_texts(fig)

    banned = [hit for text in texts for token in BANNED_TOKENS
              if token in text.upper() for hit in [(token, text)]]
    if banned:
        raise common.StageRefused(
            f"{STAGE}: drawn figure carries provenance hedging: {banned}")

    band0 = table[table["band"] == context["bands"][0]]
    combine = band0[band0["setting"] == "combine"].iloc[0]
    game = band0[band0["setting"] == "game"].iloc[0]
    ratio = float(game["share_within_setting"]) / float(
        combine["share_within_setting"])
    title = str(plot.labels.get("title", ""))
    if title not in [t for t in texts if t]:
        raise common.StageRefused(
            f"{STAGE}: the rendered heading is not in the drawn text dump")

    payload = figures.manifest_payload(table, context)
    payload["provenance"] = "FULL"
    payload["stamp"] = figures.stamp_text("full")
    payload["sources"] = ([record["path"] for record in source_records]
                          + [f"{COMBINE_TURNS.as_posix()}#"
                             "setting=combine&position_group=DB"])
    payload["source_artefacts"] = source_records
    payload["source_run"] = {
        "manifest": ARTEFACT_MANIFEST,
        "git_rev": identity["run_git_rev"],
        "config_sha": identity["config_sha"],
        "runtime_seconds": manifest_doc.get("runtime_seconds"),
    }
    payload["season_coverage"] = coverage
    # The shared payload writes counts with ``str(int)``; at seven thousand
    # turns a thousands separator is the readable form. Only this entry's
    # claim is rewritten — the sample path keeps its own wording.
    payload["claim"] = payload["claim"].replace(
        f"{int(game['count'])} game turns",
        f"{int(game['count']):,} game turns").replace(
        f"vs {int(combine['count'])} Combine",
        f"vs {int(combine['count']):,} Combine")
    payload["caveats"] = list(payload.get("caveats", [])) + [
        coverage["note"],
        ("Game side: uploaded full run (2023-2025), game-only and DB-only. "
         "Combine side: the complete Combine file, unchanged by this work; "
         "sample mode overrides only game_tracking."),
    ]
    payload["rendered_text"] = text_path.as_posix()
    document = merge_manifest(manifest_path, payload, cfg)

    record = {
        "figure": FIGURE_KEY,
        "title": payload["title"],
        "claim": payload["claim"],
        "source_artefacts": source_records,
        "source_run": payload["source_run"],
        "identity": identity,
        "crosscheck": crosscheck,
        "season_coverage": coverage,
        "counts": {
            "combine_30_60": int(combine["count"]),
            "combine_total": int(combine["setting_total"]),
            "combine_share": float(combine["share_within_setting"]),
            "game_30_60": int(game["count"]),
            "game_total": int(game["setting_total"]),
            "game_share": float(game["share_within_setting"]),
            "ratio": ratio,
            "players_by_setting": context["players_by_setting"],
            "player_games": context["player_games"],
        },
        "manifest_provenance": document.get("provenance"),
        "outputs": {"png": png_path.as_posix(),
                    "csv": csv_path.as_posix(),
                    "manifest": manifest_path.as_posix(),
                    "drawn_text": text_path.as_posix()},
    }
    common.write_json(record, provenance_path, "full", cfg)
    visible = _visible_strings(fig)
    common.write_json({
        "figure": FIGURE_KEY,
        "stamp": figures.stamp_text("full"),
        "strings": visible,
        "scan_strings": texts,
        "banned_tokens": list(BANNED_TOKENS),
        "banned_hits": [],
    }, text_path, "full", cfg)
    return {
        "table": table,
        "context": context,
        "title": title,
        "ratio": ratio,
        "combine": combine,
        "game": game,
        "png": png_path,
        "csv": csv_path,
        "manifest": manifest_path,
        "provenance": provenance_path,
        "drawn_text": text_path,
        "texts": texts,
    }


def run(cfg: common.Config, *, source_dir: Path | None = None,
        combine_turns: Path | None = None, root: Path | None = None,
        pins: dict | None = None, out_dir: Path | None = None) -> dict:
    """Verify the upload, assemble the table, and render figure 1."""
    root = Path(root or Path(__file__).resolve().parents[2])
    source_dir = Path(source_dir or root / ARTEFACT_DIR)
    combine_turns = Path(combine_turns or root / COMBINE_TURNS)

    records = verify_sources(source_dir, root, pins=pins)
    manifest_doc = load_run_manifest(source_dir)
    identity = verify_run_identity(manifest_doc, cfg, root)

    game = load_game_turns(source_dir)
    check_manifest_counts(manifest_doc, game)
    combine = load_combine_turns(combine_turns)

    # The Combine side is full data, and the upload agrees with it exactly on
    # the Combine band counts. Check that before the join, so a Combine drift
    # is named rather than folded into a "game" discrepancy.
    combine_valid = combine["valid"].fillna(False).astype(bool)
    combine_angle = pd.to_numeric(combine["abs_turn_angle"], errors="coerce")
    if int(combine_valid.sum()) != 1179:
        raise common.StageRefused(
            f"{STAGE}: Combine DB turns total {int(combine_valid.sum())}, "
            "expected 1,179")
    if int(((combine_angle >= 30) & (combine_angle < 60)).sum()) != 26:
        raise common.StageRefused(
            f"{STAGE}: Combine DB turns in 30-60 degrees do not total 26")

    turns = assemble_turns(game, combine)
    edges = [float(x) for x in cfg.get("figures.hole.plot_bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    position = str(cfg.get("figures.hole.position_group"))
    table, context = figures.band_table(turns, edges, overflow, position)
    crosscheck = check_band_csv(source_dir / ARTEFACT_BANDS, table, context)

    result = render_figure(cfg, turns, source_records=records,
                           manifest_doc=manifest_doc, identity=identity,
                           crosscheck=crosscheck, out_dir=out_dir)
    result["crosscheck"] = crosscheck
    return result
