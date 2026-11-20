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
| `s2-end` | Image Docker multi-stage, compose API + PostgreSQL, journalisation des prédictions |
| `s3-end` | Pipeline : collecte bronze, dbt silver et gold sur DuckDB, contrat Pandera, flow Prefect |
| `s4-end` | Suivi MLflow, registre (alias champion / challenger), promotion, export, CI GitHub Actions |
| `s5-end` | Test de charge Locust, décomposition de la latence, export ONNX vérifié, quantization de DistilBERT |

```bash
git checkout s1-end          # récupérer l'état de fin de S1
git checkout -b mon-binome   # travailler sur sa propre branche
```

## Démarrage rapide

Prérequis : Python 3.11+ (3.12 recommandé), git, Docker Desktop ou Docker Engine.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .                     # rend le package velov importable

python -m velov.data                 # génère data/velov_history.csv (simulateur, seed fixe)
python -m velov.train                # entraîne et écrit models/model.joblib + models/metadata.json
python -m velov.train --mlflow       # idem + run tracé dans MLflow (voir « MLflow (S4) »)
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

## Exigences du projet

Le service doit respecter les exigences de [docs/exigences.md](docs/exigences.md), de S1 à S9.
Chaque rendu indique celles qu'il couvre et comment le vérifier.

## Docker (S2)

```bash
python -m velov.data && python -m velov.train   # l'image embarque models/ : il doit exister
docker build -t velov-api:0.1.0 .
docker run --rm -p 8000:8000 velov-api:0.1.0

cp .env.example .env                            # puis changer POSTGRES_PASSWORD
docker compose up --build -d
docker compose logs -f api
docker compose down                             # -v pour supprimer aussi le volume Postgres
```

## Pipeline de données (S3)

```bash
python -m velov.ingest --replay data/fixtures     # bronze de démonstration (48 h, 20 stations)
python -m velov.ingest                            # ou un instantané du vrai flux de la Métropole
cd velov_dbt && dbt build --profiles-dir . && cd ..   # silver et gold, tests et contrat
python -m velov.contract                          # contrat Pandera sur la table gold
python -m velov.train --data data/velov.duckdb --test-days 1
python -m velov.flow                              # les trois étapes orchestrées par Prefect
```

dbt est le seul écrivain de `data/velov.duckdb` : le reste ouvre la base en lecture seule.
Collecte continue sur la VM : voir l'en-tête de `src/velov/ingest.py` (crontab, toutes les 5 minutes).

## MLflow (S4)

```bash
# même PostgreSQL que l'API (service db), base dédiée "mlflow" ; aucun nouveau secret
docker compose -f compose.yaml -f compose.mlflow.yaml up -d --build  # http://<ip-vm>:5000
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000    # sans serveur : sqlite:///mlflow.db

python -m velov.train --version 0.1.0 --register    # run tracé + version "challenger"
python -m velov.promote                             # décision, sans rien changer
python -m velov.promote --apply                     # déplace l'alias "champion" si la règle est remplie
python -m velov.export --alias champion --out models/
./scripts/release.sh                                # sur la VM : export, build, redémarrage de l'API
```

La règle de promotion et la livraison du modèle sont justifiées dans `src/velov/promote.py`
et `docs/adr/0002-livraison-du-modele.md`. La CI est dans `.github/workflows/ci.yml`.

## Serving sous charge et optimisation (S5)

```bash
# depuis le POSTE, vers l'API sur la VM (jamais depuis la machine qui sert l'API)
pip install -r requirements-loadtest.txt
./loadtest/paliers.sh http://<ip-vm>:8000 w1      # échauffement, puis 10, 25, 50, 100, 150 usagers
python loadtest/summary.py results/w1 --markdown  # p50, p95, p99, débit, erreurs, capacité sous le SLO

# où passe le temps d'une prédiction (sans réseau)
python scripts/bench_predict.py --n 500 --batch 1 100 1000

# même modèle en ONNX : export refusé si une prédiction s'écarte de plus de 0,001 vélo
pip install -r requirements-optim.txt
python -m velov.export_onnx
MODEL_FORMAT=onnx uvicorn velov.api.main:app --host 0.0.0.0 --port 8000

# TP5a, en local : DistilBERT float32 contre int8 (taille, latence, précision sur SST-2)
python scripts/bench_quantization.py
```

SLO, capacité mesurée et critères de retour arrière : `docs/runbook.md` (EX-16, EX-18).
Choix du moteur d'inférence et son triplet mesuré : `docs/adr/0003-moteur-inference.md` (EX-17).

## Exploitation et monitoring (S6)

Un seul point d'entrée, en HTTPS (EX-19) ; le SLO suivi en continu, avec des alertes (EX-20) ; la dérive et
l'erreur réelle du modèle mesurées sur les prédictions journalisées (EX-21).

```bash
# sur la VM : certificat pour son adresse, identifiant de Prometheus, puis la stack complète
./scripts/make_cert.sh 192.168.1.2xx
# dans .env (voir .env.example) : COMPOSE_FILE, PUBLIC_HOST, GRAFANA_ADMIN_PASSWORD,
# PROMETHEUS_WEB_PASSWORD, MONITOR_DB_PASSWORD
./scripts/make_htpasswd.sh
docker compose up -d
./scripts/make_ro_role.sh            # rôle PostgreSQL en lecture seule pour velov.monitor

# depuis le poste : récupérer le certificat (jamais la clé), puis HTTPS seulement
scp <user>@192.168.1.2xx:~/velov-mlops/certs/velov.crt certs/
curl -s --cacert certs/velov.crt https://192.168.1.2xx/ready
#   Grafana    : https://192.168.1.2xx/grafana/     (admin)
#   Prometheus : https://192.168.1.2xx/prometheus/  (authentification basique)

# rejouer une semaine (normal, froid, demenagement), puis surveiller
pip install -r requirements-monitoring.txt
python -m velov.replay --url https://192.168.1.2xx --cacert certs/velov.crt --scenario normal
export MONITOR_DATABASE_URL="postgresql+psycopg://velov_ro:<MONITOR_DB_PASSWORD>@127.0.0.1:5432/velov"   # sur la VM
python -m velov.monitor --data data/velov_history.csv --out reports/     # code de sortie 1 si alerte
```

| Pièce | Fichier |
|---|---|
| Proxy TLS, Grafana sous `/grafana/`, Prometheus sous `/prometheus/` (authentifié) | `docker/nginx/velov.conf`, `compose.monitoring.yaml` |
| Métriques de l'API (seau à 0,2 s pour le SLI, `velov_model_info`) | `src/velov/api/main.py`, `tests/test_metrics.py` |
| SLI, LatenceHorsSLO, LatenceBurnRate, Erreurs5xx, ApiInjoignable | `docker/prometheus/alerts.yml` |
| Tableau de bord (débit, 5xx, SLI, modèle servi), provisionné | `docker/grafana/dashboards/velov.json` |
| Dérive (Evidently) et MAE face à la persistance, globale et par station | `src/velov/monitor.py`, `tests/test_monitor.py` |
| Conduite à tenir : alertes, dérive, sécurité | `docs/runbook.md` (sections 3, 7, 8, 9) |
| Lecture des semaines rejouées, seuils | `docs/model_card.md` (section 8) |

### Mesures en local (2026-11-20, VM injoignable)
Stack complète sur le poste (Windows 11, Docker Desktop, Compose 2.7), certificat pour `127.0.0.1`.

- **Proxy** : 301, 60, 200, 404, 502 pour les cinq appels de S6 · 09 ; ports 8000, 3000, 9090, 5000 et 5432
  refusés depuis l'adresse réseau du poste, 443 accepté ; Prometheus 401 sans identifiant.
- **Prometheus** : cible `velov-api` UP ; `promtool check rules` : 7 règles valides ; SLI 0,98 sous charge
  légère, 0,66 sous Locust à 10 usagers (toute la stack sur un poste) ; API arrêtée : ApiInjoignable `firing`.
- **Grafana** : tableau dans le dossier Velov, revenu après suppression du volume `grafanadata`.
- **Semaines rejouées** (même modèle 0.1.0, 3 340 prédictions jugées par semaine) :

| Semaine | Colonnes en dérive (score) | MAE | Persist. | Ratio | Alerte |
|---|---|---|---|---|---|
| Normale | temperature (0,39) | 1,309 | 1,821 | 0,72 | non |
| Froid, −15 °C | temperature (4,46) | 1,419 | 1,821 | 0,78 | non |
| Déménagement | temperature, bikes_available (0,25), occupancy_rate (0,30), predicted_bikes (0,26) | 1,584 | 1,818 | 0,87 | stations 2, 5, 8, 11, 14, 17, 20 |

Points relevés :
- Docker Compose 2.7 **ignore `ports: !reset []`** sans erreur (il faut 2.24) : le port 8000 restait ouvert sur
  toutes les interfaces. `compose.yaml` publie donc l'API sur `127.0.0.1` seulement, quelle que soit la version.
- La table `predictions` contenait 26 516 lignes aléatoires des tests de charge de S5, sur les mêmes dates
  que la semaine rejouée : `velov.monitor --depuis <début du rejeu>` les exclut sans les effacer.

## Structure

```
src/velov/
  data.py          simulateur de données (remplacé par l'open data en S3, même schéma)
  features.py      features partagées entraînement / API (anti training-serving skew)
  ingest.py        collecte du flux GBFS vers data/bronze, idempotente (S3)
  contract.py      contrat Pandera de la table d'entraînement (S3)
  flow.py          flow Prefect : ingestion, dbt, contrat (S3)
  train.py         entraînement, baseline, paquet model.joblib + metadata.json
  export_onnx.py   export ONNX vérifié ligne à ligne, servi avec MODEL_FORMAT=onnx (S5)
  replay.py        rejeu d'une semaine d'observations contre l'API (S6, fourni)
  monitor.py       dérive et erreur réelle sur les prédictions journalisées (S6)
  tracking.py      run MLflow, registre, alias (S4)
  promote.py       règle de promotion challenger / champion (S4)
  export.py        version du registre -> models/ avant le build (S4)
  retrain.py       réentraînement : entraîner, enregistrer, décider (S4)
  api/main.py      FastAPI : /health, /ready, /v1/model, /v1/predict, /v1/predict/batch
  api/schemas.py   contrat d'entrée / sortie (Pydantic)
  api/storage.py   journalisation des prédictions (S2)
tests/             pytest (features, API, ingestion, contrat, qualité du modèle, registre, ONNX)
loadtest/          test de charge Locust, paliers, résumé (S5)
scripts/           benchmarks de latence et de quantization (S5), release, fixtures
.github/workflows/ CI : lint, tests, scan de secrets, build et /ready de l'image (S4)
docs/              templates : model card, runbook, ADR
velov_dbt/         projet dbt : silver (stg_*), gold (station_hourly), tests, contrat (S3)
data/fixtures/     bronze de démonstration, régénérable avec scripts/make_fixtures.py (S3)
exercices/         démo pickle (S1), audit, debug et conformité Docker (S2), audit SQL (S3), audit d'un test de charge (S5)
```

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
