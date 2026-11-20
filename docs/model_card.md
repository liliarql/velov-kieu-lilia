# Model card : velov-availability

> Template à compléter par le binôme. Mettez-la à jour à chaque nouvelle version du modèle.
> Inspirée de Mitchell et al., "Model Cards for Model Reporting" (2019).

## 1. Identité
- Nom / version :
- Date d'entraînement :
- Responsables (binôme) :
- Emplacement de l'artefact (fichier, registre, image) :

## 2. Usage prévu
- Ce que le modèle prédit :
- Utilisateurs et décisions alimentées :
- Usages hors périmètre (ce qu'il ne faut PAS faire avec ce modèle) :

## 3. Données
- Source, période couverte, volume :
- Découpage entraînement / test (et pourquoi temporel) :
- Limites connues (stations absentes, événements exceptionnels, météo...) :

## 4. Performance
| Métrique | Modèle | Baseline (persistance) | Seuil d'acceptation |
|---|---|---|---|
| MAE (vélos) | 1,339 | 1,879 | battre la persistance (ratio 0,71) |

- Fenêtre de test : les 14 derniers jours de `data/velov_history.csv` (6 720 lignes), découpage temporel.
- Performance par segment : MAE en heures de pointe 1,415. Par station : voir section 8 (le seul endroit
  où un segment qui décroche se voit).

## 5. Contrat d'entrée / sortie
- Champs d'entrée, types, bornes :
- Sortie et unité :
- Comportement sur entrée inconnue (nouvelle station...) :

## 6. Risques et limites
- Dérive attendue (saisonnalité, travaux, nouvelles stations) :
- Données personnelles : oui / non, justification :

## 7. Exploitation
- Latence cible : 95 % des `/v1/predict` sous 200 ms (EX-16), suivi en continu par Prometheus (runbook, section 7).
- Fréquence de réentraînement prévue : sur alerte de `velov.monitor` (runbook, section 8), jamais
  automatiquement ; décision par la règle de promotion de S4.
- Lien vers le runbook : `docs/runbook.md`.

## 8. Monitoring (EX-21)
- **Outil** : `python -m velov.monitor`, sur les prédictions journalisées (table `predictions`), rôle en
  lecture seule `velov_ro`.
- **Référence** : la première semaine de la fenêtre de test du modèle servi (3 360 lignes, du 2026-08-15 au
  2026-08-22), avec les prédictions du modèle sur ces lignes. Ni l'entraînement (le modèle y paraît meilleur),
  ni les 14 jours (ils contiendraient la semaine surveillée).
- **Courant** : les 7 derniers jours journalisés, une ligne par station et par heure (la dernière prédiction
  si une heure a été rejouée), pour la version servie.
- **Colonnes surveillées** : `temperature`, `bikes_available`, `occupancy_rate`, `is_raining` (entrées),
  `predicted_bikes` (sortie). Score Evidently 0.7.23 : distance de Wasserstein normée (seuil 0,1) pour les
  numériques, Jensen-Shannon pour `is_raining`.
- **Erreur réelle** : MAE à H+1 face à la persistance, sur les mêmes lignes, globale et par station.
- **Seuils d'alerte** (code de sortie 1) : ratio global > 0,9 ; ou une station avec un ratio > 1,0 sur au
  moins 50 paires. Une dérive seule n'alerte pas.
- **Fréquence** : chaque semaine (crontab de la VM à mettre en place, Stretch du TP6).

### Semaines rejouées (2026-11-20, en local, VM injoignable)
Rejeu de la seconde semaine de test, décalée pour finir le 2026-11-14, à travers le proxy HTTPS ;
3 360 prédictions par semaine, 3 340 jugées (la dernière heure n'a pas encore sa vérité). Modèle 0.1.0.

| Semaine | Dérive (score) | MAE modèle | MAE persistance | Ratio | Pire station (ratio) | Alerte |
|---|---|---|---|---|---|---|
| Référence (sans rejeu) | — | — | — | 0,71 | 18 (0,90) | non |
| Normale | `temperature` 0,39 | 1,309 | 1,821 | 0,72 | 6 (0,99) | non |
| Froid, −15 °C | `temperature` 4,46 | 1,419 | 1,821 | 0,78 | 6 (0,97) | non |
| Déménagement | `temperature` 0,39, `bikes_available` 0,25, `occupancy_rate` 0,30, `predicted_bikes` 0,26 | 1,584 | 1,818 | 0,87 | 8 (1,18) | **oui** : stations 2, 5, 8, 11, 14, 17, 20 |

**Quelles colonnes dérivent, et pourquoi ?**
- Normale : seule `temperature` dérive (0,39). Fin août, il fait un peu plus frais que la semaine de
  référence : c'est de la saisonnalité, et l'erreur ne bouge pas. Une dérive n'est pas une panne.
- Froid : `temperature` dérive très fort (4,46), hors de tout ce que le modèle a vu ; les prédictions bougent à
  peine (0,09, sous le seuil). Le modèle se sert peu de la température.
- Déménagement : la température reste celle de la semaine normale, mais `bikes_available`, `occupancy_rate`
  et `predicted_bikes` dérivent : les 7 stations de bureaux se remplissent comme des stations résidentielles.

**Le ratio à la persistance, comparé à l'entraînement.** 0,71 à l'entraînement et sur la référence ;
0,72 en semaine normale (même niveau), 0,78 au froid (le modèle extrapole et perd 8 %), 0,87 au déménagement.
Les trois restent sous 0,9 : **le seuil global seul n'aurait rien vu**, même pendant le déménagement.

**Les stations les plus mal prédites : un segment qui décroche ?** Oui, au déménagement : les 7 stations
de bureaux, et elles seules, passent au-dessus de la persistance (ratio de 1,01 à 1,18 ; station 8 : MAE 1,34
contre 1,14). Le modèle y fait pire que de ne rien prédire, alors que la moyenne cache le problème.
Au froid et en semaine normale, aucune station ne dépasse 1,0.

**Réentraîner ou non, et le seuil retenu.**
- Normale et froid : non. L'erreur reste dans la marge ; la dérive de la température est notée ici.
- Déménagement : oui, après analyse : le comportement des stations 2, 5, 8... a changé durablement (dérive du
  concept), le modèle doit l'apprendre. `velov.retrain`, puis la règle de promotion de S4 et une décision humaine.
- Seuils retenus : **0,9 en global** et **1,0 par station** (au moins 50 paires). Sur les deux seules semaines
  hors entraînement dont on dispose (référence et semaine normale), aucun des deux n'aurait sonné : zéro
  fausse alerte. Le déménagement est détecté par le seuil par station, pas par le global.
- **Limite connue** : la marge du seuil par station est mince (0,99 pour la station 6 en semaine normale).
  Deux semaines d'historique hors entraînement ne suffisent pas à calibrer : à recaler par backtest sur
  plusieurs semaines quand la collecte en aura accumulé, avec le nombre de fausses alertes qu'il aurait données.
