# TP3 - Exercice de jointure et declaration IA

## Source et etat de l'exercice (slide 3)

L'enonce original a ete recupere sans modification dans `exercices/s3_audit/features_ia.sql`, depuis [le depot de l'enseignant, commit 61e2a9d](https://github.com/MohamedGassem/velov-mlops-student/blob/61e2a9d/exercices/s3_audit/features_ia.sql), le 2026-10-29. Il etait absent de l'historique local au debut de la correction.

Les commandes de l'en-tete sont celles du depot enseignant : notre CLI `--replay` attend un fichier GBFS et `--feed`, et notre meteo Bronze utilise une enveloppe `response.current` + `collected_at`. L'exercice est garde tel quel pour la revue, pas execute comme modele dbt du pipeline corrige.

La liste ci-dessous est la revue IA de l'enonce fourni. Le support `preparation-orale-tp3.md` explique les modeles et les corrections du pipeline.

## Revue IA de la jointure originale

| Lignes | Probleme et consequence | Correction |
|---|---|---|
| 44-45 | Moyenne dans [H,H+1[ : inclut des observations apres H alors que la prediction est emise a H. Score offline artificiellement favorable. | Fenetre passee ]H-1h,H], avec heure source connue au plus tard a H. |
| 17-21 | Seul `last_reported` est lu ; une observation ancienne publiee/corrigee apres H peut entrer dans les features passees. | Lire aussi l'heure de disponibilite du feed et exiger `feed_updated_at <= H` ; conserver la disponibilite de premiere observation. |
| 20-21, 38 | Tous les snapshots bruts sont agreges sans deduplication. Les releves repetes surrepresentent certains instants. | Deduplication par station/heure de releve avec politique temporelle deterministe avant agregation. |
| 27, 47 | La meteo perd son fuseau (TIMESTAMP naive) et Gold est converti en heure murale Europe/Paris. Les fixtures enseignant annoncent GMT : decalage d'une/deux heures et ambiguite au retour d'heure. | Parser le fuseau annonce, convertir en TIMESTAMPTZ UTC, joindre des instants UTC ; garder Paris seulement pour les features calendaires. |
| 25-30, 46-47 | Aucune date de collecte/publication meteo : des valeurs connues seulement apres H peuvent etre utilisees a H. L'heure de validite seule ne prouve pas la disponibilite. | Conserver `collected_at`/date d'emission du forecast et exiger une disponibilite <= H. Sans cette provenance, ne pas certifier EX-11. |
| 30, 46-48 | Plusieurs fichiers/version meteo pour la meme heure peuvent donner plusieurs lignes `(station_id,H)` apres `group by all` si leurs valeurs different. | Choisir une seule version meteo connue a H avec un ordre deterministe, puis verifier l'unicite de la cle. |
| 18-21, 38 | Pas de controle metier des releves agreges (negatifs, depassement de capacity, station desinstallee) ; une anomalie fausse la moyenne. | Controler en Silver, capacity ASOF et politique explicite pour l'etat des stations ; bloquer au seuil choisi. |

La colonne `bikes_mean_hour` ajouterait aussi une septieme colonne : pour l'integrer au Gold contractuel, il faudrait decider et versionner un nouveau contrat. La requete de l'exercice reste une requete d'audit et n'a pas ete ajoutee au Gold a six colonnes.

Les fixtures de 48 h sont retrouvees dans [le depot enseignant](https://github.com/MohamedGassem/velov-mlops-student/tree/61e2a9d/data/fixtures), generees par `scripts/make_fixtures.py`. Elles contiennent des doublons, une station figee et une valeur 99 volontairement invalide, et leur meteo horaire n'a pas `collected_at`. Les utiliser directement avec notre schema different ferait perdre la preuve de disponibilite ; notre demo reelle d'une heure reste donc separee. Une simulation separee est maintenant disponible avec `python -m velov.demo` : 48 h, 20 stations, disponibilite definie par le scenario et origine synthetique indiquee dans le manifeste. Elle permet de tester le training, sans reutiliser ces fixtures comme des observations reelles.

## Revue IA du pipeline effectivement present

- Ancien `features.py`, `shift(-1)` : une ligne manquante transformait H+1 en H+2. Correction : garder seulement un ecart exact d'une heure UTC.
- Ancien `ingest.py`, fallback sur `datetime.now` : replay du meme JSON sans `last_updated` creait plusieurs fichiers. Correction : refuser le snapshot avant toute ecriture.
- Ancien Gold : filtrage de capacity avant les tests. Correction : controle Silver sur capacity ASOF avant publication, stockage des lignes fautives.
- Ancien build : creation de Gold public avant tests de valeurs. Correction : candidat sous contrat puis publication seulement apres tous les tests.
- Revue de la jointure actuelle : status connu au cutoff horaire ; capacity derniere connue a H ; meteo `valid_at` dans l'heure precedente et `collected_at <= H`. Les corrections connues apres H ne doivent pas changer les features passees (test EX-11).

## Declaration IA de cette correction

Outil : OpenAI Codex. Demande : verifier l'audit contre le depot, lire le TP3, corriger les erreurs confirmees et documenter les exigences manquantes, hors VM indisponible.

Contributions : corrections Python/SQL, hook de publication, tests de regression et integration, demo synthetique 48 h, README, runbook, ADR, explication des modeles et suivi des exigences. Verifications executees : `validation-tp3.md`. La revue de la jointure et ce support ont ete rediges avec l'aide de Codex. Les donnees simulees sont identifiees comme telles ; la VM est exclue.
