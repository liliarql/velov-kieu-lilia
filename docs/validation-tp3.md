# Validation TP3 - 2026-10-29

## Perimetre et environnement

Verification du depot courant contre le TP3 fourni et l'audit utilisateur. VM exclue a la demande (indisponible). Aucune collecte reseau reelle ni publication distante n'a ete executee. Les tests pipeline travaillent sur des copies temporaires ; la base et les archives locales existantes n'ont pas ete remplacees.

Environnement teste : Ubuntu/WSL, Python 3.14.4 ; dbt-core 1.12.6, dbt-duckdb 1.11.0, DuckDB 2.0.0, Pandera 0.35.0, requests 2.34.2. Les versions directes sont declarees dans les requirements. Python 3.12/Docker n'ont pas ete revalides ici.

Une copie propre des fichiers sources courants (fichiers Git et nouveaux fichiers non ignores), sans le venv ni les bases locales, a ete preparee dans un repertoire temporaire. Un nouveau venv a ete cree avec Python 3.14 ; `pip install -r requirements-dev.txt`, `pip install -e .` puis `bash scripts/verify_tp3.sh` ont reussi. Il s'agit d'un export de l'etat de travail actuel, pas d'un clone d'un commit distant deja pousse : les corrections ne sont pas encore commitees. Le clone Git final doit etre refait apres commit/tag.

## Resultats reels

| Verification | Resultat |
|---|---|
| Installation dans le nouveau venv | Succes |
| `python -m pip check` | No broken requirements found |
| `python -m ruff check src tests` | All checks passed |
| Suite pytest complete | **38 tests reussis**, aucun echec |
| Build dbt demo avec cutoff fixe et Bronze isole | **PASS=20 WARN=0 ERROR=0 SKIP=0 TOTAL=20** |
| Detail dbt | 5 modeles (4 vues Silver, 1 candidat Gold), 14 tests, 1 hook de publication |
| `python -m velov.train --data <base-demo> --validate-only` | **Pandera PASS : 36 lignes, 36 stations, 1 heure** |
| `git diff --check` | Succes |

## Preuves de comportement (tests executables)

`tests/test_ingest.py` : un snapshot valide, meme apres permutation des cles JSON, ne cree qu'un seul fichier ; timestamps absent/invalide/naif/NaN/hors limites ne creent pas de Bronze ; replay meteo ne reecrit pas la premiere collecte.

`tests/test_features.py` : cible uniquement a exactement H+1 par station, trou horaire refuse, retour d'heure Paris traite en heures UTC ecoulees. Les tests precedents de features sont conserves.

`tests/test_pipeline.py` (19 cas, vrais sous-processus dbt) :

- EX-10 : deux builds du meme Bronze au meme `as_of`, hash du contenu Gold trie identique.
- EX-11 : nouvelles versions de capacity, corrections tardives de status et meteo collectee plus tard ; Gold de l'heure passee identique.
- EX-12 Silver : velos negatifs, velos au-dessus de capacity, station zero, capacity zero ou 81, temperature source hors bornes ; pipeline en echec, Gold public inchange.
- EX-12 contrat dbt : colonne renommee ou mauvais type ; build en echec, Gold precedent inchange.
- EX-12 Gold : valeurs invalides injectees dans le SELECT final apres Silver ; candidat cree mais tests en echec, Gold public inchange.
- Premier build invalide : aucune table publique `station_hourly` creee.
- `dbt run` peut produire un candidat invalide, sans publier ; build partiel invalide ou valide ne remplace pas Gold.
- CLI `--validate-only` : validation sans modele ; Gold public manuellement corrompu pour le test refuse par Pandera, aucun modele ecrit.

`tests/test_serving_import.py` : import de l'API avec imports DuckDB/Pandera interdits, pour verifier que le serving n'a pas acquis de dependances pipeline. Les deux tests API preexistants restent verts.

## Couverture du sujet fourni

| TP3 | Etat local |
|---|---|
| Must - Bronze idempotent | Corrige et teste |
| Must - Silver/Gold dbt, contrat enforced | Present et teste, candidat puis copie publique |
| Must - not_null, relationships, capacity avec seuil | Presents ; seuil strict >0 et diagnostic Silver |
| Should - train lit Gold, Pandera avant entrainement | Present ; validate-only ajoute et teste |
| EX-10/11/12 | Scripts/tests reproductibles et reussis |
| README Pipeline, runbook replay/incidents, nouvel ADR cout | Completes |
| IA declaree et exercice jointure | Enonce original, revue IA par lignes et support d'explication des modeles fournis |
| Stretch Prefect/freshness/Redpanda | Non implemente, facultatif pour valider le Must |
| VM | Exclue de cette correction, non verifiee |
| Tag tp3 et clone final | Etape de rendu apres commit ; voir ci-dessous |

## Limites et cloture du rendu

- Demo reelle : une seule heure ; impossible d'entrainer H+1 sur ce jeu. Collecter davantage d'historique reel ; ne pas presenter le simulateur comme une collecte reelle.
- Le jeu pedagogique enseignant de 48 h a ete retrouve sur son depot public (voir audit-s3.md). Il utilise un format meteo different sans date de collecte et une anomalie volontaire ; ne pas l'injecter tel quel en pretendant prouver la disponibilite historique. Meme 48 h ne permettent pas un test de 14 jours.
- Aucun commit, push ou changement de tag effectue. Le tag annote local `tp3` avait pour objet `6a727e8` et pointait le commit `1095f51`, identique au HEAD initial. Il doit pointer le futur commit final apres revue, puis etre pousse avec la branche. Le fichier genere `velov_dbt/.user.yml` a ete retire de l'index et conserve localement, et son exclusion ajoutee a `.gitignore`.
- Apres commit, verifier un vrai clone : `git clone --local <chemin-du-depot> <nouveau-dossier>`, activer un venv neuf et suivre le README puis `bash scripts/verify_tp3.sh`. L'export propre teste ici valide les sources courantes et les dependances, sans attester un futur push.

## Complement : demo 48 h

`python -m velov.demo` genere un Bronze synthetique dans un dossier distinct. Le test d'integration ajoute a reussi : 960 lignes, 48 heures, 940 cibles H+1 et creation des artefacts avec un test temporel d'un jour. La prochaine verification depuis le clone Git inclut ce scenario dans `scripts/verify_tp3.sh`.
