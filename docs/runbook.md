# Runbook : API velov-availability

> Template à compléter au fil des sessions. Public cible : une personne d'astreinte
> qui ne connaît pas le projet et doit rétablir le service à 3 h du matin.

## 1. Vue d'ensemble
- Ce que fait le service, en deux phrases :
- Schéma d'architecture (lien) :
- Dépendances (base de données, stockage du modèle...) :

## 2. Démarrer / arrêter
```bash
# démarrer
# arrêter
# redémarrer un seul service
```

## 3. Vérifier que tout va bien
| Vérification | Commande | Résultat attendu |
|---|---|---|
| Le process répond | `curl -s http://<hôte>:8000/health` | `{"status":"ok"}` |
| Le modèle est chargé | `curl -s http://<hôte>:8000/ready` | HTTP 200 + version |
| | | |

## 4. Incidents connus
| Symptôme | Cause probable | Diagnostic | Action |
|---|---|---|---|
| `/ready` renvoie 503 | | | |
| Le conteneur redémarre en boucle | | | |
| Latence élevée | | | |

## 5. Déployer une nouvelle version du modèle
1.
2.
3. Retour arrière (rollback) :

## 6. Contacts et escalade
-

## 7. Pipeline de données (S3)

Services : `collector` (ingestion bronze toutes les 15 min) et `dbt` (à la demande, profil `tools`).

### Vérifier que la collecte tourne
| Vérification | Commande | Résultat attendu |
|---|---|---|
| Le conteneur tourne | `docker compose ps collector` | `Up` |
| Les passages réussissent | `docker compose logs --tail 20 collector` | `station_status : 466 reçus, 466 nouveaux` toutes les 15 min |
| Bronze est frais | `docker compose run --rm dbt source freshness` | `PASS` (avertissement au-delà d'1 h, erreur au-delà de 6 h) |

### La collecte s'est arrêtée
1. `docker compose logs --tail 100 collector` : lire la cause (API injoignable, base indisponible, `DATABASE_URL`).
2. Conteneur arrêté : `docker compose up -d collector`. Il redémarre seul (`restart: unless-stopped`) sauf arrêt manuel.
3. API GBFS en panne : rien à faire côté projet, la boucle réessaie à chaque passage. Vérifier avec
   `curl -s https://download.data.grandlyon.com/files/rdata/jcd_jcdecaux.jcdvelov/gbfs.json`.
4. Après redémarrage : la météo des 2 derniers jours est rattrapée automatiquement ; les **disponibilités des heures
   manquées sont perdues** (la source ne publie que l'état courant). Noter la période manquante ici :

| Période manquante (UTC) | Cause | Noté par |
|---|---|---|
| | | |

### Rejouer une journée
Bronze est la source de vérité, silver et gold en sont dérivés. Rejouer = reconstruire :
```bash
docker compose run --rm dbt build                       # reconstruit silver + gold et lance les tests
docker compose exec db psql -U velov -d velov -c   "select count(*) from gold.gold_station_hourly where timestamp >= '2026-10-26' and timestamp < '2026-10-27';"
```
Deux builds successifs donnent la même table gold (EX-10). Relancer l'ingestion pour une journée passée est inutile :
l'API ne renvoie que l'état courant, et un second passage n'insère aucun doublon.

### `dbt build` échoue
| Symptôme | Cause probable | Action |
|---|---|---|
| `violates check constraint` ou `not-null` sur `gold_station_hourly` | Relevé incohérent dans le flux | Gold n'est pas publié, l'ancienne table reste (EX-12). Identifier la ligne dans `silver.silver_station_status`, corriger le filtre silver si c'est un nouveau cas |
| `bikes_within_capacity` en échec | Plus de 1 % des relevés au-dessus de la capacité | Vérifier les capacités dans `silver.silver_stations` (changement de station ?) |
| `relation "bronze.station_status" does not exist` | Aucune ingestion n'a encore tourné | `docker compose run --rm collector python -m velov.ingest` |
