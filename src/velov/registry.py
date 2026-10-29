"""Noms partagés du registre MLflow (S4).

Module sans dépendance lourde : importé par l'entraînement (tracking), la promotion et la livraison,
y compris dans l'image MLflow qui n'embarque ni scikit-learn ni pandas.
"""

EXPERIMENT = "velov-availability"
REGISTERED_MODEL = "velov-availability"
CHALLENGER = "challenger"
CHAMPION = "champion"
# Champion remplacé lors de la dernière promotion : la cible d'un retour arrière.
PREVIOUS_CHAMPION = "previous-champion"
METRICS = ("mae_model", "mae_baseline_persistence", "mae_model_peak_hours")
PACKAGE_DIR = "package"  # artefacts du run : le paquet servi par l'API (model.joblib + metadata.json)
