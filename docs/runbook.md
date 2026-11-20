# Runbook : API velov-availability

> Public cible : une personne d'astreinte qui ne connaît pas le projet et doit rétablir le service à 3 h du matin.

## 1. Vue d'ensemble
- **Ce que fait le service** : prédit le nombre de vélos disponibles à H+1 pour chaque station Vélo'v, via une API REST (FastAPI/uvicorn).
- **Dépendances** : PostgreSQL 17 (journalisation des prédictions), modèle sklearn embarqué dans l'image Docker, MLflow (registre, non requis au runtime).

## 2. Démarrer / arrêter
```bash
# démarrer (API + PostgreSQL)
docker compose up -d --build

# arrêter
docker compose down          # -v pour supprimer aussi les données Postgres

# redémarrer un seul service
docker compose restart api
```

## 3. Vérifier que tout va bien
| Vérification | Commande | Résultat attendu |
|---|---|---|
| Le process répond | `curl -s http://<hôte>:8000/health` | `{"status":"ok"}` |
| Le modèle est chargé | `curl -s http://<hôte>:8000/ready` | HTTP 200 + version |
| Version du modèle servi | `curl -s http://<hôte>:8000/v1/model` | JSON avec model_version, run_id |
| Conteneurs en marche | `docker compose ps` | api et db : running/healthy |
| Logs de l'API | `docker compose logs -f api` | pas d'exception au démarrage |

## 4. Incidents connus
| Symptôme | Cause probable | Diagnostic | Action |
|---|---|---|---|
| `/ready` renvoie 503 | Modèle non chargé (fichier absent, empreinte invalide, erreur d'encodage) | `docker compose logs api` : chercher l'exception au démarrage | Vérifier `models/metadata.json` (UTF-8), `model.joblib` (SHA-256). Reconstruire l'image après correction. |
| Le conteneur redémarre en boucle | Crash du process uvicorn | `docker compose logs api --tail 50` | Identifier l'exception. Si OOM : vérifier la mémoire avec `docker stats`. |
| Latence élevée (p95 > 200 ms) | API saturée (CPU à 100 %) ou contention sur PostgreSQL | `docker stats --no-stream` : CPU de l'API. `curl -D - ... \| grep x-process-time` : temps dans l'API vs temps total. | Si CPU saturé : réduire la charge ou ajouter des cœurs (pas des workers, voir ci-dessous). Si X-Process-Time est bas mais latence Locust haute : file d'attente, les requêtes attendent leur tour. |

## 5. Déployer une nouvelle version du modèle
1. `python -m velov.export --alias champion --out models/`
2. `TAG="m${VERSION}-$(git rev-parse --short HEAD)" docker compose up -d --build api`
3. `curl -s http://<hôte>:8000/ready` → vérifier la nouvelle version
4. **Retour arrière** : redéployer l'image précédente, ou remettre l'alias `champion` sur `previous-champion` dans MLflow puis réexporter.

## 6. Performance et capacité (EX-16)

### SLO
- **95 % des requêtes POST /v1/predict sous 200 ms**, moins de 1 % d'erreurs.

### Capacité mesurée (mesure locale, 2026-11-17)
| Configuration | Capacité sous le SLO | Débit max |
|---|---|---|
| 1 worker, 1 cœur | ~21 req/s (25 usagers) | ~32 req/s |
| 2 workers, 2 cœurs | ~8 req/s (10 usagers) | ~22 req/s |

### Que faire près du plafond
1. **Lire X-Process-Time-Ms** : si proche du p50 Locust → le temps est dans le calcul (CPU-bound). Si très inférieur → file d'attente, les requêtes attendent.
2. **Ne pas ajouter de workers** : la contention sur PostgreSQL (journalisation synchrone) dégrade les performances avec plusieurs workers. Ajouter des cœurs à un seul worker n'aide pas non plus (GIL Python).
3. **Pistes d'amélioration** : journalisation asynchrone (BackgroundTasks), remplacement de pandas dans add_features, export ONNX du modèle.
4. **Scaling horizontal** : un reverse proxy devant N instances mono-worker, chacune sur son cœur, avec sa propre connexion PostgreSQL.

## 7. Contacts et escalade
- Binôme : Lilia (fork liliarql/velov-kieu-lilia)
- Cours : Mohamed Gassem
