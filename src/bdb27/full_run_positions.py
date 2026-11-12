"""Render figure 5 from the uploaded full-data band table (2026-11-11).

Figure 5 is the cross-position exhibit: the same 30–60° shares as figure 1,
one panel per position group. D185 recorded it as blocked because the uploaded
turn table is DB-only. That was wrong for *this* figure: the upload also
contains ``bdb27_full_game_bands.csv``, the notebook's own band table, which
carries every position group × both settings as counts and shares. Nothing
this figure draws needs a per-turn row.

Two elements do need turn-level data and are therefore not invented here:

* player exposure (``n_players`` / ``players_by_setting``) — the uploaded band
  table does not carry it for the non-DB game positions, so those cells are
  left blank;
* snap-type composition — not drawn by figure 5 at all.

Everything else — counts, denominators, shares — is read from the uploaded
band table and re-checked against the Combine turn table and, for DB, against
the figure-1 path's assembled table.

The drawing contract is D183's trimmed figure 5: heading, bars, band rows,
counts and legend. No subtitle, caption, band callout or EXPLORATORY badge.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from bdb27 import common, figures, full_run
from bdb27.figure_text_scan import BANNED_TOKENS, figure_texts

STAGE = "08_full_figure5"
FIGURE_KEY = figures.FIGURE_KEY_ALL_POSITIONS

#: The six groups figure 5 draws, in TASK order, with OTHER appended. OTHER is
#: unclassified and exists only on the game side; its panel is drawn game-only
#: and is excluded from the "Combine takes a smaller share" claim by name.
POSITIONS = ["DB", "WR", "TE", "OL", "DL", "OTHER"]
#: Groups with no Combine turns at all in this run.
GAME_ONLY = ["OTHER"]

ARTEFACT_BANDS = full_run.ARTEFACT_BANDS
ARTEFACT_PARQUET = full_run.ARTEFACT_PARQUET
ARTEFACT_MANIFEST = full_run.ARTEFACT_MANIFEST
COMBINE_TURNS = full_run.COMBINE_TURNS
def _count_bands(turns: pd.DataFrame, edges: list[float], overflow: str,
                 position: str, setting: str) -> tuple[dict[str, int], int]:
    """Count valid turns per display band with figure 1's left-closed rule."""
    valid = turns["valid"].fillna(False).astype(bool)
    part = turns[valid & (turns["position_group"] == position)
                 & (turns["setting"] == setting)]
    angle = pd.to_numeric(part["abs_turn_angle"], errors="coerce")
    bands = figures.band_labels(edges, overflow)
    counts: dict[str, int] = {}
    for i, band in enumerate(bands):
        lo = float(edges[i])
        hi = float(edges[i + 1]) if i < len(edges) - 1 else None
        mask = angle >= lo
        if hi is not None:
            mask = mask & (angle < hi)
        counts[band] = int(mask.sum())
    if sum(counts.values()) != len(part):
        raise common.StageRefused(
            f"{STAGE}: {len(part)} valid {position} {setting} turns do not fall "
            f"inside the display bands {bands}")
    return counts, int(len(part))


def load_band_table(source_dir: Path) -> pd.DataFrame:
    path = Path(source_dir) / ARTEFACT_BANDS
    table = pd.read_csv(path)
    needed = {"setting", "position_group", "band", "band_index",
              "band_low_deg", "band_high_deg", "count", "setting_total",
              "share_within_setting"}
    missing = needed - set(table.columns)
    if missing:
        raise common.StageRefused(
            f"{STAGE}: {ARTEFACT_BANDS} is missing columns: {sorted(missing)}")
    return table


def positions_order(uploaded: pd.DataFrame, cfg: common.Config,
                    required: list[str] | None = None) -> list[str]:
    """Required positions first, then any extra group the upload carries.

    ``required`` defaults to the configured positions. Figure 5 passes the
    six-group constant because OTHER cannot live in ``config.yaml``: the config
    fingerprint is pinned by the uploaded run, so the sixth panel is a code
    requirement, and a missing one must refuse rather than quietly vanish.
    """
    required = ([str(p) for p in required]
                if required is not None
                else [str(p) for p in cfg.get("figures.all_positions.positions")])
    present = list(dict.fromkeys(uploaded["position_group"].astype(str)))
    order = required + [p for p in present if p not in required]
    unknown = [p for p in order if p not in present]
    if unknown:
        raise common.StageRefused(
            f"{STAGE}: the uploaded band table has no rows for {unknown}; "
            "figure 5 refuses to draw an empty panel")
    return order


def table_from_bands(uploaded: pd.DataFrame, positions: list[str],
                     edges: list[float], overflow: str) -> pd.DataFrame:
    """The uploaded band table in ``figures.band_table``'s long schema."""
    bands = figures.band_labels(edges, overflow)
    got_bands = {str(b) for b in uploaded["band"].astype(str)}
    if got_bands != set(bands):
        raise common.StageRefused(
            f"{STAGE}: uploaded bands {sorted(got_bands)} do not match the "
            f"figure's display bands {bands}")

    rows: list[dict] = []
    for position in positions:
        for i, band in enumerate(bands):
            part = uploaded[(uploaded["position_group"].astype(str) == position)
                            & (uploaded["band"].astype(str) == band)]
            if len(part) == 0:
                raise common.StageRefused(
                    f"{STAGE}: no uploaded row for {position} {band}")
            settings = set(part["setting"].astype(str))
            extra = settings - {"combine", "game"}
            if extra:
                raise common.StageRefused(
                    f"{STAGE}: unknown setting {sorted(extra)} for "
                    f"{position} {band}")
            if "game" not in settings:
                raise common.StageRefused(
                    f"{STAGE}: no game rows for {position} {band}")
            if "combine" not in settings and position not in GAME_ONLY:
                raise common.StageRefused(
                    f"{STAGE}: no Combine rows for {position} {band}; only "
                    f"{GAME_ONLY} may be game-only")
            for setting in ("combine", "game"):
                if setting not in settings:
                    continue
                match = part[part["setting"].astype(str) == setting]
                if len(match) != 1:
                    raise common.StageRefused(
                        f"{STAGE}: {len(match)} uploaded rows for {position} "
                        f"{band} {setting}, expected exactly one")
                row = match.iloc[0]
                count = int(row["count"])
                total = int(row["setting_total"])
                share = float(row["share_within_setting"])
                if total <= 0:
                    raise common.StageRefused(
                        f"{STAGE}: {position} {band} {setting} has a zero "
                        "setting_total")
                if count / total != share and not math.isclose(
                        count / total, share, rel_tol=0, abs_tol=1e-12):
                    raise common.StageRefused(
                        f"{STAGE}: {position} {band} {setting} share {share} "
                        f"does not equal {count}/{total}")
                rows.append({
                    "position": position,
                    "band_index": i + 1,
                    "band": band,
                    "band_low_deg": float(edges[i]),
                    "band_high_deg": (float(edges[i + 1])
                                      if i < len(edges) - 1 else None),
                    "setting": setting,
                    "count": count,
                    "setting_total": total,
                    "share_within_setting": count / total,
                })

    table = pd.DataFrame(rows)

    # Per position and setting, the band counts must reconstruct the uploaded
    # denominator, and every row must carry the same denominator.
    for (position, setting), part in table.groupby(["position", "setting"],
                                                   sort=False):
        totals = set(int(x) for x in part["setting_total"])
        if len(totals) != 1:
            raise common.StageRefused(
                f"{STAGE}: {position} {setting} carries several "
                f"setting_total values: {sorted(totals)}")
        total = totals.pop()
        if int(part["count"].sum()) != total:
            raise common.StageRefused(
                f"{STAGE}: {position} {setting} counts sum to "
                f"{int(part['count'].sum())}, uploaded total is {total}")
    return table


def check_combine_side(table: pd.DataFrame, combine_turns: pd.DataFrame,
                       edges: list[float], overflow: str,
                       positions: list[str]) -> dict:
    """The uploaded Combine rows must equal the Combine turn table exactly."""
    checked = 0
    for position in positions:
        rows = table[(table["position"] == position)
                     & (table["setting"] == "combine")]
        if rows.empty:
            if position not in GAME_ONLY:
                raise common.StageRefused(
                    f"{STAGE}: {position} has no Combine rows to cross-check")
            continue
        counts, total = _count_bands(combine_turns, edges, overflow,
                                     position, "combine")
        if total != int(rows["setting_total"].iloc[0]):
            raise common.StageRefused(
                f"{STAGE}: {position} Combine total is {total} in the turn "
                f"table but {int(rows['setting_total'].iloc[0])} uploaded")
        for _, row in rows.iterrows():
            want = counts[str(row["band"])]
            if int(row["count"]) != want:
                raise common.StageRefused(
                    f"{STAGE}: {position} {row['band']} Combine count is "
                    f"{want} in the turn table but {int(row['count'])} uploaded")
            checked += 1
    return {"rows_checked": checked, "matches_combine_turn_table": True}


def check_db_game_side(uploaded: pd.DataFrame, source_dir: Path,
                       combine_turns_path: Path, edges: list[float],
                       overflow: str) -> dict:
    """The uploaded DB game rows must equal figure 1's assembled DB table."""
    game = full_run.load_game_turns(source_dir)
    combine = full_run.load_combine_turns(combine_turns_path)
    turns = full_run.assemble_turns(game, combine)
    table, context = figures.band_table(
        turns, edges, overflow, "DB")
    return full_run.check_band_csv(Path(source_dir) / ARTEFACT_BANDS,
                                   table, context)


def player_counts(combine_turns: pd.DataFrame, game: pd.DataFrame,
                  positions: list[str]) -> tuple[dict[str, dict[str, int]],
                                                 dict[str, int]]:
    """Player exposure the upload can actually support, and nothing more."""
    combine_valid = combine_turns["valid"].fillna(False).astype(bool)
    by_setting: dict[str, dict[str, int]] = {}
    for position in positions:
        found: dict[str, int] = {}
        part = combine_turns[combine_valid
                             & (combine_turns["position_group"] == position)]
        if len(part):
            found["combine"] = int(part["nfl_id"].nunique())
        by_setting[position] = found

    game_valid = game["valid"].fillna(False).astype(bool)
    db_game = game[game_valid]
    by_setting.setdefault("DB", {})["game"] = int(db_game["nfl_id"].nunique())
    player_games = {"DB": int(db_game.groupby(["nfl_id", "game_id"]).ngroups)}
    return by_setting, player_games


def build_context(table: pd.DataFrame, positions: list[str],
                  edges: list[float], overflow: str,
                  players: dict[str, dict[str, int]],
                  player_games: dict[str, int]) -> dict:
    per_position: dict[str, dict] = {}
    for position in positions:
        part = table[table["position"] == position]
        n_by_setting = {str(s): int(v) for s, v in
                        part.groupby("setting", sort=False)["count"].sum().items()}
        per_position[position] = {
            "n_valid": int(part["count"].sum()),
            "n_by_setting": n_by_setting,
            "players_by_setting": dict(players.get(position, {})),
        }
    untested = [p for p in positions
                if int(per_position[p]["n_by_setting"].get("combine", 0)) == 0]
    return {
        "positions": list(positions),
        "bands": figures.band_labels(edges, overflow),
        "edges": [float(e) for e in edges],
        "per_position": per_position,
        "n_turns": int(table["count"].sum()),
        "n_excluded": 0,
        "combine_untested": untested,
        "player_games": player_games,
    }
def render(cfg: common.Config, table: pd.DataFrame, context: dict, *,
           source_records: list[dict], manifest_doc: dict, identity: dict,
           crosschecks: dict, out_dir: Path | None = None) -> dict:
    """Draw figure 5 and write every FULL-provenance artefact."""
    outdir = Path(out_dir) if out_dir else cfg.repo_path("figures")
    png_path = outdir / f"{FIGURE_KEY}.png"
    csv_path = outdir / f"{FIGURE_KEY}.csv"
    manifest_path = outdir / "figures_manifest.json"
    provenance_path = outdir / f"{FIGURE_KEY}.provenance.json"
    text_path = outdir / f"{FIGURE_KEY}.drawn_text.json"

    csv = figures.all_positions_csv_frame(table, context, cfg)
    common.write_csv(csv, csv_path, "full", cfg,
                     extra={"stamp": figures.stamp_text("full"),
                            "figure": FIGURE_KEY})

    plot = figures.plot_all_positions(table, context, cfg)
    fig = figures.render(plot, cfg, png_path, block="all_positions")
    texts = figure_texts(fig)

    banned = [hit for text in texts for token in BANNED_TOKENS
              if token in text.upper() for hit in [(token, text)]]
    if banned:
        raise common.StageRefused(
            f"{STAGE}: drawn figure carries provenance hedging: {banned}")

    title = str(plot.labels.get("title", ""))
    if title not in [t for t in texts if t]:
        raise common.StageRefused(
            f"{STAGE}: the rendered heading is not in the drawn text dump")

    payload = figures.all_positions_manifest_payload(table, context, cfg)
    payload["provenance"] = "FULL"
    payload["stamp"] = figures.stamp_text("full")
    payload["sources"] = ([record["path"] for record in source_records]
                          + [f"{COMBINE_TURNS.as_posix()}#setting=combine"])
    payload["source_artefacts"] = source_records
    payload["source_run"] = {
        "manifest": ARTEFACT_MANIFEST,
        "git_rev": identity["run_git_rev"],
        "config_sha": identity["config_sha"],
        "runtime_seconds": manifest_doc.get("runtime_seconds"),
    }
    payload["crosschecks"] = crosschecks
    payload["position_order_note"] = (
        "Order is the configured TASK order with OTHER appended; not a ranking "
        "by gap size.")
    payload["combine_untested"] = list(context["combine_untested"])
    payload["players_known"] = {
        "source": ("Combine player counts from outputs/turns/turns.parquet; "
                   "game counts for DB from the uploaded full DB turn table. "
                   "The uploaded band table carries no player exposure for the "
                   "other game positions, so those CSV cells are blank."),
        "by_position_setting": {
            p: dict(v) for p, v in
            ((p, c.get("players_by_setting", {}))
             for p, c in context["per_position"].items())},
        "player_games": context.get("player_games", {}),
    }
    payload["caveats"] = list(payload.get("caveats", [])) + [
        ("Every count and share here is read from the uploaded full-run band "
         "table (bdb27_full_game_bands.csv), not from the sample turn table. "
         "The Combine side is cross-checked row for row against the Combine "
         "turn table; DB game rows against figure 1's assembled DB table."),
        ("Player exposure is not in the uploaded band table for the non-DB "
         "game positions, so the CSV leaves those n_players cells blank "
         "rather than copying sample numbers."),
    ]
    if context["combine_untested"]:
        payload["caveats"].append(
            f"{', '.join(context['combine_untested'])} has no Combine turns in "
            "this run; its panel is drawn game-only and is excluded from the "
            "\"every position the Combine tests\" wording, not hidden.")
    payload["rendered_text"] = text_path.as_posix()
    document = full_run.merge_manifest(manifest_path, payload, cfg)

    band0 = table[table["band"] == context["bands"][0]]
    record = {
        "figure": FIGURE_KEY,
        "title": payload["title"],
        "claim": payload["claim"],
        "source_artefacts": source_records,
        "source_run": payload["source_run"],
        "identity": identity,
        "crosschecks": crosschecks,
        "positions": list(context["positions"]),
        "combine_untested": list(context["combine_untested"]),
        "per_position": {
            p: {
                "n_valid": int(c["n_valid"]),
                "n_by_setting": dict(c["n_by_setting"]),
                "players_by_setting": dict(c.get("players_by_setting", {})),
            } for p, c in context["per_position"].items()},
        "counts": {
            "n_turns_drawn": int(context["n_turns"]),
            "positions": int(len(context["positions"])),
            "band0_combine_count": int(
                band0.loc[band0["setting"] == "combine", "count"].iloc[0])
            if (band0["setting"] == "combine").any() else 0,
            "band0_game_count": int(
                band0.loc[band0["setting"] == "game", "count"].iloc[0]),
        },
        "manifest_provenance": document.get("provenance"),
        "outputs": {"png": png_path.as_posix(),
                    "csv": csv_path.as_posix(),
                    "manifest": manifest_path.as_posix(),
                    "drawn_text": text_path.as_posix()},
    }
    common.write_json(record, provenance_path, "full", cfg)
    visible = full_run._visible_strings(fig)
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
        "payload": payload,
        "png": png_path,
        "csv": csv_path,
        "manifest": manifest_path,
        "provenance": provenance_path,
        "drawn_text": text_path,
        "texts": texts,
    }


def run(cfg: common.Config, *, source_dir: Path | None = None,
        combine_turns: Path | None = None, root: Path | None = None,
        out_dir: Path | None = None) -> dict:
    """Verify the upload, rebuild figure 5's table, draw it."""
    root = Path(root or Path(__file__).resolve().parents[2])
    source_dir = Path(source_dir or root / full_run.ARTEFACT_DIR)
    combine_turns = Path(combine_turns or root / COMBINE_TURNS)

    records = full_run.verify_sources(source_dir, root)
    manifest_doc = full_run.load_run_manifest(source_dir)
    identity = full_run.verify_run_identity(manifest_doc, cfg, root)

    game = full_run.load_game_turns(source_dir)
    full_run.check_manifest_counts(manifest_doc, game)

    uploaded = load_band_table(source_dir)
    positions = positions_order(uploaded, cfg, required=POSITIONS)
    edges = [float(x) for x in
             cfg.get("figures.hole.plot_bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    table = table_from_bands(uploaded, positions, edges, overflow)

    combine_turns_df = figures.load_turns(combine_turns)
    crosschecks = {
        "combine": check_combine_side(table, combine_turns_df, edges, overflow,
                                      positions),
        "db_game": check_db_game_side(uploaded, source_dir, combine_turns,
                                      edges, overflow),
    }

    players, player_games = player_counts(combine_turns_df, game, positions)
    context = build_context(table, positions, edges, overflow, players,
                            player_games)

    result = render(cfg, table, context, source_records=records,
                    manifest_doc=manifest_doc, identity=identity,
                    crosschecks=crosschecks, out_dir=out_dir)
    result["crosschecks"] = crosschecks
    return result
