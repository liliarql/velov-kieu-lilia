"""Surveillance du modèle servi : dérive et erreur réelle sur les prédictions journalisées (S6, EX-21).

Dans cet ordre :
    1. Référence : la première semaine de la fenêtre de test du modèle servi (metadata.json),
       avec les prédictions du modèle sur ces lignes. Ni elle ni la semaine rejouée n'ont servi
       à l'entraînement ; prendre les 14 jours mettrait dans la référence la semaine surveillée.
    2. Courant : les 7 derniers jours de la table predictions (comptés depuis le dernier
       observed_at, pas depuis maintenant), une ligne par station et par heure (la dernière
       prédiction faite si une heure a été rejouée deux fois).
    3. Dérive des entrées et des prédictions avec Evidently, rapport dans <out>/drift.html.
    4. MAE réelle face à la persistance, globale et par station : la vérité d'une prédiction
       faite à t est la disponibilité observée à t + 1 h, déjà dans la table.

Une dérive seule ne déclenche pas d'alerte (S6 · 23 : la semaine normale dérive déjà) : on
alerte sur l'erreur. Code de sortie 1 si le ratio modèle / persistance dépasse le seuil global,
ou si une station fait pire que la persistance ; 0 sinon ; 2 si la mesure est impossible.

Sécurité : l'URL de la base se lit dans MONITOR_DATABASE_URL (ou DATABASE_URL), pas en argument :
un argument se voit dans la liste des process (ps) et dans l'historique du shell. Utiliser le rôle
en lecture seule velov_ro (scripts/make_ro_role.sh), jamais le propriétaire de la base.
L'URL n'est jamais affichée.

Usage :
    export MONITOR_DATABASE_URL=postgresql+psycopg://velov_ro:$MONITOR_DB_PASSWORD@127.0.0.1:5432/velov
    python -m velov.monitor --data data/velov_history.csv --out reports/
    # ne garder que ce qui a été prédit après un instant (exclut un test de charge passé) :
    python -m velov.monitor --data data/velov_history.csv --depuis 2026-11-20T10:30:00Z
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, select

from velov.api.storage import predictions as predictions_table
from velov.features import FEATURES, TARGET, make_training_frame
from velov.train import METADATA_FILENAME, load_table

# Colonnes comparées entre référence et courant : les entrées qui bougent, et la sortie du modèle.
NUMERICAL = ["temperature", "bikes_available", "occupancy_rate", "predicted_bikes"]
CATEGORICAL = ["is_raining"]
WINDOW = pd.Timedelta(days=7)
TRUTH_LAG = pd.Timedelta(hours=1)

# Seuils par défaut, justifiés dans docs/model_card.md (section Monitoring).
SEUIL_GLOBAL = 0.9  # ratio MAE modèle / MAE persistance (0,71 à l'entraînement)
SEUIL_STATION = 1.0  # une station où le modèle fait pire que la persistance
MIN_STATION = 50  # paires minimum pour juger une station (sinon trop de bruit)


def load_reference(data_path: Path, model_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Première semaine de la fenêtre de test, avec les prédictions du modèle servi."""
    from velov.api.main import load_model  # vérifie l'empreinte du joblib avant de le charger

    model, metadata = load_model(model_dir)
    frame = make_training_frame(load_table(data_path))
    ts = frame["timestamp"]
    start = ts.max() - pd.Timedelta(days=metadata["params"]["test_days"])
    ref = frame[(ts > start) & (ts <= start + WINDOW)].copy()
    if ref.empty:
        raise ValueError(f"Fenêtre de référence vide dans {data_path} : sont-ce les données du modèle servi ?")
    ref["predicted_bikes"] = np.clip(model.predict(ref[FEATURES]), 0, ref["capacity"].to_numpy())
    ref["is_raining"] = ref["is_raining"].astype(int)
    ref["truth"] = ref[TARGET]
    return ref, metadata


def load_current(database_url: str, model_version: str, since: pd.Timestamp | None) -> pd.DataFrame:
    """Les 7 derniers jours journalisés pour cette version, dédoublonnés par station et heure."""
    t = predictions_table
    query = select(
        t.c.created_at,
        t.c.station_id,
        t.c.observed_at,
        t.c.capacity,
        t.c.bikes_available,
        t.c.temperature,
        t.c.is_raining,
        t.c.predicted_bikes,
    ).where(t.c.model_version == model_version)  # requête paramétrée : pas de SQL construit à la main
    if since is not None:
        query = query.where(t.c.created_at >= since.to_pydatetime())
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)
    finally:
        engine.dispose()
    if df.empty:
        return df
    for col in ("created_at", "observed_at"):
        df[col] = pd.to_datetime(df[col], utc=True)  # SQLite rend des dates sans fuseau
    df = df[df["observed_at"] > df["observed_at"].max() - WINDOW]
    # Une heure rejouée deux fois (autre scénario) : on garde la dernière prédiction faite.
    df = df.sort_values("created_at").drop_duplicates(["station_id", "observed_at"], keep="last")
    df["occupancy_rate"] = df["bikes_available"] / df["capacity"]
    df["is_raining"] = df["is_raining"].astype(int)
    # Vérité terrain : la disponibilité observée une heure plus tard, à la même station.
    truth = df[["station_id", "observed_at", "bikes_available"]].rename(columns={"bikes_available": "truth"})
    truth["observed_at"] = truth["observed_at"] - TRUTH_LAG
    return df.merge(truth, on=["station_id", "observed_at"], how="left").reset_index(drop=True)


def drift_scores(current: pd.DataFrame, reference: pd.DataFrame, html_path: Path | None) -> dict:
    """Score de dérive par colonne (Evidently 0.7), et si la colonne dérive selon sa méthode."""
    from evidently import DataDefinition, Dataset, Report
    from evidently.presets import DataDriftPreset

    cols = NUMERICAL + CATEGORICAL
    definition = DataDefinition(numerical_columns=NUMERICAL, categorical_columns=CATEGORICAL)
    snapshot = Report([DataDriftPreset()]).run(  # ordre : courant d'abord, référence ensuite
        Dataset.from_pandas(current[cols], data_definition=definition),
        Dataset.from_pandas(reference[cols], data_definition=definition),
    )
    if html_path is not None:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot.save_html(str(html_path))
    scores = {}
    for metric in snapshot.dict()["metrics"]:
        config = metric["config"]
        if not config.get("type", "").endswith("ValueDrift"):
            continue
        value, threshold, method = float(metric["value"]), float(config["threshold"]), config["method"]
        # Distance (Wasserstein, Jensen-Shannon) : dérive au-delà du seuil.
        # Test statistique (petits échantillons, « p_value ») : dérive sous le seuil.
        drifted = value < threshold if "p_value" in method else value >= threshold
        scores[config["column"]] = {"score": round(value, 3), "method": method, "drift": drifted}
    return scores


def performance(df: pd.DataFrame) -> dict:
    """MAE du modèle et de la persistance sur les mêmes lignes (celles qui ont leur vérité)."""
    judged = df.dropna(subset=["truth"])
    err_model = (judged["predicted_bikes"] - judged["truth"]).abs()
    err_persist = (judged["bikes_available"] - judged["truth"]).abs()
    mae_model, mae_persist = float(err_model.mean()), float(err_persist.mean())
    per_station = (
        pd.DataFrame({"station_id": judged["station_id"], "model": err_model, "persistance": err_persist})
        .groupby("station_id")
        .agg(n=("model", "size"), mae_model=("model", "mean"), mae_persistance=("persistance", "mean"))
    )
    per_station["ratio"] = per_station["mae_model"] / per_station["mae_persistance"]
    return {
        "n": len(judged),
        "mae_model": mae_model,
        "mae_persistance": mae_persist,
        "ratio": mae_model / mae_persist if mae_persist else float("nan"),
        "per_station": per_station.sort_values("ratio", ascending=False),
    }


def decide(perf: dict, seuil_global: float, seuil_station: float, min_station: int) -> list[str]:
    alerts = []
    if perf["ratio"] > seuil_global:
        alerts.append(f"ratio global {perf['ratio']:.2f} > {seuil_global}")
    stations = perf["per_station"]
    bad = stations[(stations["n"] >= min_station) & (stations["ratio"] > seuil_station)]
    for station_id, row in bad.iterrows():
        alerts.append(
            f"station {station_id} : MAE {row['mae_model']:.2f} contre persistance "
            f"{row['mae_persistance']:.2f} (ratio {row['ratio']:.2f} > {seuil_station})"
        )
    return alerts


def run(args: argparse.Namespace, database_url: str) -> int:
    reference, metadata = load_reference(args.data, args.model_dir)
    version = args.model_version or metadata["model_version"]
    since = pd.Timestamp(args.depuis) if args.depuis else None
    if since is not None and since.tzinfo is None:
        raise ValueError("--depuis doit porter un fuseau, ex. 2026-11-20T10:30:00Z")
    current = load_current(database_url, version, since)
    if current.empty:
        print(f"Aucune prédiction journalisée pour le modèle {version} : rien à surveiller.")
        return 2

    drift = drift_scores(current, reference, args.out / "drift.html" if args.out else None)
    perf = performance(current)
    perf_ref = performance(reference)
    alerts = decide(perf, args.seuil_global, args.seuil_station, args.min_station)

    print(
        f"Modèle {version} · référence {len(reference)} · courant {len(current)} "
        f"({current['observed_at'].min():%Y-%m-%d %H:%M} → {current['observed_at'].max():%Y-%m-%d %H:%M} UTC)"
    )
    print("\nDérive (score par colonne, seuil de la méthode)")
    for col, d in drift.items():
        print(f"  {col:<16} {d['score']:>6.3f}  {'DÉRIVE' if d['drift'] else ''}")
    print(f"\nPerformance sur {perf['n']} prédictions")
    print(f"  MAE modèle       {perf['mae_model']:.3f}")
    print(f"  MAE persistance  {perf['mae_persistance']:.3f}  (ratio {perf['ratio']:.2f})")
    print(f"  Ratio sur la référence : {perf_ref['ratio']:.2f}")
    print("\nStations les plus mal prédites (ratio au-dessus de 1 : pire que la persistance)")
    print(perf["per_station"].head(5).round(2).to_string())
    print()
    print("\n".join(f"ALERTE : {a}" for a in alerts) if alerts else "Pas d'alerte de performance")

    if args.out:
        summary = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model_version": version,
            "since": since.isoformat() if since is not None else None,
            "n_reference": len(reference),
            "n_current": len(current),
            "current_from": current["observed_at"].min().isoformat(),
            "current_to": current["observed_at"].max().isoformat(),
            "drift": drift,
            "performance": {k: round(perf[k], 3) for k in ("mae_model", "mae_persistance", "ratio")} | {"n": perf["n"]},
            "reference_ratio": round(perf_ref["ratio"], 3),
            "per_station": perf["per_station"].round(3).reset_index().to_dict(orient="records"),
            "thresholds": {"global": args.seuil_global, "station": args.seuil_station, "min_station": args.min_station},
            "alerts": alerts,
        }
        (args.out / "monitor.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if alerts else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Dérive et erreur réelle du modèle servi.")
    parser.add_argument("--data", type=Path, required=True, help="données du modèle servi (data.path de /v1/model)")
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--model-version", help="défaut : celle de metadata.json")
    parser.add_argument("--out", type=Path, default=Path("reports"), help="drift.html et monitor.json")
    parser.add_argument("--depuis", help="garder les prédictions faites après cet instant (ISO 8601, avec fuseau)")
    parser.add_argument("--seuil-global", type=float, default=SEUIL_GLOBAL)
    parser.add_argument("--seuil-station", type=float, default=SEUIL_STATION)
    parser.add_argument("--min-station", type=int, default=MIN_STATION)
    args = parser.parse_args()

    database_url = os.getenv("MONITOR_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("définir MONITOR_DATABASE_URL (rôle en lecture seule velov_ro), voir l'en-tête du module")
    if not (args.model_dir / METADATA_FILENAME).exists():
        parser.error(f"{args.model_dir / METADATA_FILENAME} introuvable")
    sys.exit(run(args, database_url))


if __name__ == "__main__":
    main()
