# Validation TP3 - 2026-10-29

## Perimetre et environnement

Verification du depot contre le TP3 fourni et l'audit utilisateur. VM exclue a la demande (indisponible). Les tests ne collectent pas de donnees reelles sur le reseau. Les archives et la base existantes restent distinctes de la demo generee dans `data/demo48h/`.

Environnement teste : Ubuntu/WSL, Python 3.14.4 ; dbt-core 1.12.6, dbt-duckdb 1.11.0, DuckDB 2.0.0, Pandera 0.35.0, requests 2.34.2. Les versions directes sont declarees dans les requirements. Python 3.12/Docker n'ont pas ete revalides ici.

**Validation depuis un vrai clone Git independant du commit `e3b5e120fe3c35400f4b1119d008437cd428bf32`** : `git clone --no-hardlinks --branch tp3-minh-dung`, nouveau venv Python 3.14, `pip install -r requirements-dev.txt`, `pip install -e .`, puis `bash scripts/verify_tp3.sh`. Toutes les commandes ont retourne 0, avec un arbre Git propre apres execution. Le commit de cloture de ce rapport ne change que la documentation, pas le code teste.

Les sorties de synthese capturees sont conservees dans [validation-tp3-results.txt](validation-tp3-results.txt).

## Resultats reels

| Verification | Resultat |
|---|---|
| Installation dans le nouveau venv | Succes |
| `python -m pip check` | No broken requirements found |
| `python -m ruff check src tests` | All checks passed |
| Suite pytest complete | **39 tests reussis**, aucun echec |
| Build dbt demo avec cutoff fixe et Bronze isole | **PASS=20 WARN=0 ERROR=0 SKIP=0 TOTAL=20** |
| Detail dbt | 5 modeles (4 vues Silver, 1 candidat Gold), 14 tests, 1 hook de publication |
| `python -m velov.train --data <base-demo> --validate-only` | **Pandera PASS : 36 lignes, 36 stations, 1 heure** |
| Demo synthetique 48 h | dbt PASS=20, Pandera 960 lignes / 20 stations / 48 heures |
| Training H+1 sur simulation | 940 exemples : 460 train, 480 test (`--test-days 1`) ; artefacts crees |
| MAE demo (velos) | Modele **1,989**, persistance **1,971** : le modele ne bat pas cette baseline |
| `git diff --check` | Succes |

## Preuves de comportement (tests executables)

`tests/test_ingest.py` : un snapshot valide, meme apres permutation des cles JSON, ne cree qu'un seul fichier ; timestamps absent/invalide/naif/NaN/hors limites ne creent pas de Bronze ; replay meteo ne reecrit pas la premiere collecte.

`tests/test_features.py` : cible uniquement a exactement H+1 par station, trou horaire refuse, retour d'heure Paris traite en heures UTC ecoulees. Les tests precedents de features sont conserves.

`tests/test_pipeline.py` (20 cas, vrais sous-processus dbt) :

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
| Clone propre | Installation neuve, 39 tests et deux scenarios dbt/Pandera reussis ; commit code reference ci-dessus |
| Rendu Git | Branche `tp3-minh-dung`, tag annote `tp3` sur le commit final ; commandes de verification dans le README |

## Demo 48 h et limites

`python -m velov.demo` genere un Bronze synthetique deterministe dans un dossier distinct. Les hypotheses de disponibilite sont explicites dans le manifeste. La generation rejouee garde le meme contenu. Le scenario passe la chaine complete dbt -> Gold public -> Pandera -> train H+1 dans le clone propre et dans le dossier de travail.

Artefacts disponibles localement (regenerables, ignores par Git) :

- `data/demo48h/manifest.json` : provenance synthetique, graine, heures et cutoff.
- `data/demo48h/demo.duckdb` : 960 lignes Gold sur 48 heures.
- `data/demo48h/models/model.joblib` et `metadata.json` : modele et resultats.

Le modele de cette petite simulation ne bat pas la baseline de persistance. L'execution reussie prouve le fonctionnement de l'entrainement, pas sa qualite pour la production. L'historique reel archive reste limite a une heure ; davantage de collecte reelle sera necessaire. Un test de 14 jours ne peut pas etre realise sur 48 h.

Le jeu pedagogique enseignant original de 48 h, de format meteo different et contenant une anomalie volontaire, est source dans `audit-s3.md`. Notre simulation est distincte et ne pretend pas etre une collecte reelle ni la copie de ces fixtures.

Le fichier genere `velov_dbt/.user.yml` est retire du versionnement et ignore, conserve localement. Avant correction, le tag annote `tp3` avait pour objet `6a727e8` et pointait le commit `1095f51` (le HEAD initial) ; l'objet du tag ne doit pas etre confondu avec le commit.

Pour verifier le rendu publie, comparer la branche et le tag dereference : `git ls-remote origin refs/heads/tp3-minh-dung 'refs/tags/tp3^{}'`. Ils doivent donner le meme commit final. Les commandes de clone distant et de verification sont dans le README.
