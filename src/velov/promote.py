"""Règle de promotion du registre MLflow : le challenger devient-il champion ? (S4)

Trois conditions, toutes requises :
1. Le challenger bat la baseline de persistance (EX-05) : sinon le modèle ne justifie pas sa complexité.
2. Il améliore la MAE du champion d'au moins MIN_GAIN. Le seuil vient de la variance due à la seed :
   à données identiques, seeds 42 et 7 donnent 1,339 et 1,345 (écart 0,006, 5 seeds dans [1,339 ; 1,345]).
   Un gain inférieur à 0,01 n'est pas distinguable du bruit : on ne remplace pas le champion pour ça.
3. Il ne dégrade pas les heures de pointe de plus de PEAK_TOLERANCE (bruit de seed observé : 0,05).
   Une MAE globale meilleure peut cacher un modèle pire aux heures qui comptent le plus.

Sans champion, seule la condition 1 s'applique (premier modèle du registre).

Usage :
    python -m velov.promote             # applique la règle, pose l'alias champion si elle passe
    python -m velov.promote --dry-run   # affiche la décision sans toucher au registre
    python -m velov.promote --rollback  # retour arrière : l'ancien champion reprend l'alias champion
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

MIN_GAIN = 0.01
PEAK_TOLERANCE = 0.05


@dataclass(frozen=True)
class Decision:
    promote: bool
    reasons: list[str]


def decide(challenger: dict[str, float], champion: dict[str, float] | None) -> Decision:
    """Applique la règle sur les métriques de test des deux versions (fonction pure, testée sans MLflow)."""
    checks = [
        (
            challenger["mae_model"] < challenger["mae_baseline_persistence"],
            f"baseline : MAE {challenger['mae_model']:.3f} < persistance {challenger['mae_baseline_persistence']:.3f}",
        )
    ]
    if champion is not None:
        checks += [
            (
                challenger["mae_model"] <= champion["mae_model"] - MIN_GAIN,
                f"gain : MAE {challenger['mae_model']:.3f} <= champion {champion['mae_model']:.3f} - {MIN_GAIN}",
            ),
            (
                challenger["mae_model_peak_hours"] <= champion["mae_model_peak_hours"] + PEAK_TOLERANCE,
                f"pointe : MAE {challenger['mae_model_peak_hours']:.3f}"
                f" <= champion {champion['mae_model_peak_hours']:.3f} + {PEAK_TOLERANCE}",
            ),
        ]
    reasons = [("OK   " if ok else "ECHEC ") + label for ok, label in checks]
    return Decision(promote=all(ok for ok, _ in checks), reasons=reasons)


def main() -> None:
    parser = argparse.ArgumentParser(description="Promotion challenger -> champion dans le registre MLflow.")
    parser.add_argument("--dry-run", action="store_true", help="afficher la décision sans modifier le registre")
    parser.add_argument("--rollback", action="store_true", help="redonner l'alias champion à l'ancien champion")
    args = parser.parse_args()

    from mlflow import MlflowClient  # import local : la règle (decide) se teste sans MLflow
    from mlflow.exceptions import MlflowException

    from velov.registry import CHALLENGER, CHAMPION, METRICS, PREVIOUS_CHAMPION, REGISTERED_MODEL

    client = MlflowClient()

    def metrics_of(alias: str) -> tuple[str, dict[str, float]] | None:
        try:
            mv = client.get_model_version_by_alias(REGISTERED_MODEL, alias)
        except MlflowException:
            return None
        run_metrics = client.get_run(mv.run_id).data.metrics
        return mv.version, {k: run_metrics[k] for k in METRICS}

    if args.rollback:
        previous = metrics_of(PREVIOUS_CHAMPION)
        if previous is None:
            raise SystemExit(f"Aucune version avec l'alias {PREVIOUS_CHAMPION} : rien à restaurer")
        current = metrics_of(CHAMPION)
        client.set_registered_model_alias(REGISTERED_MODEL, CHAMPION, previous[0])
        client.delete_registered_model_alias(REGISTERED_MODEL, PREVIOUS_CHAMPION)
        print(f"Retour arrière : champion = version {previous[0]} (était {current[0] if current else 'aucune'})")
        print("Relivrer ensuite le champion à l'API (voir README, section S4)")
        return

    challenger = metrics_of(CHALLENGER)
    if challenger is None:
        raise SystemExit(f"Aucune version avec l'alias {CHALLENGER} : lancer `python -m velov.train --mlflow`")
    champion = metrics_of(CHAMPION)
    if champion is not None and champion[0] == challenger[0]:
        raise SystemExit(f"La version {challenger[0]} est déjà champion")

    decision = decide(challenger[1], champion[1] if champion else None)
    print(f"Challenger : version {challenger[0]}")
    print(f"Champion   : {f'version {champion[0]}' if champion else 'aucun'}")
    for reason in decision.reasons:
        print(f"  {reason}")
    if not decision.promote:
        raise SystemExit("Promotion refusée : le champion reste en place")
    if args.dry_run:
        print("Promotion possible (--dry-run : registre inchangé)")
        return
    if champion is not None:
        client.set_registered_model_alias(REGISTERED_MODEL, PREVIOUS_CHAMPION, champion[0])
    client.set_registered_model_alias(REGISTERED_MODEL, CHAMPION, challenger[0])
    print(f"Version {challenger[0]} promue {CHAMPION}")


if __name__ == "__main__":
    main()
