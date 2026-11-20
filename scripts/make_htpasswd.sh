#!/usr/bin/env bash
# Identifiants de l'authentification basique devant Prometheus (S6), lus par le proxy Nginx.
#
#   ./scripts/make_htpasswd.sh      # lit PROMETHEUS_WEB_USER (défaut velov) et PROMETHEUS_WEB_PASSWORD dans .env
#
# Le fichier ne contient qu'une empreinte SHA-512 salée (crypt $6$), jamais le mot de passe.
# Il reste hors de git (secrets/ dans .gitignore). Le mot de passe passe par l'entrée standard
# d'openssl, jamais en argument (visible dans ps).
set -euo pipefail
cd "$(dirname "$0")/.."

lire() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
USER_WEB="$(lire PROMETHEUS_WEB_USER)"
USER_WEB="${USER_WEB:-velov}"
PW="$(lire PROMETHEUS_WEB_PASSWORD)"
if [ -z "$PW" ] || [ "$PW" = "change-me" ]; then
  echo "PROMETHEUS_WEB_PASSWORD manquant ou laissé à change-me dans .env" >&2
  exit 1
fi
if [ "${#PW}" -lt 16 ]; then
  echo "PROMETHEUS_WEB_PASSWORD trop court (16 caractères minimum)" >&2
  exit 1
fi

mkdir -p secrets
HASH="$(printf '%s' "$PW" | openssl passwd -6 -stdin)"
umask 022  # lisible par l'utilisateur nginx du conteneur ; ce n'est qu'une empreinte salée
printf '%s:%s\n' "$USER_WEB" "$HASH" > secrets/prometheus.htpasswd
echo "secrets/prometheus.htpasswd écrit pour l'utilisateur ${USER_WEB} (empreinte SHA-512)."
echo "Recharger le proxy : docker compose exec proxy nginx -s reload"
