# ADR-0002 - ELT et micro-batch de cinq minutes

- Date : 2026-10-29
- Statut : choix local adopte ; automatisation VM non verifiee.

## Contexte et decision

Nous predisons a H+1. Les API GBFS et Open-Meteo fournissent des snapshots ; nous conservons le Bronze JSON immutable et transformons avec dbt/DuckDB. Une collecte toutes les cinq minutes est le choix cible : 288 passages/jour, sans imposer un moteur de streaming pour une table horaire.

Batch quotidien : simple mais retard trop important pour suivre les stations. Streaming Kafka/Redpanda : plus d'infrastructure, de memoire et d'exploitation pour une frequence horaire. Micro-batch : compromis retenu ; Prefect/Redpanda sont Stretch dans TP3, pas des prerequis du Must.

## Cout et ressources (estimation, pas mesure VM)

288 passages/jour x (taille moyenne station_status + station_information + meteo) donne le stockage brut quotidien. A titre d'hypothese, 0,2 Mo pour les trois snapshots par passage donne 57,6 Mo/jour, environ 21 Go/an avant compression et avant deduplication. La taille reelle doit etre mesuree sur plusieurs jours ; le hash evite certains snapshots identiques, sans garantir un taux de compression ou de deduplication. Les 19 petits fichiers demo ne representent pas un reseau complet ni une journee.

Point de depart a mesurer : VM 2 vCPU, 2 Go RAM, disque avec marge pour Bronze + DuckDB + candidat + copie Gold et logs. Aucun tarif fournisseur n'est invente pour la VM scolaire ; le cout dominant est ici l'espace disque et le temps d'exploitation. Retention, compression Bronze et nettoyage des logs seront fixes apres mesure, sans supprimer les archives necessaires au replay.

## Consequences

- Bronze idempotent avec heure source obligatoire ; ecritures atomiques.
- Un seul writer DuckDB ; verrou commun pour les lanceurs automatiques.
- Point-in-time : status connu a H, capacity ASOF connue a H, meteo valide et collectee avant H.
- Qualite stricte en Silver : une anomalie bloque, lignes conservees pour diagnostic.
- Gold candidat sous contrat dbt, tests complets puis remplacement transactionnel du public. Une execution partielle ne publie pas.
- Coexistence avec l'API Docker TP2 ; le pipeline reste executable en Python local.

Verification : `python -m pytest tests/test_pipeline.py -q`. Limites : historique demo d'une heure ; VM indisponible, consommation et continuite apres SSH non mesurees.
