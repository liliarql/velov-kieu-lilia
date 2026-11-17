# Runbook : API velov-availability

## 1. Périmètre

L'API prédit la disponibilité des vélos à H+1. L'image embarque le modèle et ses
métadonnées ; PostgreSQL journalise les prédictions. MLflow n'est pas nécessaire
au serving et reste arrêté pendant les mesures TP5.

Ce runbook concerne les mesures **locales Windows/WSL2 et Docker Desktop**, avec
8 CPU visibles et 7,61 Gio de RAM Docker. La capacité n'a pas été validée sur VM.
Consulter les [protocoles et résultats TP5](../README.md#performance-tp5).
Exécuter les commandes depuis la racine du dépôt avec le contexte Docker local.
Prérequis : `.env` contenant `POSTGRES_PASSWORD` et image avec un modèle valide.

## 2. Démarrer, arrêter et changer de configuration

Configuration w1, un worker et quota de 1 CPU :

```bash
docker compose up -d api
docker update --cpus 1 "$(docker compose ps -q api)"
curl -i http://localhost:8000/ready
```

Configuration w2, deux workers et quota de 2 CPU, avec la même image et le même modèle :

```bash
docker compose -f compose.yaml -f compose.workers.yaml up -d api
docker update --cpus 2 "$(docker compose ps -q api)"
curl -i http://localhost:8000/ready
```

Pour revenir de w2 à w1, réexécuter les commandes w1. Après toute recréation de
conteneur, réappliquer le quota CPU et vérifier la configuration effective :

```bash
docker inspect --format '{{json .Config.Cmd}}' "$(docker compose ps -q api)"
docker inspect --format '{{.HostConfig.NanoCpus}}' "$(docker compose ps -q api)"
```

Attendu : un worker par défaut en w1, `--workers 2` en w2 ; `NanoCpus` vaut
respectivement `1000000000` et `2000000000`.
Si l'image n'existe pas, produire le modèle avant le build (README), puis lancer
`docker compose up -d --build api`. Ne pas reconstruire ou réentraîner entre les
deux configurations d'une comparaison.

Avant les mesures, arrêter MLflow sans arrêter PostgreSQL :

```bash
docker compose -f compose.yaml -f compose.mlflow.yaml stop mlflow
docker compose -f compose.yaml -f compose.mlflow.yaml ps -a mlflow
```

MLflow doit être arrêté. Pour arrêter seulement l'API : `docker compose stop api`.
Ne pas utiliser `docker compose down -v` pour un incident : cela efface les volumes.

## 3. SLO et capacité mesurée (EX-16)

SLO : **p95 ≤ 200 ms sur `POST /v1/predict`**, avec moins de 1 % d'erreurs pour
qualifier un palier, conformément au script de résumé. L'essai initial de préparation
doit présenter 0 % d'erreurs. Exclure `/health` des statistiques de prédiction.

| Configuration | Meilleur débit observé sous SLO | Usagers | p95 | Erreurs | Premier palier hors SLO |
|---|---:|---:|---:|---:|---|
| w1 : 1 worker / 1 CPU | 21,7 req/s | 25 | 100 ms | 0 % | 50 usagers : 800 ms |
| w2 : 2 workers / 2 CPU | 21,7 req/s | 25 | 91 ms | 0 % | 50 usagers : 670 ms |

Campagnes du 17 novembre 2026 : échauffement séparé de 1 minute, puis cinq paliers
10/25/50/100/150 usagers de 2 minutes chacun. Ces valeurs sont les meilleurs paliers
mesurés sous SLO, pas un plafond exact ni une garantie de production. Les 42,5 req/s
observées à 150 usagers en w1 ont un p95 de 2 700 ms et ne sont pas une capacité sous SLO.

Deux workers ne doublent pas la capacité observée. En w2, les relevés CPU API aux
paliers 100 et 150 sont de 197,90 % et 199,13 %, avec 477,6 et 480,1 MiB de mémoire.
Ajouter un worker ne constitue donc pas une solution validée à la latence sur ce poste.

## 4. Surveillance et actions près du plafond

| Vérification | Commande | Résultat attendu |
|---|---|---|
| Processus vivant | `curl -i http://localhost:8000/health` | HTTP 200 |
| Modèle chargé | `curl -i http://localhost:8000/ready` | HTTP 200 et version attendue |
| Identité du modèle | `curl -s http://localhost:8000/v1/model` | Version et SHA-256 de la campagne |
| CPU et mémoire | `docker stats --no-stream` | Interpréter selon le quota et la charge |
| Erreurs API/DB | `docker compose logs --tail=100 api db` | Pas d'erreur de chargement ou de journalisation |

Seuils opérationnels proposés pour ce poste, distincts des résultats mesurés :

- **Préalerte** vers 17 req/s (80 % du débit sous SLO observé) ou p95 ≥ 150 ms :
  examiner CPU, mémoire, logs et évolution du trafic ; différer les charges non urgentes.
  Cette marge est un choix d'exploitation, pas un palier supplémentaire mesuré.
- **SLO dépassé** si p95 > 200 ms ou erreurs ≥ 1 % sur la fenêtre observée :
  réduire temporairement le trafic à la source et enregistrer les métriques.
  Le service actuel ne possède pas de limiteur automatique de débit.
- Vérifier les ressources du générateur Locust pendant les tests et arrêter les
  services inutiles. Reprendre le même protocole pour confirmer une capacité.
- Si la dégradation suit le passage à w2, revenir à w1 avec les commandes de la
  section 2, puis vérifier `/ready` et une prédiction. Garder modèle et DB inchangés.

Le principal coût mesuré parmi les quatre étapes de `_predict()` est la prédiction
(p50 4,373 ms), suivie des features (2,869 ms). Ce microbenchmark exclut HTTP et DB :
il ne prouve pas à lui seul la cause de toute la saturation du service.

## 5. Incidents

| Symptôme | Diagnostic | Action |
|---|---|---|
| `/ready` renvoie 503 | Lire les logs API, vérifier modèle, metadata et versions | Restaurer un couple modèle/metadata valide et reconstruire l'image si nécessaire |
| Conteneur arrêté ou redémarrages | `docker compose ps -a` et logs | Corriger la cause, relancer et vérifier `/ready` |
| Latence élevée sans erreur HTTP | Examiner p95, CPU, RAM, charge DB | Appliquer la section 4 ; HTTP 200 seul ne prouve pas le respect du SLO |
| Erreur de journalisation | Logs API/DB et santé de `db` | Rétablir PostgreSQL ; l'API peut prédire malgré des traces manquantes |

## 6. Refaire les mesures

Locust tourne hors image API, dans le venv du poste. Employer un préfixe neuf pour
chaque campagne et noter le commit, la configuration et l'heure :

```bash
source .venv/bin/activate
bash loadtest/paliers.sh http://localhost:8000 nouvelle-campagne
python loadtest/summary.py results/nouvelle-campagne --markdown
```

Conserver des ressources CPU pour Locust et relever son utilisation pendant les
paliers. Une campagne sur VM sera documentée séparément, avec Locust sur le poste
et l'API sur la VM ; la capacité locale ne lui est pas directement transférable.
