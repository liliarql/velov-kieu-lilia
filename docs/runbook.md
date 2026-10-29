# Runbook - Pipeline et API Velo'v

## Reperes

Depuis la racine du depot sous Linux/WSL : `source .venv/bin/activate`. Les JSON immuables sont dans `data/bronze/`, la base de production locale dans `data/velov.duckdb`. La demonstration est separee (`data/demo/bronze`, `data/demo.duckdb`). Le pipeline depend des API seulement pendant la collecte ; le rejeu dbt travaille hors ligne.

`main.station_hourly` est le dernier Gold publie. `main.station_hourly_candidate` n'est pas une table a consommer. Ne jamais la renommer manuellement pour contourner un echec.

## Collecte arretee / reseau indisponible

1. Verifier les dates des fichiers : `find data/bronze -name '*.json' -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort | tail`.
2. Relancer `python -m velov.ingest` et `python -m velov.weather_ingest` ; capturer stdout/stderr dans les logs de l'ordonnanceur quand la VM sera disponible.
3. Timeout/HTTP : verifier DNS et accessibilite de la source, puis retenter. Un echec ne doit pas creer de fichier JSON incomplet.
4. `last_updated` absent/invalide : snapshot refuse, corriger la source ; ne pas inventer une heure et ne pas modifier un ancien Bronze pour le faire passer.
5. Une periode sans archive GBFS est perdue ; l'API courante ne reconstitue pas le passe. Ne pas combler avec des observations futures.

VM actuellement indisponible : ordonnanceur distant et survie apres fermeture SSH non verifies.

## Rejouer une journee archivee

Conserver les snapshots anterieurs a la journee : ils peuvent contenir la derniere capacity connue. Une replay dbt reconstruit l'historique jusqu'au cutoff choisi, sans telechargement ni effacement de Bronze. Exemple pour le 26 octobre 2026 UTC :

```bash
cd velov_dbt
dbt build --profiles-dir . --vars '{"as_of":"2026-10-27T00:00:00Z"}'
cd ..
python -m velov.train --data data/velov.duckdb --validate-only
```

Utiliser le meme cutoff et les memes archives pour comparer deux runs. `as_of` fixe l'heure limite des lignes Gold. Ce n'est pas une reconstruction des donnees manquantes.

Pour verifier le replay d'un fichier GBFS conserve :

```bash
python -m velov.ingest --replay <fichier.json> --feed station_status
# Refaire exactement la commande : log DEJA PRESENT, aucun nouveau fichier.
```

Pour la meteo, les archives enveloppees conservent `collected_at`. Rejouer via dbt ces archives telles quelles ; ne pas rappeler l'API pour simuler une collecte historique.

## Echec dbt / qualite

```bash
cd velov_dbt
dbt debug --profiles-dir .
dbt build --profiles-dir . --vars '{"as_of":"2026-10-27T00:00:00Z"}'
```

Lire `logs/dbt.log`, `target/run_results.json` et les fichiers de test compiles. La sortie fournit les nombres de violations. Les lignes rejetes sont consultables, par exemple :

```bash
cd ..
python -c "import duckdb; c=duckdb.connect('data/velov.duckdb', read_only=True); print(c.sql('select * from main_dbt_test__audit.silver_status_quality limit 20'))"
```

Seuil strict : aucune violation acceptee. Source absente, capacity invalide, schema/type incorrect, Gold vide : corriger la cause, puis relancer un build complet. Garder les archives fautives pour diagnostic ; si une correction du fournisseur arrive, documenter sa provenance. Ne pas changer un seuil pour obtenir du vert sans decision metier documentee.

Le hook ne publie pas apres un echec ou une selection partielle. L'ancienne table publique reste disponible. Au premier build en echec, il n'existe pas encore de Gold public. Les tests `tests/test_pipeline.py` prouvent les deux situations.

## DuckDB verrouille / ecritures concurrentes

Identifier le processus qui detient la base (`lsof data/velov.duckdb` si disponible). Attendre la fin du writer et fermer les sessions interactives avant de relancer. Ne jamais supprimer `.wal` ou la base pour retirer un verrou. Un seul writer a la fois ; les lecteurs d'entrainement ouvrent en lecture seule et peuvent devoir attendre la fin d'un build.

Pour les executions automatisees futures, faire passer tous les lanceurs par le meme verrou :

```bash
mkdir -p data
flock -n data/pipeline.lock bash -c 'python -m velov.ingest && python -m velov.weather_ingest && cd velov_dbt && dbt build --profiles-dir .'
```

## Exploiter l'API

Modele requis : `models/model.joblib` et `models/metadata.json`. Demarrer en local avec `uvicorn velov.api.main:app --host 127.0.0.1 --port 8000` ; arreter avec Ctrl-C. Docker : `docker compose up --build -d`, `docker compose restart api`, `docker compose down`.

`/health` retourne 200 si le processus repond ; `/ready` retourne 200 avec la version si le modele est charge, sinon 503. Ports : 8000 local, 8002 via compose. Consulter `docker compose logs --tail=100 api db`.

- `/ready` 503 : verifier presence du modele et SHA-256 dans les metadonnees, versions de Python/sklearn, logs de chargement. Reentrainer ou restaurer ensemble modele + metadonnees connus valides, reconstruire l'image.
- Base PostgreSQL indisponible : verifier `docker compose ps`, sante de `db`, mot de passe `.env` et logs. L'API peut predire mais journalise l'echec de tracabilite.
- Nouvelle version : garder une copie du couple modele/metadonnees precedent, reconstruire l'image, verifier `/ready` et une prediction. Retour arriere : restaurer ce couple et reconstruire l'image.

Responsables : binome du depot (coordonnees a renseigner par les etudiants).
