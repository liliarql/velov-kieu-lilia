"""Promotion d'un modèle : le challenger devient-il champion ? (S4)

Règle en trois conditions, écrite avant de regarder les chiffres :
1. Le challenger bat la baseline de persistance (EX-05).
2. Il améliore le champion d'au moins MIN_GAIN (en MAE absolue).
3. Il ne dégrade pas les heures de pointe au-delà de PEAK_TOLERANCE.

Par défaut le script affiche sa décision sans rien changer (dry-run).
Avec --apply, il déplace les alias (champion, previous-champion).
Avec --rollback, il restaure le previous-champion en champion.

Usage :
    python -m velov.promote --data data/velov_history.csv
    python -m velov.promote --data data/velov_history.csv --apply
    python -m velov.promote --rollback
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mlflow import MlflowClient

from velov.features import make_training_frame
from velov.registry import CHALLENGER, CHAMPION, MODEL_NAME, PREVIOUS
from velov.tracking import load_registered, version_by_alias
from velov.train import evaluate, load_training_data, temporal_split

MIN_GAIN = 0.01
PEAK_TOLERANCE = 0.05


def decide(
    challenger: dict,
    champion: dict | None,
    min_gain: float = MIN_GAIN,
    peak_tolerance: float = PEAK_TOLERANCE,
) -> tuple[bool, str]:
    """Applique la règle de promotion à des métriques déjà calculées (fonction pure)."""
    # Condition 1 : bat la persistance (EX-05)
    if challenger["mae_model"] >= challenger["mae_baseline_persistence"]:
        return False, "le challenger ne bat pas la baseline de persistance (EX-05)"

    # Pas de champion : premier modèle valide
    if champion is None:
        return True, "aucun champion : premier modèle qui bat la baseline"

    # Condition 2 : améliore le champion d'au moins min_gain
    target = champion["mae_model"] - min_gain
    if challenger["mae_model"] > target:
        return False, f"MAE {challenger['mae_model']:.3f} > {target:.3f} : gain insuffisant sur le champion"

    # Condition 3 : heures de pointe non dégradées
    chall_peak = challenger.get("mae_model_peak_hours")
    champ_peak = champion.get("mae_model_peak_hours")
    if chall_peak is not None and champ_peak is not None:
        limit = champ_peak + peak_tolerance
        if chall_peak > limit:
            return False, f"MAE peak {chall_peak:.3f} > {limit:.3f} : dégradation aux heures de pointe"

    return True, f"MAE {challenger['mae_model']:.3f} <= {target:.3f} et peak OK"


def rollback() -> int:
    """Restaure le previous-champion en champion."""
    prev = version_by_alias(PREVIOUS)
    if prev is None:
        print("Aucun previous-champion : impossible de revenir en arrière.")
        return 1
    client = MlflowClient()
    current = version_by_alias(CHAMPION)
    if current is not None:
        client.set_registered_model_alias(MODEL_NAME, CHALLENGER, current.version)
    client.set_registered_model_alias(MODEL_NAME, CHAMPION, prev.version)
    print(f"Rollback : champion -> version {prev.version} (ancien previous-champion)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare challenger et champion, puis promeut ou non.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--min-gain", type=float, default=MIN_GAIN)
    parser.add_argument("--peak-tolerance", type=float, default=PEAK_TOLERANCE)
    parser.add_argument("--apply", action="store_true", help="déplacer les alias (sinon : décision seule)")
    parser.add_argument("--rollback", action="store_true", help="restaurer le previous-champion en champion")
    args = parser.parse_args()

    if args.rollback:
        return rollback()

    challenger_v = version_by_alias(CHALLENGER)
    if challenger_v is None:
        print("Aucune version avec l'alias challenger : rien à évaluer.")
        return 1
    champion_v = version_by_alias(CHAMPION)

    raw = load_training_data(args.data)
    _, test_df = temporal_split(make_training_frame(raw), args.test_days)
    first, last = test_df["timestamp"].min(), test_df["timestamp"].max()
    print(f"Fenêtre de test : {first:%Y-%m-%d %H:%M} -> {last:%Y-%m-%d %H:%M} UTC, {len(test_df)} lignes")

    c_ver = str(challenger_v.version)
    scores: dict[str, dict] = {"challenger": evaluate(load_registered(c_ver), test_df)}
    print(f"{'challenger v' + c_ver:<20}: MAE {scores['challenger']['mae_model']:.3f}")
    if champion_v is not None:
        ch_ver = str(champion_v.version)
        scores["champion"] = evaluate(load_registered(ch_ver), test_df)
        print(f"{'champion v' + ch_ver:<20}: MAE {scores['champion']['mae_model']:.3f}")
    print(f"{'persistance':<20}: MAE {scores['challenger']['mae_baseline_persistence']:.3f}")

    promote, reason = decide(scores["challenger"], scores.get("champion"), args.min_gain, args.peak_tolerance)
    print(f"Décision : {'PROMOUVOIR' if promote else 'GARDER le champion'} ({reason})")

    if promote and args.apply:
        client = MlflowClient()
        if champion_v is not None:
            client.set_registered_model_alias(MODEL_NAME, PREVIOUS, champion_v.version)
        client.set_registered_model_alias(MODEL_NAME, CHAMPION, challenger_v.version)
        client.set_model_version_tag(MODEL_NAME, challenger_v.version, "promotion", reason)
        print(f"Alias champion -> version {challenger_v.version}")
    elif promote:
        print("Rien n'a été modifié : relancer avec --apply pour promouvoir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
