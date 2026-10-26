# ADR-0002 : collecte en micro-batch (15 min) et transformation batch

- Statut : accepté
- Date : 2026-10-26
- Auteurs : Lilia (binôme), rédaction assistée par IA (déclarée)

## Contexte
Le modèle prédit la disponibilité **à H+1**, avec une ligne par station et par heure.
La source (flux GBFS Vélo'v du Grand Lyon) est un instantané publié toutes les quelques minutes,
pas un flux d'événements : on ne reçoit pas les locations une par une, seulement l'état courant.
La météo (Open-Meteo) est horaire.

Ordre de grandeur mesuré au premier passage : 466 stations par instantané. À 4 passages par heure,
cela fait environ 45 000 lignes bronze par jour. Avec un payload JSON de quelques centaines d'octets,
on obtient quelques dizaines de Mo par jour et moins de 1 Go par mois. C'est une estimation, à confirmer
avec `pg_total_relation_size` après une semaine de collecte.

Contraintes : une VM Proxmox par groupe, déjà en place, sans budget cloud. Une personne doit pouvoir
réparer le pipeline à 3 h du matin avec le runbook.

## Options envisagées
| Option | Avantages | Inconvénients |
|---|---|---|
| Batch quotidien | Le plus simple | Un instantané par jour ne reconstruit pas l'historique horaire (la source n'est pas rejouable) |
| **Micro-batch : collecte toutes les 15 min, dbt à la demande** | Granularité suffisante pour l'horaire, rattrapage d'une heure manquée, un seul conteneur Python, coût marginal nul sur la VM | Latence de quelques minutes à une heure, sans importance pour une prédiction H+1 |
| Streaming (Redpanda/Kafka + consommateur) | Latence de quelques secondes, rejouabilité du topic | Un broker de plus à exploiter (mémoire, disque, supervision), aucun gain puisque la source est elle-même un instantané interrogé ; en cloud, un cluster managé représente un coût mensuel fixe sans commune mesure avec ce volume |

## Décision
Nous retenons le **micro-batch** : le service `collector` écrit en bronze toutes les 15 minutes, de façon
idempotente, et `dbt build` reconstruit silver et gold à partir de bronze. La latence de bout en bout,
inférieure à une heure, suffit à un modèle qui prédit H+1.

## Conséquences
- La seule donnée irremplaçable est bronze : une heure non collectée est perdue (la source n'a pas d'historique).
  La sauvegarde du volume PostgreSQL devient importante.
- Gold est déterministe (dérivé de bronze uniquement) : on peut le reconstruire à tout moment (EX-10).
- À revoir si le besoin passe à une prédiction à quelques minutes ou à une détection d'anomalie en temps réel :
  le streaming (stretch du TP3 : Redpanda dans le compose) redeviendrait pertinent.
