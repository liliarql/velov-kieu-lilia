# Preparation orale TP3 - support redige avec IA

Support d'explication du pipeline et des corrections verifiees dans le depot.

## Presenter le pipeline en deux minutes

Nous archivons chaque snapshot source dans Bronze. L'heure source et le hash du JSON canonique donnent une identite stable. Un second passage sur le meme snapshot ne cree rien. Si l'heure source manque, nous refusons le snapshot : utiliser l'heure du replay casserait l'idempotence.

Silver convertit les types, met les heures en UTC et deduplique les status. `stg_status_checked` rattache la derniere capacity connue a l'heure H par jointure ASOF. Les tests verifient les donnees avant Gold : une violation suffit a bloquer et les lignes fautives restent consultables.

Gold choisit le dernier status eligible dans ]H-1h,H], connu au plus tard a H, puis une meteo valide et collectee avant H. Le candidat a six colonnes est soumis au contrat dbt et aux tests. Un hook ne remplace la table publique qu'apres un build complet reussi. Le training lit uniquement cette table publique, en lecture seule, puis applique Pandera.

## Expliquer chaque modele au partenaire

| Modele | Entree -> sortie | Pourquoi il existe |
|---|---|---|
| stg_station_status | GBFS status -> identifiant, heures source/releve, velos, etat | Types et UTC ; premier feed pour chaque station/releve, afin de ne pas remplacer le passe par une correction tardive |
| stg_station_information | Historique GBFS information -> capacity, metadonnees, heure source | Garder les versions pour joindre la capacity connue a H, pas la capacity actuelle pour toute l'histoire |
| stg_weather | Enveloppe meteo -> valid_at, collected_at, temperature, pluie | Separer validite de l'observation et disponibilite ; conserver les anomalies pour les tests |
| stg_status_checked | Status + capacity ASOF -> observations eligibles a H | Controle metier avant selection Gold ; aucune suppression silencieuse des velos hors capacity |
| station_hourly | Silver valide -> candidat horaire de six colonnes | Une cle station/heure et aucun usage de valeurs connues apres la prediction |

## Questions et exemples a travailler

- Pourquoi deux dates pour le status ? Un releve de 10h publie a 12h n'etait pas connu pour une prediction de 10h.
- Pourquoi ASOF ? Une capacity publiee a 11h ne doit pas modifier la ligne Gold de 10h.
- Pourquoi la moyenne de [H,H+1[ fuit-elle ? Elle utilise des velos observes apres l'emission de la prediction a H. Utiliser une fenetre passee avec disponibilite <= H.
- Que protege le contrat dbt ? Noms/types/structure des colonnes. Les tests verifient les valeurs, l'unicite et les relations.
- Pourquoi un candidat ? Un test de valeurs intervient apres creation du modele ; le candidat permet de garder le Gold public precedent si ce test echoue.
- Pourquoi `shift(-1)` seul ne suffit pas ? Entre 10h et 12h sans 11h, il construit H+2. Nous exigeons un ecart exact d'une heure UTC.
- Pourquoi 48 h ne permettent pas un test de 14 jours ? Le split temporel serait vide. La demo simulee utilise explicitement `--test-days 1`, sans pretendre evaluer un modele pour la production.

## Preuves a montrer

- Bronze : `python -m pytest tests/test_ingest.py -q`.
- Cibles H+1 et changement d'heure : `python -m pytest tests/test_features.py -q`.
- Idempotence Gold et point-in-time : `python -m pytest tests/test_pipeline.py -k 'ex10 or ex11' -q`.
- Publication bloquee : `python -m pytest tests/test_pipeline.py -k ex12 -q`.
- Demo 48 h avec training : commandes `python -m velov.demo` puis dbt et train du README ; le test `test_synthetic48h_gold_and_h1_training` verifie la chaine sur 960 lignes Gold.
- Verification complete dans un environnement neuf : `bash scripts/verify_tp3.sh`.

La revue de l'exercice original avec numeros de lignes, consequences et corrections est dans `audit-s3.md`.
