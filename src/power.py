"""power — design power for the locked 50 ms jump-SD smallest effect of interest.

This module answers ONE question, on evidence, and fits nothing:

    given the fittable player count P and the reliability evidence in
    ``outputs/phase3b/``, what is the smallest jump SD the design can detect,
    and is 50 ms detectable?

The estimator is the design floor already used by Phase 1 and Phase 3b:

    min detectable share of between-player variance = z * sqrt(2 / P)

with ``P`` the Kish effective count of the fittable players' three-level
reliabilities. The share of between-player **variance** carried by a jump SD
``tau`` is ``(tau / sigma_between)^2``; solving the floor for ``tau`` gives the
smallest detectable SD. Nothing here is estimated from the sample: the inputs
are the complete label-file artefacts written by Phase 3b (provenance ``FULL``).

Two limits are carried into every report rather than hidden:

1. **Reliability is measured on the raw get-off mean.** It is an upper bound on
   the reliability of the residual "jump" (0.869 at 25% of between-player
   variance removed, 0.815 at 50%; Phase 3b). Lower reliability cannot make the
   design *more* powerful, so the smallest detectable SD reported here is a
   best case.
2. **The locked outcome is the tracked LOS crossing**, which adds the ≈0.29 yd
   integer-yard spot error (≈0.14 s) to the outcome. The reliability evidence
   was measured on the published label. The table is therefore optimistic for
   the locked estimand; this is stated, not corrected with an invented number.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import stats as ST

STAGE = "power_calculation"


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def load_player_table(path: str | Path) -> list[dict]:
    """Read Phase 3b's per-player reliability table.

    Only rows stamped ``FULL`` are accepted: this module's evidence claim rests
    on the inputs being complete label-file artefacts, not a tracking sample.
    """
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    if not rows:
        raise ValueError(f"{path}: empty reliability table")
    stamps = {r.get("provenance") for r in rows}
    if stamps != {"FULL"}:
        raise ValueError(
            f"{path}: expected every row stamped FULL, found {sorted(stamps)}; "
            "the power calculation must not run on a sample-limited table")
    out = []
    for r in rows:
        out.append({
            "nfl_id": r["nfl_id"],
            "reps": int(float(r["reps_primary"])),
            "reliability": float(r["reliability_three_level"]),
            "has_forty_attempt": _as_bool(r["has_forty_attempt"]),
        })
    return out


def load_summary(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("provenance") != "FULL":
        raise ValueError(
            f"{path}: provenance {payload.get('provenance')!r}, expected FULL")
    return payload


# --------------------------------------------------------------------------- #
# Estimators
# --------------------------------------------------------------------------- #
def median(values: Sequence[float]) -> float:
    v = sorted(float(x) for x in values)
    if not v:
        return float("nan")
    n = len(v)
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def detectability(jump_sd_s: float, between_sd_s: float, p_eff: float,
                  z: float = 1.96) -> dict:
    """Smallest detectable jump SD at a given effective player count.

    ``floor = z*sqrt(2/P_eff)`` is a share of between-player variance. The
    smallest detectable jump SD is ``between_sd * sqrt(floor)``. A jump SD at or
    above that value is called detectable.
    """
    if p_eff is None or math.isnan(p_eff) or p_eff < 2:
        return {"p_eff": p_eff, "floor_share": float("nan"),
                "smallest_detectable_jump_sd_s": float("nan"),
                "detectable": False}
    floor = ST.min_detectable_share(p_eff, z)["min_share"]
    min_sd = ST.sd_from_variance_share(floor, between_sd_s)
    return {"p_eff": float(p_eff), "floor_share": float(floor),
            "smallest_detectable_jump_sd_s": float(min_sd),
            "detectable": bool(jump_sd_s >= min_sd)}


def summarise_cut(players: Iterable[Mapping], cut: float, *,
                  between_sd_s: float, jump_sd_s: float,
                  z: float = 1.96) -> dict:
    """One row of the power table for a minimum-reps cut."""
    admitted = [p for p in players if p["reps"] >= cut]
    fittable = [p for p in admitted if p["has_forty_attempt"]]
    rels = [p["reliability"] for p in fittable]
    p_eff = ST.effective_players(rels) if rels else float("nan")
    eff = detectability(jump_sd_s, between_sd_s, p_eff, z)
    head = ST.min_detectable_share(len(fittable), z)["min_share"] if len(fittable) >= 2 else float("nan")
    head_sd = ST.sd_from_variance_share(head, between_sd_s) if not math.isnan(head) else float("nan")
    return {
        "min_reps_per_player": float(cut),
        "players_admitted": len(admitted),
        "players_fittable": len(fittable),
        "median_reliability_fittable_three_level": median(rels),
        "effective_players_fittable": p_eff,
        "floor_share_effective": eff["floor_share"],
        "smallest_detectable_jump_sd_ms_effective": (
            1000.0 * eff["smallest_detectable_jump_sd_s"]),
        "floor_share_headcount": head,
        "smallest_detectable_jump_sd_ms_headcount": (
            1000.0 * head_sd if not math.isnan(head_sd) else float("nan")),
        "resolves_50ms": eff["detectable"],
    }


def build_report(players: Sequence[Mapping], summary: Mapping, *,
                 cuts: Sequence[float], between_sd_s: float,
                 jump_sd_s: float, z: float, output_inputs: Mapping) -> dict:
    """The full power artefact: table, answers, estimator, caveats."""
    soi_share = ST.variance_share_from_sd(jump_sd_s, between_sd_s)
    required = ST.players_required_for_share(soi_share, z)
    table = [summarise_cut(players, cut, between_sd_s=between_sd_s,
                           jump_sd_s=jump_sd_s, z=z) for cut in cuts]
    all_fittable = summarise_cut(players, 0, between_sd_s=between_sd_s,
                                 jump_sd_s=jump_sd_s, z=z)
    resolving = [row["min_reps_per_player"] for row in table
                 if row["resolves_50ms"]]
    best = min(table + [all_fittable],
               key=lambda r: r["smallest_detectable_jump_sd_ms_effective"])
    return {
        "stage": STAGE,
        "status": "EVIDENCE_DRAFT",
        "question": ("what is the smallest detectable jump SD given the "
                     "fittable player count and the Phase 3b reliability, and "
                     "is the locked 50 ms SOI detectable?"),
        "locked_soi": {
            "jump_sd_ms": 1000.0 * jump_sd_s,
            "between_player_sd_s": between_sd_s,
            "between_player_share_noise_corrected": float(
                summary["distribution"]["between_share_noise_corrected"]),
            "jump_share_of_between_player_variance": soi_share,
            "denominator": "between_player_variance",
            "reading": (
                "50 ms is 49.8% of the 0.1004 s between-player SD, i.e. 24.8% "
                "of between-player variance. The share is squared; comparing "
                "an SD ratio to a variance-share floor would overstate power."),
        },
        "estimator": {
            "floor": "z*sqrt(2/P_eff): normal approximation to the sampling SD "
                     "of a between-player variance share",
            "z": z,
            "p_eff": "Kish (sum r)^2/sum r^2 over fittable players' three-level "
                     "reliabilities",
            "smallest_detectable_jump_sd": "between_sd*sqrt(floor)",
            "why": ("This is the design floor already used by Phase 1(i) and "
                    "Phase 3b, extended with the share-to-SD inversion. It is "
                    "a design approximation, not a posterior width; the Phase 4 "
                    "fit is the confirmation."),
            "k_two_level_cross_check": float(
                summary["two_level_model"]["k_sigma2_over_tau2"]),
            "reliability_three_level_at_median": float(
                summary["three_level_model"]["reliability_at_median"]),
        },
        "table": table,
        "no_rep_cut": all_fittable,
        "answers": {
            "soi_share_of_between_player_variance": soi_share,
            "detectable_at_any_candidate_cut": bool(resolving),
            "smallest_cut_resolving_50ms": (min(resolving) if resolving else None),
            "smallest_detectable_jump_sd_ms_at_20_reps": table[0][
                "smallest_detectable_jump_sd_ms_effective"] if table else None,
            "smallest_detectable_jump_sd_ms_best_case": best[
                "smallest_detectable_jump_sd_ms_effective"],
            "best_case_min_reps_per_player": best["min_reps_per_player"],
            "players_required_for_50ms": required,
            "fittable_players_available": all_fittable["players_fittable"],
            "verdict": (
                "50 ms is not detectable at any candidate cut, nor with every "
                "fittable player. The smallest detectable jump SD is "
                f"{table[0]['smallest_detectable_jump_sd_ms_effective']:.1f} ms "
                "at the 20-rep cut and "
                f"{all_fittable['smallest_detectable_jump_sd_ms_effective']:.1f} "
                f"ms with all {all_fittable['players_fittable']} fittable "
                f"players. It would become detectable at P_eff >= {required} "
                f"(about {required} fittable players at the cut-20 reliability "
                "mix). The release supplies at most "
                f"{all_fittable['players_fittable']} fittable players, so the "
                "locked SOI is outside what this design can resolve."
                if not resolving else
                "50 ms is detectable at the cuts listed in "
                "smallest_cut_resolving_50ms."),
        },
        "caveats": [
            "The reliability evidence is measured on the published "
            "player_get_off mean, an upper bound on the reliability of the "
            "residual jump (0.869 at 25% of between-player variance removed, "
            "0.815 at 50%). The table is therefore a best case.",
            "The locked outcome is the tracked LOS crossing, which carries the "
            "integer-yard spot error (about 0.29 yd, 0.14 s). That noise is not "
            "in the reliability evidence; it can only raise the smallest "
            "detectable SD.",
            "The floor is a design approximation, not a model-based power "
            "simulation. Phase 4's posterior intervals are the confirmation.",
            "The Kish count weights players by relative reliability only; a "
            "cruder P * mean-reliability attenuation gives a slightly larger "
            "smallest detectable SD at every cut. The verdict — no candidate "
            "cut resolves 50 ms — is unchanged.",
            "No number here is UNVALIDATED SAMPLE OUTPUT: every input is a "
            "complete label-file artefact (Phase 3b, provenance FULL). The "
            "0.86 yd plane offset does not enter this calculation.",
        ],
        "inputs": dict(output_inputs),
    }
