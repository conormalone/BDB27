"""dl_fixtures — four committed synthetic worlds (design §6).

Each world is a **schema-compliant analysis frame**, not a finding:

============  ==========================================================
world         planted truth
============  ==========================================================
``planted``   a0 raises the pressure hazard (HR 1.35 per SD) and decay
              follows **ceiling-relative** load
``absolute``  a0 raises the hazard and decay follows **absolute** load
``null``      no a0 effect, no load effect
``censoring`` the a0 effect is present but pressures are rare (~2%) and
              most snaps are censored
============  ==========================================================

Files per world (all small, all committed):

``snaps.csv``            the stage-03 snap schema, fully populated except the
                         flat ``absolute_load``/``relative_load`` columns, which
                         stay ``NA`` until a training fold freezes a threshold —
                         exactly as on real data.  The stage rebuilds the load
                         panel from ``in_game_speed_1_0``/``_2_0``.
``player_ceiling.csv``   long format (``nfl_id, feature, estimate, se, icc, ...``)
``players.csv``          ``nfl_id, draft_year, nfl_position``
``combine_results.csv``  ``nfl_id, draft_year, combine_height, combine_weight``
``meta.json``            the planted values, so a test can assert *direction*
                         without hard-coding a sample number

The generator is deterministic: the same seed writes byte-identical files.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_SRC))

from dl import dl_common as D  # noqa: E402
from dl import dl_models_core as M  # noqa: E402
from dl.dl_snaps import SNAP_COLUMNS  # noqa: E402

WORLDS = ("planted", "absolute", "null", "censoring")

#: Planted per-world settings.  These are fixture truths, never design values.
PLANT: dict[str, dict[str, Any]] = {
    "planted": {"hr_a0": 2.0, "decay": "relative", "gamma": 1.5, "rare": None,
                "shift": 2.1},
    "absolute": {"hr_a0": 2.0, "decay": "absolute", "gamma": 1.5, "rare": None,
                 "shift": 2.1},
    "null": {"hr_a0": 1.00, "decay": None, "gamma": 0.0, "rare": None,
             "shift": 2.1},
    "censoring": {"hr_a0": 2.0, "decay": None, "gamma": 0.0, "rare": 0.02,
                  "shift": 2.1},
}


def _frame(cfg: Mapping, world: str) -> tuple[pd.DataFrame, dict]:
    """Build one world's snap frame with the planted hazard structure."""
    block = D.dl(cfg) if "bdb27_dl" in cfg else cfg
    plant = PLANT[world]
    seed = int(cfg["seeds"]["fixture"]) if "seeds" in cfg else int(block["fixture"]["seed"])
    rng = np.random.default_rng(seed + WORLDS.index(world) * 101)

    n_players = int(block["fixture"]["n_players"])
    n_games = int(block["fixture"]["n_games"])
    snaps_per_game = int(block["fixture"].get("snaps_per_game", 20))

    players = pd.DataFrame({
        "nfl_id": np.arange(10001, 10001 + n_players),
        "draft_year": rng.choice([2023, 2024, 2025], size=n_players),
        "position_group": rng.choice(["edge", "interior"], size=n_players,
                                     p=[0.5, 0.5]),
        "a0": np.round(rng.normal(7.2, 0.9, size=n_players), 4),
        "tau": np.round(rng.uniform(0.55, 0.95, size=n_players), 4),
        "height_in": np.round(rng.normal(75.0, 2.2, size=n_players), 1),
        "weight_lb": np.round(rng.normal(285.0, 18.0, size=n_players), 1),
    })
    players["vmax"] = players["a0"] * players["tau"]
    players["ceiling_1_0"] = players["vmax"] * (1 - np.exp(-1.0 / players["tau"]))
    players["ceiling_2_0"] = players["vmax"] * (1 - np.exp(-2.0 / players["tau"]))
    # A player-level get-off component (so the mediator's random intercept is not
    # on the boundary) and a small negative association with a0 (better ceiling ->
    # quicker get-off), which makes E1 a non-degenerate descriptive path.
    players["go_player"] = (rng.normal(0.0, 0.06, size=n_players)
                            - 0.02 * (players["a0"] - players["a0"].mean()))

    rows: list[dict] = []
    for g in range(n_games):
        game_id = 900000 + g
        teams = ["AAA", "BBB", "CCC", "DDD"]
        opp = rng.choice(teams)
        for pid in players["nfl_id"]:
            # every player plays every game, so every rusher clears the §9
            # 100-snap floor and the fold rule can be exercised at 3 folds
            n_snaps = int(rng.integers(max(3, snaps_per_game - 1), snaps_per_game + 2))
            start = float(rng.uniform(60.0, 600.0))
            gaps = rng.uniform(25.0, 95.0, size=n_snaps)
            elapsed = start + np.cumsum(np.concatenate([[0.0], gaps[:-1]]))
            drive_base = 0
            for k, t in enumerate(elapsed):
                quarter = int(min(4, max(1, math.floor(t / 900.0) + 1)))
                clock = float(max(0.0, 900.0 - (t - (quarter - 1) * 900.0)))
                if k > 0 and elapsed[k] - elapsed[k - 1] > 300.0:
                    drive_base = 0
                rows.append({
                    "game_id": game_id, "play_id": g * 1000 + k * 3 + 1,
                    "nfl_id": int(pid), "season": int(players.loc[
                        players["nfl_id"] == pid, "draft_year"].iloc[0]),
                    "week": 1 + (g % 17), "opponent_team": str(opp),
                    "position_group": str(players.loc[
                        players["nfl_id"] == pid, "position_group"].iloc[0]),
                    "quarter": quarter, "game_clock_s": clock,
                    "elapsed": float(t),
                    "drive_position": drive_base,
                    "alignment": str(rng.choice(["edge", "interior"])),
                    "pre_snap_depth_yd": float(rng.uniform(0.5, 2.5)),
                    "blitz": float(rng.integers(0, 2)),
                    "n_blockers": float(rng.integers(1, 3)),
                    "down": int(rng.integers(1, 5)),
                    "yards_to_go": float(rng.integers(1, 16)),
                    "score_margin": float(rng.integers(-17, 18)),
                    "primary_blocker_group": str(rng.choice(["OL_L", "OL_R", "OL_C"])),
                    "seconds_since_last_snap": (float(elapsed[k] - elapsed[k - 1])
                                                if k else float("nan")),
                    "snaps_in_drive": float(drive_base),
                    "in_game_speed_1_0": float(max(0.0, rng.normal(4.6, 0.9))),
                    "in_game_speed_2_0": float(max(0.0, rng.normal(5.2, 0.9))),
                    "player_get_off": float(max(0.2, rng.normal(1.15, 0.12))),
                })
                drive_base += 1

    frame = pd.DataFrame(rows)
    frame = frame.merge(players[["nfl_id", "a0", "vmax", "tau", "go_player",
                                 "height_in", "weight_lb"]],
                        on="nfl_id", how="left")
    frame["player_get_off"] = (frame["player_get_off"] + frame["go_player"]).round(4)
    # The load panel is built exactly as the stage builds it (one arithmetic path).
    panel = M.build_load_panel(frame, block)
    frame = pd.concat([frame, panel], axis=1)

    # ---- planted outcome model (Weibull AFT) -------------------------------- #
    k = 1.5
    hr = float(plant["hr_a0"])
    beta_a0 = -math.log(hr) / k                     # per SD of a0
    z_a0 = (frame["a0"] - frame["a0"].mean()) / frame["a0"].std(ddof=0)
    eta = beta_a0 * z_a0.to_numpy()
    if plant["decay"] == "relative":
        col = "relative_load_0p7"
        load = frame[col].to_numpy(float)
        z = (load - np.nanmean(load)) / np.nanstd(load)
        eta = eta - (float(plant["gamma"]) / k) * np.nan_to_num(z)
    elif plant["decay"] == "absolute":
        col = "absolute_load_2p5"
        load = frame[col].to_numpy(float)
        z = (load - np.nanmean(load)) / np.nanstd(load)
        eta = eta - (float(plant["gamma"]) / k) * np.nan_to_num(z)
    # player-level frailty and noise
    b = rng.normal(0.0, 0.15, size=n_players)
    frailty = pd.Series(b, index=players["nfl_id"]).reindex(frame["nfl_id"]).to_numpy()
    # Weibull AFT: T = lambda * E**(1/k) with E ~ Exp(1).  (numpy's gumbel is the
    # MAXIMUM Gumbel, which is not the AFT error law — using it would generate a
    # distribution that is not Weibull and the fitted shape would wander.)
    noise = np.log(rng.exponential(1.0, size=len(frame))) / k
    log_t = float(plant.get('shift', 0.0)) + eta + frailty + noise
    t_event = np.exp(log_t)
    censor = rng.uniform(1.5, 4.0, size=len(frame))
    event = (t_event <= censor).astype(int)
    if plant["rare"] is not None:
        # heavy censoring: only a small share of snaps produce a pressure
        keep = rng.random(len(frame)) < float(plant["rare"]) / max(event.mean(), 1e-9)
        event = (event.astype(bool) & keep).astype(int)
        event = np.where(event == 1, 1, 0)
        # rare events are late ones: keep the recorded time, else censor
    # Censor time is the dropback duration; a charged pressure happened before
    # it by construction (the event is defined as t_event <= censor).
    frame["event"] = event
    frame["time_to_pressure"] = np.where(event == 1, np.round(t_event, 4), np.nan)
    frame["censor_time"] = np.round(censor, 4)
    frame["quick_pressure"] = np.where(
        (frame["event"] == 1) & (frame["time_to_pressure"] <= 2.5), 1.0, np.nan)
    frame["sack"] = 0.0
    frame["unblocked_pressure"] = 0.0
    frame["stunt"] = np.nan
    frame["n_rushers"] = np.nan
    frame["total_distance_before"] = np.nan
    frame["absolute_load"] = np.nan
    frame["relative_load"] = np.nan
    frame["garbage_time"] = False
    frame["provenance"] = "SYNTHETIC FIXTURE (not a finding)"
    meta = {
        "world": world,
        "planted": dict(plant),
        "seed": seed,
        "k_shape": k,
        "beta_a0_per_sd": beta_a0,
        "beta_load_per_sd": (-(float(plant["gamma"]) / k) if plant["decay"] else 0.0),
        "true_load_column": (f"relative_load_0p7" if plant["decay"] == "relative"
                             else ("absolute_load_2p5" if plant["decay"] == "absolute"
                                   else None)),
        "n_snaps": int(len(frame)),
        "n_players": int(n_players),
        "event_rate": float(frame["event"].mean()),
        "statement": "SYNTHETIC FIXTURE (not a finding)",
    }
    return frame, meta


def _ceiling_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Long-format ceiling table for the features the stage consumes."""
    players = frame[["nfl_id", "a0", "vmax", "tau"]].drop_duplicates("nfl_id")
    rng = np.random.default_rng(4242)
    rows = []
    for _, p in players.iterrows():
        a0_se = float(abs(rng.normal(0.18, 0.03)))
        rows.append({"nfl_id": int(p["nfl_id"]), "position_group": "edge",
                     "feature": "a0", "estimate": float(p["a0"]), "se": a0_se,
                     "n_attempts": 2, "icc": 0.62, "reliability": 0.9,
                     "selected": True, "vif": 2.0})
        rows.append({"nfl_id": int(p["nfl_id"]), "position_group": "edge",
                     "feature": "vmax", "estimate": float(p["vmax"]),
                     "se": float(abs(rng.normal(0.12, 0.02))), "n_attempts": 2,
                     "icc": 0.66, "reliability": 0.9, "selected": True, "vif": 3.0})
        rows.append({"nfl_id": int(p["nfl_id"]), "position_group": "edge",
                     "feature": "tau", "estimate": float(p["tau"]),
                     "se": float(abs(rng.normal(0.05, 0.01))), "n_attempts": 2,
                     "icc": 0.55, "reliability": 0.8, "selected": False, "vif": 3.0})
    table = pd.DataFrame(rows)
    table.insert(0, "provenance", "SYNTHETIC FIXTURE (not a finding)")
    return table


def write_world(cfg: Mapping, world: str, out_dir: Path) -> dict:
    """Write one world to ``out_dir``; returns its meta."""
    frame, meta = _frame(cfg, world)
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = [c for c in SNAP_COLUMNS if c in frame.columns]
    # The blocked-assignment control is not a stage-03 column on real data (it is
    # PENDING FULL RUN there); the fixture supplies it so the §8 design is exercised.
    if "primary_blocker_group" not in fields:
        fields.append("primary_blocker_group")
    out = frame[fields].copy()
    for col in out.columns:
        if out[col].dtype.kind == "f":
            out[col] = out[col].round(4)
    out.to_csv(out_dir / "snaps.csv", index=False)

    ceiling = _ceiling_table(frame)
    ceiling.to_csv(out_dir / "player_ceiling.csv", index=False)

    players = frame[["nfl_id", "season"]].drop_duplicates("nfl_id")
    players = players.rename(columns={"season": "draft_year"})
    players["nfl_position"] = "DE"
    players.to_csv(out_dir / "players.csv", index=False)

    combine = frame[["nfl_id", "season", "height_in", "weight_lb"]].drop_duplicates("nfl_id")
    combine = combine.rename(columns={"season": "draft_year",
                                      "height_in": "combine_height",
                                      "weight_lb": "combine_weight"})
    combine["combine_position"] = "DL"
    combine.to_csv(out_dir / "combine_results.csv", index=False)

    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n",
                                       encoding="utf-8")
    return meta


def world_config(cfg: Mapping, world_dir: Path, scratch: Path) -> dict:
    """A config dict whose DL paths point at one fixture world.

    The design values are copied unchanged from the repository config; only the
    paths and the fixture sizes change.
    """
    import copy

    out = copy.deepcopy(cfg)
    block = out["bdb27_dl"]
    block["paths"]["raw"] = str(world_dir)
    # Generated artefacts never land inside the committed fixture directory.
    block["paths"]["outputs"] = str(Path(scratch) / "outputs")
    block["paths"]["interim"] = str(scratch / "interim")
    block["paths"]["parquet"] = str(scratch / "parquet")
    block["paths"]["checkpoints"] = str(scratch / "checkpoints")
    block["paths"]["tmp"] = str(scratch / "tmp")
    block["paths"]["thresholds"] = str(scratch / "thresholds.json")
    # The fixture stores the analysis frames flat in the world directory.
    resolved = Path(world_dir).resolve()
    block["models"]["inputs"]["snaps"] = str(resolved / "snaps.csv")
    block["models"]["inputs"]["ceiling"] = str(resolved / "player_ceiling.csv")
    return out


def build_all(cfg: Mapping, root: Path | None = None) -> dict[str, dict]:
    """(Re)generate every world under ``root`` (default: the config fixture dir)."""
    block = D.dl(cfg) if "bdb27_dl" in cfg else cfg
    root = Path(root) if root else (D.REPO_ROOT / str(block["paths"]["fixtures"]))
    out = {}
    for world in WORLDS:
        out[world] = write_world(cfg, world, root / world)
    return out


def main(argv=None) -> int:
    cfg = D.load_config()
    metas = build_all(cfg)
    for world, meta in metas.items():
        print(f"{world}: n_snaps={meta['n_snaps']} players={meta['n_players']} "
              f"event_rate={meta['event_rate']:.4f} plot={meta['true_load_column']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
