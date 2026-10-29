"""Livraison du champion du registre à l'API (S4, niveau Should).

Télécharge le paquet servi (model.joblib + metadata.json) du run de la version « champion »,
vérifie l'empreinte SHA-256 du modèle, ajoute à metadata.json la version du registre
(lue par /ready), puis écrit le tout dans le dossier de modèles de l'API.

Dépend seulement de MLflow et de la bibliothèque standard : il tourne dans l'image du serveur MLflow,
sur la VM, sans installer le projet (voir le service deliver de compose.champion.yaml).

Usage :
    python -m velov.deliver --out models/        # champion actuel
    python -m velov.deliver --out models/ --alias previous-champion
Puis redémarrer l'API, qui charge le modèle au démarrage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from velov.registry import CHAMPION, PACKAGE_DIR, REGISTERED_MODEL


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def deliver(out_dir: Path, alias: str = CHAMPION) -> dict:
    """Écrit dans out_dir le paquet de la version portant l'alias. Renvoie le metadata livré."""
    import mlflow
    from mlflow import MlflowClient

    mv = MlflowClient().get_model_version_by_alias(REGISTERED_MODEL, alias)
    with tempfile.TemporaryDirectory() as tmp:
        package = Path(mlflow.artifacts.download_artifacts(run_id=mv.run_id, artifact_path=PACKAGE_DIR, dst_path=tmp))
        metadata = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
        model_file = package / metadata["artifact"]["file"]
        # Un fichier corrompu ou remplacé ne doit jamais atteindre l'API.
        if sha256_of(model_file) != metadata["artifact"]["sha256"]:
            raise SystemExit(f"Empreinte SHA-256 invalide pour la version {mv.version} : livraison annulée")
        metadata["registry"] = {
            "model": REGISTERED_MODEL,
            "version": str(mv.version),
            "alias": alias,
            "run_id": mv.run_id,
        }

        out_dir.mkdir(parents=True, exist_ok=True)
        # Le modèle d'abord, metadata.json ensuite : l'API ne charge que ce que metadata.json désigne.
        shutil.copyfile(model_file, out_dir / model_file.name)
        (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Livre une version du registre MLflow dans le dossier de l'API.")
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--alias", default=CHAMPION)
    args = parser.parse_args()
    metadata = deliver(args.out, args.alias)
    reg = metadata["registry"]
    print(f"Livré : {reg['model']} version {reg['version']} ({reg['alias']}) -> {args.out}")
    print(f"MAE {metadata['metrics']['mae_model']} (baseline {metadata['metrics']['mae_baseline_persistence']})")


if __name__ == "__main__":
    main()
