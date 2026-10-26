# velov-mlops

Projet fil rouge du cours **Industrialisation de l'IA dans le Cloud** (M2 Data Engineering / IA).

Objectif métier : prédire, pour chaque station Vélo'v, le nombre de vélos disponibles **dans une heure**,
et servir cette prédiction via une API fiable, conteneurisée, puis déployée (on-premise et cloud).

## Progression par session

Chaque étape du cours correspond à un tag git. Absent ou bloqué : repartez du tag de fin de la session précédente.

| Tag | Contenu |
|---|---|
| `s1-start` | Données simulées, features, entraînement. API à compléter (TP1) |
| `s1-end` | Modèle v0 servi par FastAPI, tests pytest |

```bash
git checkout s1-end          # récupérer l'état de fin de S1
git checkout -b mon-binome   # travailler sur sa propre branche
```

## Démarrage rapide

Prérequis : Python 3.11+ (3.12 recommandé), git. Docker sera nécessaire en S2.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .                     # rend le package velov importable

python -m velov.data                 # génère data/velov_history.csv (simulateur, seed fixe)
python -m velov.train                # entraîne et écrit models/model.joblib + models/metadata.json
python -m velov.train --mlflow       # idem + journalisation MLflow (puis : mlflow ui)
pytest                               # tests
uvicorn velov.api.main:app --reload  # API sur http://127.0.0.1:8000/docs (--reload : en dev uniquement)
```

Exemple d'appel :

```bash
curl -X POST http://127.0.0.1:8000/v1/predict \
  -H "Content-Type: application/json" \
  -d '{"station_id": 3, "timestamp": "2026-10-06T08:00:00+02:00", "capacity": 20,
       "bikes_available": 12, "temperature": 14.5, "is_raining": false}'
```

Le `timestamp` doit porter un fuseau (`+02:00`, `Z`...) : sans fuseau, l'API répond 422.
Les instants sont renvoyés et journalisés en UTC (`"target_timestamp": "2026-10-06T07:00:00Z"`).

## Pipeline de données (S3)

Les données réelles remplacent le simulateur : disponibilités Vélo'v (flux GBFS du Grand Lyon) et météo (Open-Meteo).

```
GBFS Vélo'v ─┐                         dbt (pipeline/)
             ├─ velov.ingest ─► bronze ───► silver ───► gold.gold_station_hourly ─► velov.train --source gold
Open-Meteo ──┘  (collector, 15 min)   JSON brut   typé       contrat enforced           contrat Pandera
```

| Couche | Contenu | Garantie |
|---|---|---|
| bronze | Payload JSON tel que reçu (`bronze.station_status`, `station_information`, `weather_hourly`) | Idempotent : clé naturelle + `ON CONFLICT DO NOTHING` (EX-10) |
| silver | Vues typées : relevés, versions des fiches stations, référentiel, météo horaire | Tests `not_null`, `unique` |
| gold | Une ligne par station et par heure, au schéma `RAW_COLUMNS` de l'API | Contrat dbt `enforced` + contraintes (EX-12), point-in-time (EX-11), tests `not_null`, `relationships`, capacité avec seuil de 1 % |

Depuis un clone propre (`.env` créé à partir de `.env.example`) :

```bash
docker compose up -d db collector                       # collecte toutes les 15 min
docker compose run --rm collector python -m velov.ingest # un passage immédiat (sinon, attendre le premier)
docker compose run --rm dbt build                       # silver + gold + tests
docker compose run --rm dbt source freshness            # alerte si la collecte s'est arrêtée
```

Gold reste vide tant qu'aucune heure complète n'a été collectée (la ligne datée H attend la fin de l'heure H-1).
Entraîner sur gold (environnement de développement, `pip install -r requirements-dev.txt`, base joignable) :

```bash
DATABASE_URL=postgresql://velov:<mot de passe>@<hôte>:5432/velov python -m velov.train --source gold
```

Choix d'architecture : [ADR-0002](docs/adr/0002-batch-micro-batch-streaming.md). Exploitation : [runbook](docs/runbook.md), section 7.

## Exigences du projet

Le service doit respecter les exigences de [docs/exigences.md](docs/exigences.md), de S1 à S9.
Chaque rendu indique celles qu'il couvre et comment le vérifier.


## Structure

```
src/velov/
  data.py          simulateur de données (même schéma que la table gold)
  ingest.py        ingestion bronze : GBFS Vélo'v + Open-Meteo -> PostgreSQL (S3)
  contract.py      contrat Pandera de la table gold, vérifié avant l'entraînement (S3)
  features.py      features partagées entraînement / API (anti training-serving skew)
  train.py         entraînement, baseline, artefact + metadata.json, option MLflow
  api/main.py      FastAPI : /health, /ready, /v1/model, /v1/predict, /v1/predict/batch
  api/schemas.py   contrat d'entrée / sortie (Pydantic)
pipeline/          projet dbt : modèles silver et gold, contrat, tests (S3)
tests/             pytest (features, API, contrat)
docs/              templates : model card, runbook, ADR
exercices/         démo pickle (S1)
```

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
