"""Synthetic FULL-provenance fixture for the two renderers.

**This is a smoke test. None of these numbers is a result.** The repository has
no full-data run, so no figure has ever been drawn and no writeup token has ever
been filled. This fixture exists to prove that the render paths work on data
shaped like a full run — not to demonstrate anything about football.

Two jobs:

* ``build(dest)`` writes the committed fixture files. Deterministic, fixed seed,
  no network. Run it only when the fixture itself needs regenerating; the
  outputs are in version control.
* ``materialise(outputs_dir)`` lays the fixture out as an ``outputs/`` tree
  (``phase4/decomposition.json``, ``validation/validation.json``,
  ``phase3/snaps.parquet``, ``phase2/physical_time_by_player.csv``,
  ``phase4/player_effects.csv``) so ``notebook_report.render_figures`` and
  ``writeup_render.render`` can be pointed at it. The parquet is built here and
  never committed.

The artefacts are stamped ``provenance: "FULL"`` because the renderers (by
design) refuse anything else, and carry ``fixture: true`` plus a note so the
stamp cannot be quoted as evidence. The writeup renderer prints a warning when
it sees that flag.

Planted values are simple and internally consistent: each player has a
Combine-style distance-time curve ``t_i(d) = k_i * d**0.72`` and a jump
``u_i``; each rep's crossing time is ``t_i(depth) + u_i + noise``. The
variance-split numbers in ``decomposition.json`` are illustrative constants, not
recovered from this generated data.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

FIXTURE_NOTE = ("SYNTHETIC FIXTURE — smoke test for the renderers. "
                "Not a BDB27 result; no full-data run produced these values.")

PLAYERS = [
    # (nfl_id, display_name, k, u_s)
    (9001, "Fixture Rusher A", 0.300, -0.031),
    (9002, "Fixture Rusher B", 0.318, 0.022),
    (9003, "Fixture Rusher C", 0.306, 0.048),
    (9004, "Fixture Rusher D", 0.330, -0.047),
    (9005, "Fixture Rusher E", 0.312, 0.011),
    (9006, "Fixture Rusher F", 0.324, -0.003),
]

REPS = {9001: 34, 9002: 31, 9003: 29, 9004: 36, 9005: 28, 9006: 30}

DISTANCES = [round(0.25 + 0.05 * i, 2) for i in range(46)]   # 0.25 .. 2.50


def physical_s(k: float, distance: float) -> float:
    """The fixture's Combine curve: time to cover ``distance``."""
    return round(k * distance ** 0.72, 5)


# --------------------------------------------------------------------------- #
# committed files
# --------------------------------------------------------------------------- #
def build(dest: str | Path) -> dict[str, Path]:
    """Write the committed fixture files into ``dest``."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    # physical_time_by_player.csv — one curve per player
    rows = ["nfl_id,display_name,distance_yd,posterior_mean_s,posterior_sd_s,provenance"]
    for nfl_id, name, k, _u in PLAYERS:
        for d in DISTANCES:
            rows.append(f"{nfl_id},{name},{d:.2f},{physical_s(k, d)},0.0120,FULL")
    written["phys"] = dest / "physical_time_by_player.fixture.csv"
    written["phys"].write_text("\n".join(rows) + "\n", encoding="utf-8")

    # snaps.csv — rep-level crossing times around each player's curve + jump
    rng = random.Random(20270106)
    snaps = ["nfl_id,depth_yd,cross_time_s,analysis_ready,in_primary_set,provenance"]
    for nfl_id, _name, k, u in PLAYERS:
        for _ in range(REPS[nfl_id]):
            depth = round(min(1.40, max(0.35, rng.gauss(0.66, 0.18))), 3)
            noise = rng.gauss(0.0, 0.055)
            t = physical_s(k, depth) + u + noise
            snaps.append(f"{nfl_id},{depth:.3f},{t:.4f},True,True,FULL")
    written["snaps"] = dest / "snaps.fixture.csv"
    written["snaps"].write_text("\n".join(snaps) + "\n", encoding="utf-8")

    # player_effects.csv — u and its SE, as 04_models writes them
    eff = ["player_id,u_s,u_se_s,n_reps,provenance"]
    for nfl_id, _name, _k, u in PLAYERS:
        eff.append(f"{nfl_id},{u},0.0180,{REPS[nfl_id]},FULL")
    written["effects"] = dest / "player_effects.fixture.csv"
    written["effects"].write_text("\n".join(eff) + "\n", encoding="utf-8")

    written["decomposition"] = dest / "decomposition.fixture.json"
    written["decomposition"].write_text(
        json.dumps(_decomposition(), indent=2) + "\n", encoding="utf-8")
    written["validation"] = dest / "validation.fixture.json"
    written["validation"].write_text(
        json.dumps(_validation(), indent=2) + "\n", encoding="utf-8")
    return written


def _group(label: str, n_reps: int, n_players: int, n_team_games: int,
           b: float, share_phys: float, share_u: float, remainder: float,
           jump_ms: float, rel_sb: float, rel_mb: float, r_u: float, r_p: float,
           **extra) -> dict:
    return {
        "status": "OK",
        "label": label,
        "n_reps": n_reps,
        "n_players": n_players,
        "n_team_games": n_team_games,
        "b": b,
        "b_fit": b,
        "share_phys": share_phys,
        "share_u": share_u,
        "share_phys_total": round(share_phys * (1 - remainder), 4),
        "share_u_total": round(share_u * (1 - remainder), 4),
        "within_player_remainder_total": remainder,
        "jump_sd_s": round(jump_ms / 1000.0, 5),
        "jump_sd_ms": jump_ms,
        "rung_used": 0,
        "min_reps_per_player": 20,
        "converged": True,
        "boundary": {"sigma_u_zero": False, "between_phys_var_zero": False,
                     "note": "FIXTURE values; not an estimate."},
        "bootstrap": {
            "n_requested": 200,
            "n_ok": 200,
            "status": "OK",
            "b_ci95": [round(b - 0.17, 3), round(b + 0.18, 3)],
            "share_phys_ci95": [round(share_phys - 0.20, 3), round(share_phys + 0.21, 3)],
            "share_u_ci95": [round(share_u - 0.21, 3), round(share_u + 0.20, 3)],
            "within_player_remainder_total_ci95": [round(remainder - 0.05, 3),
                                                   round(remainder + 0.05, 3)],
        },
        "ladder": {
            "rung_0_regression_calibration": {"status": "OK", "share_phys": share_phys,
                                              "share_u": share_u, "b": b},
            "rung_1_no_me_correction": {"status": "OK",
                                        "share_phys": round(min(0.99, share_phys + 0.06), 3),
                                        "share_u": round(max(0.01, share_u - 0.06), 3),
                                        "b": b},
            "rung_2_empirical_bayes": {"status": "OK",
                                       "share_phys": round(share_phys + 0.02, 3),
                                       "share_u": round(max(0.01, share_u - 0.02), 3),
                                       "b": b},
            "rung_3_random_intercept_only": {"status": "NOT_NEEDED"},
        },
        "reliability": {
            "split_half": {"n_players": n_players, "r_half": round(rel_sb - 0.08, 3),
                           "spearman_brown": rel_sb},
            "model_based": {"reliability": rel_mb, "n_players": n_players},
        },
        "agreement": {
            "u_vs_onset_latency": {"r": r_u, "n_players": n_players},
            "phys_vs_onset_to_cross": {"r": r_p, "n_players": n_players},
            "note": "FIXTURE values. Disagreement is a finding, not a failure.",
        },
        "year_to_year": {"status": "INSUFFICIENT_SEASONS", "n_pairs": 0},
        **extra,
    }


def _decomposition() -> dict:
    return {
        "provenance": "FULL",
        "fixture": True,
        "fixture_note": FIXTURE_NOTE,
        "stage": "04_models",
        "mode": "full",
        "pooled": _group("pooled", 188, 6, 42, 1.082, 0.318, 0.682, 0.845,
                         48.7, 0.792, 0.731, 0.463, 0.604),
        "edge": _group("edge", 91, 3, 24, 1.114, 0.271, 0.729, 0.862,
                       43.2, 0.741, 0.688, 0.412, 0.566),
        "interior": _group("interior", 97, 3, 26, 1.041, 0.356, 0.644, 0.829,
                           52.9, 0.813, 0.759, 0.508, 0.638),
        "model_form": "FIXTURE. y = a + b*phys + alignment + depth + u_i + v_g + e_ij.",
        "estimator": {"name": "FIXTURE — no estimator was run",
                      "measurement_error": "regression calibration",
                      "intervals": "player-cluster bootstrap"},
        "min_reps_per_player": 20,
        "phys_interpolation_status": {"OK": 188},
        "resolution": {
            "source": "FIXTURE (copied shape of outputs/prereg/power.json)",
            "provenance": "FULL",
            "smallest_detectable_jump_sd_ms_at_20_reps": 54.4,
            "smallest_detectable_jump_sd_ms_best_case": 51.8,
            "statement": "FIXTURE values. A resolution statement is not a threshold.",
        },
        "no_pressure_outcome": True,
        "no_blocker_term": True,
        "stance_term": "absent (no stance label; absorbed in u and e)",
    }


def _validation() -> dict:
    return {
        "provenance": "FULL",
        "fixture": True,
        "fixture_note": FIXTURE_NOTE,
        "stage": "05_validate",
        "mode": "full",
        "min_reps_per_player": 20,
        "sample": {"n_reps": 188, "n_players": 6, "n_team_games": 42},
        "base": {"b": 1.082, "share_phys": 0.318, "share_u": 0.682},
        "constraints": {"pressure_fields_read": [], "blocker_term": False,
                        "stance_term": False, "plane_offset_hardcoded": False,
                        "player_get_off_used": False},
        "checks": {
            "permutation": {
                "status": "OK",
                "n_requested": 200,
                "n_ok": 200,
                "observed_share_phys_percentile": 98.5,
                "one_sided_monte_carlo_p": 0.015,
                "permutation_share_phys": {"mean": 0.031, "p97_5": 0.212},
                "reading": "FIXTURE: the observed share lies above the permutation "
                           "distribution. Smoke test only.",
            },
            "placebo": {
                "status": "OK",
                "real": {"share_phys": 0.318, "b": 1.082, "n_players": 6},
                "placebo": {"share_phys": 0.104, "b": 1.021, "n_players": 6},
                "reading": "FIXTURE: height/weight carry less of the share than "
                           "physical time. Smoke test only.",
            },
            "mechanical": {
                "status": "OK",
                "base": {"b": 1.082, "share_phys": 0.318, "share_u": 0.682},
                "depth_removed": {"b": 1.104, "share_phys": 0.301, "share_u": 0.699},
                "alignment_removed": {"b": 1.071, "share_phys": 0.329, "share_u": 0.671},
                "both_removed": {"b": 1.095, "share_phys": 0.312, "share_u": 0.688},
                "onset": {"grid": [0.75, 1.0, 1.25, 1.5], "share_phys": [0.288, 0.318, 0.334, 0.341]},
                "stance": "NOT POSSIBLE (B8: no stance label)",
                "reading": "FIXTURE values. Smoke test only.",
            },
            "snap_quality": {
                "status": "OK",
                "fitted_set": {"n_reps": 188, "n_clean": 175, "n_dropped": 13,
                               "share_dropped": 0.069149},
                "base": {"share_phys": 0.318},
                "clean": {"share_phys": 0.326},
                "reading": "FIXTURE values. Smoke test only.",
            },
            "selection": {
                "status": "OK",
                "unweighted": {"share_phys": 0.318},
                "equal_snap_weighted": {"share_phys": 0.334},
                "draftee_selection": "CANNOT BE TESTED on this release (B5)",
                "reading": "FIXTURE values. Smoke test only.",
            },
        },
        "resolution": {
            "status": "OK",
            "kind": "resolution_statement",
            "final_sample": {"n_reps": 188, "n_players": 6, "n_team_games": 42,
                             "min_reps_per_player": 20},
            "between_player_sd_s": 0.0487,
            "p_eff_realised": 5.42,
            "floor_share_realised": 0.6547,
            "smallest_detectable_jump_sd_ms_realised": 61.2,
            "statement": "FIXTURE value: reported as a resolution statement, not a "
                         "threshold.",
        },
        "synthetic_can_fail": {},
        "status": "OK",
    }


# --------------------------------------------------------------------------- #
# materialisation
# --------------------------------------------------------------------------- #
def materialise(outputs_dir: str | Path, fixture_dir: str | Path | None = None) -> Path:
    """Lay the fixture out as an ``outputs/`` tree. Returns the outputs dir."""
    outputs = Path(outputs_dir)
    here = Path(fixture_dir) if fixture_dir is not None else Path(__file__).resolve().parent

    import pandas as pd

    (outputs / "phase4").mkdir(parents=True, exist_ok=True)
    (outputs / "phase3").mkdir(parents=True, exist_ok=True)
    (outputs / "phase2").mkdir(parents=True, exist_ok=True)
    (outputs / "validation").mkdir(parents=True, exist_ok=True)

    (outputs / "phase4" / "decomposition.json").write_text(
        (here / "decomposition.fixture.json").read_text(encoding="utf-8"), encoding="utf-8")
    (outputs / "validation" / "validation.json").write_text(
        (here / "validation.fixture.json").read_text(encoding="utf-8"), encoding="utf-8")

    snaps = pd.read_csv(here / "snaps.fixture.csv")
    snaps.to_parquet(outputs / "phase3" / "snaps.parquet", index=False)
    phys = pd.read_csv(here / "physical_time_by_player.fixture.csv")
    phys.to_csv(outputs / "phase2" / "physical_time_by_player.csv", index=False)
    effects = pd.read_csv(here / "player_effects.fixture.csv")
    effects.to_csv(outputs / "phase4" / "player_effects.csv", index=False)
    return outputs


if __name__ == "__main__":  # pragma: no cover
    here = Path(__file__).resolve().parent
    for name, path in build(here).items():
        print(f"{name}: {path}")
    print(f"fixture written to {here}; {len(PLAYERS)} players, "
          f"{math.fsum(REPS.values())} reps")
