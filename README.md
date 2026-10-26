# TP3 – Pipeline de données Vélo'v

## 1. Objectif

L'objectif du TP3 est de remplacer les données simulées utilisées lors des premières séances par des données réelles du réseau Vélo'v de la Métropole de Lyon.

Le pipeline suit une architecture **Bronze → Silver → Gold** avec DuckDB et dbt, afin de produire une table d'entraînement fiable, reproductible et conforme à un contrat de données.

## 2. Sources de données

**Données Vélo'v**

- Fournisseur : Métropole de Lyon / JCDecaux
- Format : GBFS v2.3
- URL : https://download.data.grandlyon.com/files/rdata/jcd_jcdecaux.jcdvelov/gbfs.json
- Flux utilisés :
  - `station_status` : vélos disponibles, état des stations et horodatages
  - `station_information` : identifiant, capacité et localisation des stations

**Données météorologiques**

- Source : Open-Meteo
- Variables : `temperature_2m` et `rain`
- Les données sont conservées avec leur horodatage de validité et leur date de collecte.

## 3. Architecture

```text
API GBFS Grand Lyon        API Open-Meteo
        |                        |
        v                        v
    ingest.py             weather_ingest.py
        |                        |
        +-----------+------------+
                    |
                    v
              BRONZE (JSON)
                    |
                    v
              dbt + DuckDB
                    |
                    v
                  SILVER
       stg_station_status
       stg_station_information
       stg_weather
                    |
                    v
                   GOLD
               station_hourly
                    |
                    v
          Contrat dbt + Data tests
                    |
                    v
       Validation Pandera / Training
```

### Bronze

Les réponses JSON des API sont conservées sans transformation métier dans `data/bronze/`.

L'ingestion est idempotente : un même snapshot ne doit pas être enregistré deux fois. Les fichiers sont identifiés à partir des informations de la source et d'une empreinte SHA-256.

### Silver

Les modèles dbt permettent de :

- Convertir les types de données.
- Normaliser les horodatages en UTC.
- Dédupliquer les relevés.
- Conserver l'historique utile des stations.
- Préparer les données météorologiques.

### Gold

Le modèle `station_hourly` produit une ligne par station et par heure avec les six colonnes contractuelles :

| Colonne | Type |
|---|---|
| `station_id` | INTEGER |
| `timestamp` | TIMESTAMPTZ |
| `capacity` | INTEGER |
| `bikes_available` | INTEGER |
| `temperature` | DOUBLE |
| `is_raining` | BOOLEAN |

Le modèle utilise uniquement les relevés connus à l'instant considéré afin d'éviter les fuites de données futures (*data leakage*).

Le contrat dbt est activé avec `contract: enforced: true`.

## 4. Installation

Depuis la racine du dépôt :

```bash
python3 -m venv .venv
source .venv/bin/activate

python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

Vérifier l'installation :

```bash
dbt --version
```

## 5. Reproduction du TP3 avec les données de démonstration

Un jeu de données Bronze est fourni dans `data/demo/bronze/` afin de reproduire les transformations sans dépendre des API en temps réel.

Depuis la racine du dépôt :

```bash
bash scripts/setup_tp3_demo.sh
```

Puis :

```bash
cd velov_dbt

dbt debug --profiles-dir .

dbt build --profiles-dir . \
  --vars '{"as_of": "2026-10-26T16:20:00+01:00"}'
```

Afficher les résultats :

```bash
dbt show --select station_hourly --limit 10 --profiles-dir . \
  --vars '{"as_of": "2026-10-26T16:20:00+01:00"}'
```

Cette procédure a été testée depuis un clone propre du dépôt.

**Résultat obtenu :**

```text
PASS=14 WARN=0 ERROR=0 SKIP=0 TOTAL=14
```

Soit 4 modèles dbt et 10 tests de qualité exécutés avec succès.

## 6. Collecte des données en temps réel

Depuis la racine du dépôt :

```bash
python -m velov.ingest
python -m velov.weather_ingest
```

Les nouveaux snapshots sont enregistrés dans `data/bronze/`. Les données de collecte réelles ne sont pas versionnées dans Git ; seul le jeu de démonstration est inclus.

La collecte périodique sur la VM Proxmox reste à déployer lorsque l'environnement sera de nouveau disponible.

## 7. Qualité et contrats

Les contrôles mis en place incluent :

- `not_null` : vérification des colonnes obligatoires.
- `relationships` : cohérence des identifiants de station.
- Contrôle de capacité : `0 <= bikes_available <= capacity`.
- Unicité de `(station_id, timestamp)`.
- Vérification de la présence de données Gold.
- Contrat dbt appliqué à la publication de la table.

### EX-10 – Idempotence

Le pipeline peut être rejoué avec un paramètre `as_of` fixe. La stabilité du résultat Gold est contrôlée en comparant les empreintes SHA-256 de deux exécutions sur le même Bronze.

### EX-11 – Point-in-time correctness

Le Gold utilise des jointures temporelles pour éviter l'utilisation de données qui n'étaient pas encore disponibles à l'instant de prédiction.

Le contrôle effectué sur 36 lignes Gold a donné :

```text
invalid_status        = 0
invalid_capacity_join = 0
invalid_weather       = 0
future_rows           = 0
```

### EX-12 – Contrat et publication

Le schéma Gold est vérifié avec `contract: enforced: true` et les règles métier sont contrôlées par les tests dbt.

Un test négatif complet du blocage de publication reste à réaliser.

## 8. Validation Pandera

Le module `velov.contracts` définit le contrat `StationHourly`.

Pour valider la table Gold sans entraîner le modèle :

```bash
python -m velov.train \
  --data data/velov.duckdb \
  --validate-only
```

L'entraînement H+1 nécessite un historique suffisant pour calculer la cible et réaliser une séparation temporelle train/test.

## 9. Runbook – Procédures de reprise

**Échec de la collecte**

Vérifier l'accessibilité des API, les erreurs HTTP et les logs d'ingestion. Relancer ensuite le script concerné. L'idempotence évite de dupliquer les snapshots déjà enregistrés.

**Échec de dbt**

Depuis `velov_dbt/` :

```bash
dbt debug --profiles-dir .
dbt build --profiles-dir .
```

Examiner le modèle ou le test en erreur avant toute nouvelle publication.

**Recalcul depuis Bronze**

Les modèles Silver et Gold peuvent être recalculés à partir des fichiers Bronze conservés.

Une période absente de la collecte GBFS en temps réel ne peut pas être reconstruite si aucun snapshot historique n'a été archivé.

## 10. Choix techniques – ADR

Nous utilisons une architecture **ELT avec micro-batch**.

- **DuckDB** : base analytique locale, sans serveur supplémentaire.
- **dbt** : transformations SQL versionnées, tests et contrats.
- **Bronze JSON** : conservation des données sources pour le rejeu.
- **Micro-batch de 5 minutes** : fréquence adaptée à une prédiction horaire, sans la complexité d'une infrastructure de streaming.
- **Alternative écartée** : streaming Kafka/Redpanda, non nécessaire pour ce besoin horaire.

Selon l'estimation présentée dans le cours S3, la collecte Vélo'v toutes les cinq minutes représente environ 25 Mo de JSON par jour, soit de l'ordre de 10 Go par an avant compression. Cette estimation concerne le flux Vélo'v et non les données météo.

## 11. Limites actuelles

- L'historique collecté est encore court pour entraîner et évaluer correctement un modèle H+1.
- La collecte automatique sur VM Proxmox n'est pas encore opérationnelle.
- Un test négatif complet du mécanisme de publication EX-12 reste à effectuer.
- La procédure de démonstration utilise un jeu Bronze fixe pour rendre la correction reproductible.