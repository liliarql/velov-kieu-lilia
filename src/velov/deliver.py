"""Exporte la version champion (ou autre alias) du registre vers models/ (S4).

Le registre décide QUEL modèle part en production (via un alias) ; l'image le livre.
L'API ne parle pas au serveur MLflow : elle charge models/ comme en S1-S2, et l'image
reste immuable. Revenir en arrière = redéployer l'image précédente.

Le paquet téléchargé est vérifié (SHA-256) puis complété par la référence au registre,
visible dans GET /v1/model.

Usage :
    python -m velov.deliver --alias champion --out models/
    python -m velov.deliver --alias previous-champion --out models/   # rollback
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import mlflow

from velov.registry import MODEL_NAME
from velov.tracking import version_by_alias
from velov.train import METADATA_FILENAME, sha256_of


def deliver(alias: str, out_dir: Path) -> dict:
    """Télécharge le paquet d'une version aliasée, vérifie l'empreinte, écrit dans out_dir."""
    version = version_by_alias(alias)
    if version is None:
        raise LookupError(f"Aucune version de {MODEL_NAME} ne porte l'alias {alias!r}")

    with tempfile.TemporaryDirectory() as tmp:
        local = Path(
            mlflow.artifacts.download_artifacts(
                run_id=version.run_id,
                artifact_path="package",
                dst_path=tmp,
            )
        )
        metadata = json.loads((local / METADATA_FILENAME).read_text(encoding="utf-8"))
        model_file = local / metadata["artifact"]["file"]
        if sha256_of(model_file) != metadata["artifact"]["sha256"]:
            raise RuntimeError(f"Empreinte invalide pour la version {version.version} : livraison refusée")

        metadata["registry"] = {
            "name": MODEL_NAME,
            "version": str(version.version),
            "alias": alias,
            "run_id": version.run_id,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(model_file, out_dir / model_file.name)
        (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporte une version du registre vers un dossier models/.")
    parser.add_argument("--alias", default="champion")
    parser.add_argument("--out", type=Path, default=Path("models"))
    args = parser.parse_args()
    try:
        metadata = deliver(args.alias, args.out)
    except (LookupError, RuntimeError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    r = metadata["registry"]
    print(f"{r['name']} v{r['version']} (@{r['alias']}, run {r['run_id'][:8]}) exporté dans {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
