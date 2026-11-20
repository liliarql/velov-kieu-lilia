# Runbook : API velov-availability

> Template à compléter au fil des sessions. Public cible : une personne d'astreinte
> qui ne connaît pas le projet et doit rétablir le service à 3 h du matin.

## 1. Vue d'ensemble
- Le service prédit le nombre de vélos disponibles dans 1 h, par station Vélo'v (`POST /v1/predict`).
  Chaque prédiction est journalisée dans PostgreSQL (table `predictions`) pour mesurer l'erreur réelle à H+1.
- Schéma : poste → **proxy Nginx (443, TLS)** → `api:8000` → `db` (PostgreSQL). Prometheus lit `api:8000/metrics`
  toutes les 15 s ; Grafana lit Prometheus ; `velov.monitor` lit la base avec un rôle en lecture seule.
- Dépendances : PostgreSQL (journalisation ; l'API sert sans lui), le modèle dans l'image (`models/`),
  MLflow (registre, pour livrer un nouveau modèle seulement).

## 2. Démarrer / arrêter
`.env` déclare `COMPOSE_FILE=compose.yaml:compose.mlflow.yaml:compose.monitoring.yaml` : toute commande
`docker compose` (release.sh compris) lance les trois fichiers, sans `-f`. Sur un poste Windows, ajouter
`COMPOSE_PATH_SEPARATOR=:` (le séparateur par défaut y est `;`).
```bash
docker compose up -d                 # démarrer toute la stack
docker compose down                  # arrêter (sans -v : les volumes pgdata, promdata, grafanadata restent)
docker compose restart api           # redémarrer un seul service
docker compose ps                    # état ; seul le proxy doit publier sur 0.0.0.0 (80, 443)
```
Première installation sur la VM : `./scripts/make_cert.sh <ip-vm>`, `./scripts/make_htpasswd.sh`, puis
`docker compose up -d`, puis `./scripts/make_ro_role.sh` (la base doit tourner).

## 3. Vérifier que tout va bien
Depuis le poste, avec le certificat de la VM. Jamais `curl -k` ni `verify=False` : ils acceptent n'importe quel
serveur, y compris un attaquant au milieu.
```bash
# récupérer le certificat (jamais la clé, qui ne quitte pas la VM)
mkdir -p certs
scp <user>@<ip-vm>:~/velov-mlops/certs/velov.crt certs/
openssl x509 -in certs/velov.crt -noout -ext subjectAltName -enddate   # l'IP de la VM, et la date d'expiration
VM=<ip-vm>
```

| Vérification (S6 · 09) | Commande | Résultat attendu |
|---|---|---|
| HTTP redirigé vers HTTPS | `curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://$VM/ready` | `301 https://<ip-vm>/ready` |
| Certificat refusé sans `--cacert` | `curl -sS https://$VM/ready` | `curl: (60)` (erreur de certificat) |
| Le modèle est chargé | `curl -s --cacert certs/velov.crt https://$VM/ready` | `{"status":"ready","model_version":"0.1.0"}` |
| Les métriques ne sortent pas | `curl -s --cacert certs/velov.crt -o /dev/null -w "%{http_code}\n" https://$VM/metrics` | `404` |
| API arrêtée, proxy vivant | `docker compose stop api`, puis l'appel `/ready` ; ensuite `docker compose start api` | `502` |
| Port de l'API fermé | `curl -m 5 http://$VM:8000/health` | échec de connexion |
| Prometheus protégé | `curl -s --cacert certs/velov.crt -o /dev/null -w "%{http_code}\n" https://$VM/prometheus/` | `401` (200 avec l'identifiant) |
| Grafana | navigateur : `https://<ip-vm>/grafana/` | page de connexion, puis le tableau « Vélo'v · API de prédiction » |

Mesuré en local le 2026-11-20 (VM injoignable, `VM=127.0.0.1`) : 301, 60, 200, 404, 502 ; Prometheus 401 sans
identifiant ou avec un mauvais, 200 avec le bon ; ports 8000, 3000, 9090, 5000 et 5432 refusés depuis l'adresse
réseau du poste, 443 accepté.

Le certificat expire au bout de 90 jours. Pour le renouveler : `./scripts/make_cert.sh <ip-vm>` puis
`docker compose restart proxy`, et redistribuer `velov.crt` aux clients.

## 4. Incidents connus
| Symptôme | Cause probable | Diagnostic | Action |
|---|---|---|---|
| `502` sur toutes les routes | L'API ne tourne pas ; le proxy, si | `docker compose ps api` ; `docker compose logs proxy` : `connect() failed (111: Connection refused)` | `docker compose start api` ; sinon section 7, ApiInjoignable |
| `504` | L'API répond plus lentement que `proxy_read_timeout` (10 s) | latence dans Grafana ; en-tête `X-Process-Time-Ms` | section 7, LatenceHorsSLO |
| Échec TLS côté client | Mauvais certificat, IP absente du SAN, ou certificat expiré | `openssl x509 -in certs/velov.crt -noout -ext subjectAltName -enddate` | recopier `velov.crt`, ou le régénérer (section 3) |
| `429` | Limite de débit du proxy dépassée (100 req/s par IP pour l'API, au-dessus de la capacité mesurée) | `docker compose logs proxy` | un client qui s'emballe ; ne relever la limite qu'après une mesure de capacité |
| `413` | Corps de requête au-delà de 1 Mo | `docker compose logs proxy` | un batch est limité à 1000 lignes (environ 150 Ko) : requête anormale |
| `/ready` renvoie 503 | Modèle absent ou empreinte invalide | `docker compose logs api` : « Empreinte invalide » ou « introuvable » | `./scripts/release.sh` (réexporte le champion) |
| Prometheus : cible DOWN, erreur 404 | Image de l'API sans instrumentateur | `curl -s 127.0.0.1:8000/metrics` sur la VM | `docker compose up -d --build api` |
| Le port 8000 répond depuis le réseau | Docker Compose < 2.24 ignore `!reset` | `docker compose version` ; `docker compose ps` | `compose.yaml` publie l'API sur `127.0.0.1` : vérifier que cette ligne est intacte |

## 5. Déployer une nouvelle version du modèle (EX-18)
Stratégie : **shadow, puis bascule**. Une seule VM, un seul conteneur API : pas de répartiteur
pour un canary. Le challenger est d'abord évalué sur le trafic réel, sans servir de réponse.

1. Shadow (24 h minimum, un jour de semaine compris) : les prédictions du challenger sont journalisées
   avec sa `model_version`, à côté de celles du champion ; MAE des deux à H+1, globale et en heures de pointe.
2. Bascule si les trois critères tiennent : MAE du challenger meilleure d'au moins 2 % (règle de promotion,
   S4), p95 de l'API sous 200 ms au palier de 50 usagers, aucune erreur 5xx pendant le test de charge.
   `python -m velov.promote --apply` puis `./scripts/release.sh` sur la VM.
3. Retour arrière, sans discuter, dans l'heure qui suit la bascule si : p95 au-dessus de 200 ms pendant
   5 minutes, taux de 5xx au-dessus de 1 %, ou `/ready` en 503.
   Commande : `./scripts/release.sh previous-champion` (ou relancer l'image précédente, déjà sur la VM).

## 6. Performance et capacité (EX-16)
- **SLO** : 95 % des requêtes `/v1/predict` en moins de 200 ms, moins de 1 % d'erreurs, mesuré par
  Locust depuis un poste du réseau de l'école (`loadtest/paliers.sh`, puis `loadtest/summary.py`).
- **Capacité mesurée** (machine de préparation, 1 worker, 1 cœur) : environ 41 req/s sous le SLO en
  joblib, environ 80 req/s en ONNX. Au-delà, la latence monte avant le débit : la file d'attente grossit.
- **Quand on approche du plafond** : passer à `--workers 2` si la VM a 2 cœurs libres (un worker par cœur,
  le modèle est chargé dans chaque worker), puis remesurer. Ajouter des workers sur un seul cœur n'apporte rien.
- **Symptôme « latence élevée »** : comparer l'en-tête `X-Process-Time-Ms` (temps dans l'API) à la latence
  vue par Locust. Proches : le calcul sature le CPU. Très différents : attente avant traitement ou réseau.

## 7. Alertes (EX-20)
Règles dans `docker/prometheus/alerts.yml`, état sur `https://<ip-vm>/prometheus/alerts` : `inactive`, `pending`
(condition vraie depuis moins longtemps que le `for`), `firing`. Toutes en gravité `page` : des utilisateurs
sont touchés maintenant.

| Alerte | Condition | Diagnostic | Action |
|---|---|---|---|
| **ApiInjoignable** | `up{job="velov-api"} == 0` pendant 1 min | `docker compose ps api`, `docker compose logs --tail 50 api` | `docker compose start api` ; en boucle de redémarrage : image précédente (section 5) |
| **LatenceHorsSLO** | SLI (part des `/v1/predict` sous 200 ms, 5 min) < 0,95 pendant 5 min | Débit dans Grafana : pic de trafic, ou API plus lente à trafic égal ? `X-Process-Time-Ms` proche de la latence : calcul ; très inférieur : file d'attente | Surcharge : section 6 (workers) ; juste après une livraison : retour arrière (section 5) |
| **LatenceBurnRate** | `(1 - SLI) / 0,05 > 14,4` sur 1 h **et** sur 5 min, pendant 2 min | Comme LatenceHorsSLO ; ici le budget d'erreur mensuel fond (2 % consommés en 1 h) | Même action, en priorité |
| **Erreurs5xx** | Plus de 1 % de 5xx sur `/v1/predict*` pendant 5 min | `docker compose logs api` : traces d'exception | Juste après une livraison : retour arrière ; sinon corriger la cause (modèle, entrée) |

Réglages et leur raison :
- **`for: 5m` sur la latence** : un pic qui se résorbe seul ne réveille personne, une surcharge qui dure, si.
  Mesuré en local le 2026-11-20 : SLI tombé à 0,66 sous Locust (toute la stack sur un seul poste),
  LatenceHorsSLO `pending` puis `firing` 5 min plus tard ; API arrêtée, ApiInjoignable `pending` 31 s après,
  `firing` 1 min plus tard.
- **Burn rate** : SLO de 95 % sur 30 jours, donc un budget de 5 %. Le facteur 14,4 consomme 2 % du budget en
  1 h (14,4 × 1 h / 720 h). Deux fenêtres : 1 h prouve que ça dure, 5 min que ça dure encore, et l'alerte
  s'éteint quelques minutes après le retour à la normale au lieu d'une heure.
- Les 4xx (erreurs de l'appelant) se suivent dans Grafana, sans alerte. La CPU non plus : une CPU à 90 %
  qui sert dans le SLO n'est pas un incident.
- Pas encore de notification (Alertmanager) : l'état se lit sur la page Alerts de Prometheus.

## 8. Dérive détectée (EX-21)
`velov.monitor` mesure la dérive (Evidently) et l'erreur réelle à H+1 face à la persistance, globale et par station.
```bash
# sur la VM : la base n'écoute que sur 127.0.0.1:5432 ; rôle en lecture seule velov_ro
pip install -r requirements-monitoring.txt
export MONITOR_DATABASE_URL="postgresql+psycopg://velov_ro:<MONITOR_DB_PASSWORD>@127.0.0.1:5432/velov"
python -m velov.monitor --data data/velov_history.csv --out reports/          # code de sortie 1 si alerte
python -m velov.monitor --data data/velov_history.csv --depuis <instant ISO>  # exclut un test de charge passé
```
L'URL de la base passe par une variable d'environnement, pas en argument (visible dans `ps` et l'historique).
`reports/drift.html` (près de 4 Mo) et `reports/monitor.json` ne se commitent pas.

Conduite à tenir (S6 · 24) :
1. **Dérive, erreur stable** (ex. semaine normale : `temperature` dérive à 0,39, ratio 0,72) : pas d'action
   immédiate. Équipe data dans la semaine : saisonnalité ou qualité des données ? Vérifier d'abord les
   contrats S3 (`velov.contract`, tests dbt). Noter la dérive dans la model card.
2. **Ratio global au-dessus de 0,9, ou une station pire que la persistance** (code de sortie 1) : analyser par
   segment (stations en alerte, heures, jours), puis `python -m velov.retrain`. La promotion suit la règle de S4
   (`python -m velov.promote`, décision humaine), puis `./scripts/release.sh`.
3. **Jamais** de réentraînement automatique sur une dérive : si le flux GBFS est cassé, on apprendrait la panne.
4. Une dérive saisonnière attendue se traite en élargissant l'historique d'entraînement (une année),
   pas en réentraînant chaque semaine.

Avant de surveiller, regarder ce que contient la table (un test de charge y écrit des lignes aléatoires) :
`docker compose exec db psql -U velov -c "SELECT model_version, min(observed_at), max(observed_at), count(*) FROM predictions GROUP BY 1;"`

## 9. Sécurité de l'exposition (EX-19)
- **Un seul point d'entrée** : le proxy (80 redirigé vers 443). API, Grafana, Prometheus, MLflow et PostgreSQL
  ne publient leur port que sur `127.0.0.1` de la VM. MLflow depuis le poste : `ssh -L 5000:127.0.0.1:5000 <user>@<ip-vm>`.
- **Secrets hors de git** : `.env`, `certs/` (clé TLS), `secrets/` (empreinte htpasswd) sont dans `.gitignore`,
  et gitleaks tourne en CI. La clé `velov.key` ne quitte jamais la VM (`chmod 600`).
- **Moindre privilège** : `velov_ro` n'a que SELECT sur `predictions`, en transactions lecture seule, et ne
  peut se connecter à aucune autre base (CONNECT retiré à PUBLIC). Grafana et Prometheus tournent avec
  `no-new-privileges` ; le proxy ne garde que les capacités utiles à son démarrage.
- **Proxy** : TLS 1.2 et 1.3, tickets de session désactivés, version de Nginx masquée, en-têtes `nosniff`,
  `SAMEORIGIN`, `no-referrer`, corps limité à 1 Mo, limite de débit par IP (API 100 req/s, au-dessus de la capacité mesurée pour ne jamais couper un test de charge ; Prometheus
  10 req/s, contre l'essai de mots de passe en rafale). Pas de HSTS : ignoré pour une adresse IP, et bloquant
  avec un certificat auto-signé ; à ajouter avec un nom de domaine (S7).
- **Grafana** : cookies `Secure` et `SameSite=strict`, ni inscription ni accès anonyme, version masquée sans
  connexion, pas de snapshots publics, aucun appel sortant.
- **Rotation** : changer le mot de passe dans `.env`, puis `./scripts/make_htpasswd.sh` et
  `docker compose exec proxy nginx -s reload` (Prometheus), ou `./scripts/make_ro_role.sh` (velov_ro).
  Le mot de passe admin de Grafana se change dans son interface : la variable ne sert qu'à la première
  initialisation du volume.

## 10. Contacts et escalade
-
