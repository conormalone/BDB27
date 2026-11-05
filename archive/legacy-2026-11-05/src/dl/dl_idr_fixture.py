#!/usr/bin/env python3
"""dl_idr_fixture — synthetic worlds for the Phase-4 Part-1 IDR model.

Three deterministic worlds, all expressed in the **snap-level schema the
production model consumes** (the Phase-3 columns of ``notes/schema.md`` §9):

``planted``
    A load x rest interaction exists at the population level: with
    ``log(deficit) = b_load*z_load + b_rest*z_rest + b_int*z_load*z_rest``,
    ``b_int > 0`` with ``b_load < 0`` — i.e. **more load and less rest gives a
    larger deficit**, the direction the confirmatory estimand claims.

``null``
    No effects at all.  The production model must return an interval that
    covers zero.

``random_slopes``
    The interaction has **population mean zero** and exists only as a
    per-rusher random slope (mean-zero, sd ``b_int_player_sd``).  A
    player-clustered interval must cover zero; the naive i.i.d. interval is
    far too narrow, which is exactly what the clustering is there to prevent.
    This world is the evidence that clustering does not *manufacture* an
    interaction, it only stops the SE from pretending snaps are independent.

The regressor is the **windowed** load, not the single-rep load: the deficit
on snap *i* is generated from the power the rusher accumulated over his prior
three snaps, which is what the production :func:`rolling_load` reconstructs.
Generating it from the current rep instead would make the fixture test a
quantity no model in the pipeline ever sees.

Every world is balanced (the same number of snaps per rusher) so the pooled
coefficient is an unweighted mean of the per-rusher slopes and the
random-slopes world's population estimate is zero by construction, not by
luck.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dl import dl_idr_models as I

__all__ = ["WORLDS", "build_idr_world"]

WINDOW = 3

WORLDS = {
    "planted": {"b0": 0.0, "b_load": -0.08, "b_rest": 0.03, "b_int": 0.05,
                "b_int_player_sd": 0.0, "b_load_player_sd": 0.0,
                "b0_player_sd": 0.03, "noise_sd": 0.02},
    "null": {"b0": 0.0, "b_load": 0.0, "b_rest": 0.0, "b_int": 0.0,
             "b_int_player_sd": 0.0, "b_load_player_sd": 0.0,
             "b0_player_sd": 0.03, "noise_sd": 0.02},
    "random_slopes": {"b0": 0.0, "b_load": -0.08, "b_rest": 0.03, "b_int": 0.0,
                      "b_int_player_sd": 0.14, "b_load_player_sd": 0.06,
                      "b0_player_sd": 0.03, "noise_sd": 0.02},
}


def build_idr_world(regime: str = "planted", *, seed: int = 20270107,
                    n_players: int = 24, n_games: int = 6,
                    snaps_per_player: int = 40) -> dict[str, object]:
    """Return ``{frame, truth}`` for one regime."""
    if regime not in WORLDS:
        raise ValueError(f"unknown regime {regime!r}; expected {sorted(WORLDS)}")
    truth = dict(WORLDS[regime])
    rng = np.random.default_rng(seed)

    rows = []
    for player in range(n_players):
        game = player % n_games
        for snap in range(snaps_per_player):
            rows.append({"game_id": 100 + game, "nfl_id": 5000 + player,
                         "play_id": snap + 1,
                         "rep_kinematic_load_w_per_kg":
                             float(rng.gamma(shape=3.0, scale=1.6)),
                         "inter_snap_rest_s": float(rng.uniform(18.0, 48.0))})
    frame = pd.DataFrame(rows)
    frame["excluded_from_load"] = False

    # The production window rule builds the regressor the model will actually
    # see; the plant is defined ON that quantity.
    windowed = I.rolling_load(frame, WINDOW)
    load = windowed.to_numpy(dtype=float)
    observed = np.isfinite(load)
    load = np.where(observed, load, np.nanmean(load))
    rest = frame["inter_snap_rest_s"].to_numpy(dtype=float)
    z_load = (load - load[observed].mean()) / load[observed].std(ddof=0)
    z_rest = (rest - rest.mean()) / rest.std(ddof=0)

    players = frame["nfl_id"].to_numpy()
    uniq = np.unique(players)
    u0 = dict(zip(uniq, rng.normal(0.0, truth["b0_player_sd"], size=len(uniq))))
    load_dev = dict(zip(uniq, rng.normal(0.0, truth["b_load_player_sd"],
                                         size=len(uniq))))
    int_dev = dict(zip(uniq, rng.normal(0.0, truth["b_int_player_sd"],
                                        size=len(uniq))))

    log_deficit = (truth["b0"] + np.array([u0[p] for p in players])
                   + truth["b_load"] * z_load
                   + np.array([load_dev[p] for p in players]) * z_load
                   + truth["b_rest"] * z_rest
                   + (truth["b_int"] + np.array([int_dev[p] for p in players]))
                   * z_load * z_rest
                   + rng.normal(0.0, truth["noise_sd"], size=len(frame)))
    frame["deficit_ratio"] = np.exp(log_deficit)

    # Descriptive context columns (the model's control plan reads them).
    frame["team"] = "T" + frame["game_id"].astype(str)
    frame["season"] = 2024
    frame["alignment"] = np.where(np.arange(len(frame)) % 3 == 0, "edge",
                                  "interior")
    frame["down"] = (np.arange(len(frame)) % 4 + 1).astype(int)
    frame["yards_to_go"] = (np.arange(len(frame)) % 12 + 1).astype(float)
    frame["score_margin"] = rng.integers(-14, 15, size=len(frame)).astype(float)
    # Play context the Phase-5 context controls need (TASK.md): the quarter and
    # the clock remaining in it.  Deterministic and balanced so the
    # "final two minutes of each half" filter has rows to remove.
    frame["quarter"] = (np.arange(len(frame)) % 4 + 1).astype(int)
    frame["game_clock_s"] = rng.integers(0, 900, size=len(frame)).astype(float)
    # `blitz` is present but structurally unobservable (D78) in EVERY world,
    # matching the real product: the model must report it as unavailable.
    frame["blitz"] = np.nan
    frame["a0_snap_yds2"] = 6.0 * frame["deficit_ratio"]
    frame["baseline_a0_yds2"] = 6.0
    return {"frame": frame, "truth": truth}
