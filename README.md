# TP3 - Pipeline de donnees Velo'v

Pipeline ELT local : GBFS + Open-Meteo -> Bronze JSON -> Silver dbt -> candidat Gold -> tests -> Gold public DuckDB -> Pandera -> entrainement H+1.

## Installation depuis une copie propre

Environnement de reference : **Linux/WSL, Python 3.14.4**. Le projet declare Python >=3.11 ; les autres versions ne sont pas encore validees pour ce TP3. Docker n'est pas necessaire au TP3.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e .
python -m pip check
dbt --version
```

`requirements.txt` contient le serving, `requirements-pipeline.txt` les versions du pipeline ; `requirements-dev.txt` inclut les deux, pytest, Ruff et MLflow. Le serving peut importer ses utilitaires sans installer DuckDB/Pandera.

## Pipeline : demonstration reproductible

Le Bronze versionne est dans `data/demo/bronze/` : 19 fichiers reels archives, **36 lignes Gold et une seule heure** au cutoff ci-dessous. Il permet de verifier le pipeline, pas d'entrainer/evaluer H+1. Le jeu pedagogique de 48 h mentionne dans les notes du cours a ete retrouve sur le depot enseignant ; ce petit jeu reel ne lui est pas equivalent. La provenance et les differences de format/disponibilite sont expliquees dans `docs/audit-s3.md`.

Ne pas melanger la demonstration et la collecte reelle. Depuis la racine :

```bash
source .venv/bin/activate
export VELOV_DB_PATH='../data/demo.duckdb'
cd velov_dbt
dbt debug --profiles-dir .
dbt build --profiles-dir . \
  --vars '{"as_of":"2026-10-26T16:20:00+01:00","bronze_dir":"../data/demo/bronze"}'
cd ..
python -m velov.train --data data/demo.duckdb --validate-only
unset VELOV_DB_PATH
```

Le chemin du profil est relatif a `velov_dbt/`. La variable `bronze_dir` peut aussi etre absolue. Le cutoff `as_of` est explicite et les calculs DuckDB sont en UTC.

Les resultats verifies et les limites sont dans [docs/validation-tp3.md](docs/validation-tp3.md).

## Demo synthetique 48 h : pipeline complet et training H+1

Cette simulation deterministe reutilise le simulateur du projet : 20 stations, 48 heures, graine 42. Les heures de disponibilite sont des **hypotheses de simulation** explicites, pas des collectes historiques. Elle reste dans `data/demo48h/`, separee des archives reelles. Le manifeste indique la source synthetique et le cutoff.

```bash
# Racine du depot, environnement active
python -m velov.demo --out data/demo48h
export VELOV_DB_PATH='../data/demo48h/demo.duckdb'
cd velov_dbt
dbt build --profiles-dir . \
  --vars '{"as_of":"2026-10-06T23:00:00Z","bronze_dir":"../data/demo48h/bronze"}'
cd ..
python -m velov.train --data data/demo48h/demo.duckdb --validate-only
python -m velov.train --data data/demo48h/demo.duckdb --out data/demo48h/models --test-days 1
unset VELOV_DB_PATH
```

Resultat attendu : 960 lignes Gold, 48 heures, 940 exemples H+1 avant split (460 train, 480 test). Les artefacts de demonstration sont `data/demo48h/models/model.joblib` et `metadata.json`. Ils restent ignores par Git. Deux jours permettent de verifier le fonctionnement, pas d'etablir une performance de production ou de reserver 14 jours de test. La generation refuse de remplacer un fichier different ; relancer avec la meme version/graine garde exactement le meme contenu.

## Collecte reelle et recalcul

GBFS : https://download.data.grandlyon.com/files/rdata/jcd_jcdecaux.jcdvelov/gbfs.json ; feeds `station_status` et `station_information`. Meteo : https://api.open-meteo.com/v1/forecast, temperature et pluie courantes a Lyon.

```bash
# Racine du depot, environnement active
python -m velov.ingest
python -m velov.weather_ingest
cd velov_dbt
dbt build --profiles-dir . --vars '{"as_of":"2026-10-29T12:00:00Z"}'
cd ..
python -m velov.train --data data/velov.duckdb --validate-only
# Seulement apres avoir collecte assez d'historique :
python -m velov.train --data data/velov.duckdb --out models/ --test-days 14
```

Adapter le cutoff a la periode voulue. Sans `as_of`, dbt utilise le debut du run. La collecte GBFS refuse un `last_updated` absent/invalide avant d'ecrire. Un snapshot valide rejoue a l'identique garde le meme nom (heure source + SHA-256 du JSON canonique). Les publications Bronze utilisent un fichier temporaire puis un lien atomique. La meteo conserve sa premiere date de collecte ; rejouer les memes valeurs ne la reecrit pas.

**VM : hors de cette correction, indisponible et non verifiee.** Aucune collecte automatique distante n'est annoncee comme operationnelle.

## Contrat et publication Gold

| Colonne | Type | Regle |
|---|---|---|
| station_id | INTEGER | > 0 |
| timestamp | TIMESTAMPTZ | UTC, cle avec station_id |
| capacity | INTEGER | 1 a 80 |
| bikes_available | INTEGER | 0 a capacity |
| temperature | DOUBLE | -25 a 50 |
| is_raining | BOOLEAN | non null |

Les cinq modeles dbt sont les trois stagings sources, `stg_status_checked` (ASOF capacity connue a l'heure de prediction) et `station_hourly`.

**`ref('station_hourly')` designe physiquement `main.station_hourly_candidate`.** Le contrat dbt enforced porte sur ce candidat. Les tests Silver detectent les anomalies avant Gold, avec **`error_if='>0'`** : une anomalie suffit a bloquer. `store_failures` conserve les lignes dans `main_dbt_test__audit` ; le log donne le nombre d'erreurs. Ce choix strict evite de masquer des erreurs dans un petit jeu d'apprentissage. Aucun seuil de 10 n'est impose par le sujet.

Le hook `publish_gold` n'execute `CREATE OR REPLACE TABLE main.station_hourly AS SELECT ...` qu'apres un **`dbt build` complet dont tous les modeles et tests ont reussi**. La copie est une instruction transactionnelle DuckDB. Echec source, schema, type, test Gold ou build partiel : Gold precedent conserve ; sans version precedente, aucune table publique creee. Le candidat reste un artefact interne, qui peut etre invalide apres `dbt run`.

Les lecteurs (dont `train.py`) utilisent exclusivement **`main.station_hourly`**. `dbt run`, `dbt test` et les selections partielles ne publient pas. `dbt show --select station_hourly` concerne le candidat, pas la copie publique. Pour lire Gold public :

```bash
python -c "import duckdb; c=duckdb.connect('data/demo.duckdb', read_only=True); print(c.sql('select * from main.station_hourly limit 10'))"
```

Les regles SQL et Pandera sont alignees. L'entrainement valide avant de calculer les features. Une cible n'est gardee que si l'observation suivante est exactement H+1 (heures UTC ecoulees), y compris au changement d'heure.

## Verification EX-10, EX-11, EX-12

```bash
python -m pytest -q
python -m ruff check src tests
# Preuves dbt seules, sur copies temporaires et sans API reseau :
python -m pytest tests/test_pipeline.py -q
# Verification complete incluant les commandes du README sur base jetable :
bash scripts/verify_tp3.sh
```

- Bronze : replay identique -> un seul fichier ; timestamp invalide -> aucune ecriture.
- EX-10 : deux vrais builds, comparaison SHA-256 du contenu Gold trie, jamais du fichier DuckDB.
- EX-11 : ajout de corrections GBFS/meteo connues apres la prediction -> Gold passe identique.
- EX-12 : injections de valeurs invalides, schema/type incorrects -> echec et contenu Gold public inchange ; premier build invalide -> aucune publication.

[Exigences](docs/exigences.md), [runbook](docs/runbook.md), [ADR pipeline](docs/adr/0002-pipeline-micro-batch.md), [declaration IA et exercice S3](docs/audit-s3.md), [explication des modeles](docs/preparation-orale-tp3.md).

## API et Docker (TP1/TP2)

Le petit Bronze TP3 ne permet pas de produire un modele. Pour une demonstration API, generer explicitement des donnees **synthetiques** :

```bash
python -m velov.data --help
python -m velov.data --days 30 --out data/velov_history.csv
python -m velov.train --data data/velov_history.csv --out models/
uvicorn velov.api.main:app --host 127.0.0.1 --port 8000
# Autre terminal :
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

Pour Docker, creer `.env` local avec `POSTGRES_PASSWORD=<mot-de-passe-local>`, puis :

```bash
docker compose up --build -d
curl http://127.0.0.1:8002/ready
docker compose logs --tail=100 api db
docker compose down
```

Le modele et `metadata.json` doivent exister avant le build de l'image. Ne pas versionner `.env`. Le Dockerfile utilise Python 3.12 par defaut ; pour servir un artefact entraine avec l'environnement de reference, construire avec `docker compose build --build-arg PYTHON_IMAGE=python:3.14-slim`. Docker n'a pas ete revalide dans cette correction locale.

## Rendu TP3

Branche de rendu : `tp3-minh-dung` sur le fork `liliarql/velov-kieu-lilia`. Le tag annote `tp3` doit designer le commit final de cette branche. Verification des references :

```bash
git rev-parse HEAD
git rev-parse 'tp3^{commit}'
git ls-remote origin refs/heads/tp3-minh-dung refs/tags/tp3 'refs/tags/tp3^{}'
```

Verification depuis un clone independant :

```bash
git clone --branch tp3 https://github.com/liliarql/velov-kieu-lilia.git velov-tp3-check
cd velov-tp3-check
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e .
bash scripts/verify_tp3.sh
```

Le rapport `docs/validation-tp3.md` distingue les verifications effectuees, le jeu reel d'une heure et la simulation 48 h. VM indisponible : exclue de cette validation.
