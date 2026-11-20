#!/usr/bin/env bash
# Test de charge complet, en local ou sur la VM : déploie l'API limitée en CPU, attend /ready,
# lance les paliers (paliers.sh) puis affiche le résumé (summary.py).
# Locust tourne toujours sur VOTRE POSTE ; seule l'API change de machine.
#
#   ./loadtest/lancer.sh local w1                          # API Docker sur le poste
#   ./loadtest/lancer.sh vm w1                             # API sur la VM -> results/vm-w1
#   API_WORKERS=2 API_CPUS=2 ./loadtest/lancer.sh local w2
#
# Variables : API_CPUS (défaut 1), API_WORKERS (défaut 1),
#             VM_SSH (défaut groupe11@192.168.1.227), VM_DIR (défaut ~/velov-mlops),
#             plus celles de paliers.sh (PALIERS, DUREE, WARMUP).
# Pas de repli automatique local <-> VM : une mesure doit dire où elle a été faite.
set -euo pipefail

CIBLE="${1:?usage : lancer.sh <local|vm> <prefixe>}"
PREFIX="${2:?usage : lancer.sh <local|vm> <prefixe>}"
export API_CPUS="${API_CPUS:-1}"
export API_WORKERS="${API_WORKERS:-1}"
VM_SSH="${VM_SSH:-groupe11@192.168.1.227}"
VM_DIR="${VM_DIR:-~/velov-mlops}"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.."

COMPOSE="-f compose.yaml -f compose.loadtest.yaml"
if [ "$API_WORKERS" -gt 1 ]; then
  COMPOSE="$COMPOSE -f compose.workers.yaml"
fi

attendre_ready() {  # $1 = URL de l'API
  for _ in $(seq 1 60); do
    curl -fsS "$1/ready" > /dev/null 2>&1 && return 0
    sleep 2
  done
  echo "L'API ne répond pas sur $1/ready après 2 min" >&2
  return 1
}

case "$CIBLE" in
  local)
    HOST="http://localhost:8000"
    echo "Déploiement local : ${API_WORKERS} worker(s), ${API_CPUS} cœur(s)"
    # shellcheck disable=SC2086
    docker compose $COMPOSE up -d --build api
    ;;
  vm)
    HOST="http://${VM_SSH#*@}:8000"
    PREFIX="vm-${PREFIX}"
    if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "$VM_SSH" true 2>/dev/null; then
      echo "VM injoignable en SSH (${VM_SSH}). Réseau de l'école ? Sinon : ./loadtest/lancer.sh local ${2}" >&2
      exit 1
    fi
    SHA="$(git rev-parse HEAD)"
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
      echo "Attention : modifications non commitées, la VM reçoit le dernier commit (${SHA:0:7})" >&2
    fi
    echo "Envoi du commit ${SHA:0:7} sur la VM (branche loadtest, pas la branche extraite)"
    git push -f vm "${SHA}:refs/heads/loadtest"
    echo "Déploiement sur la VM : ${API_WORKERS} worker(s), ${API_CPUS} cœur(s)"
    # Pas de --force : si la VM a des modifications locales, on s'arrête au lieu de les écraser.
    # shellcheck disable=SC2029
    ssh "$VM_SSH" "cd ${VM_DIR} && git switch --detach ${SHA} \
      && API_CPUS=${API_CPUS} API_WORKERS=${API_WORKERS} docker compose ${COMPOSE} up -d --build api"
    ;;
  *)
    echo "Cible inconnue : ${CIBLE} (attendu : local ou vm)" >&2
    exit 1
    ;;
esac

attendre_ready "$HOST"
"$HERE/paliers.sh" "$HOST" "$PREFIX"

# Compléter le protocole : où tournait l'API et avec quelles ressources.
{
  echo "cible=${CIBLE}"
  echo "api_cpus=${API_CPUS} api_workers=${API_WORKERS}"
  if [ "$CIBLE" = local ]; then
    # Limite réellement appliquée au conteneur (1000000000 = 1 cœur), pas seulement demandée.
    # shellcheck disable=SC2086
    echo "api_nanocpus=$(docker inspect --format '{{.HostConfig.NanoCpus}}' "$(docker compose $COMPOSE ps -q api)")"
    echo "note=mesure locale : API et Locust sur le même poste"
  fi
} >> "results/${PREFIX}/protocole.txt"

python loadtest/summary.py "results/${PREFIX}"
