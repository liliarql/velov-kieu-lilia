#!/usr/bin/env bash
# Rôle PostgreSQL en lecture seule pour la surveillance (S6) : velov.monitor, et Grafana s'il lit la base.
# Il n'a que SELECT sur la table predictions : une fuite de son mot de passe ne permet ni d'écrire,
# ni de lire une autre table, ni de toucher à MLflow.
#
#   ./scripts/make_ro_role.sh          # lit MONITOR_DB_PASSWORD dans .env ; idempotent (rejouable)
#
# Le mot de passe passe par l'entrée standard de psql, jamais en argument : un argument se voit
# dans la liste des process (ps) de la machine hôte.
set -euo pipefail
cd "$(dirname "$0")/.."

PW="$(grep -E '^MONITOR_DB_PASSWORD=' .env | tail -1 | cut -d= -f2- || true)"
if [ -z "$PW" ]; then
  echo "MONITOR_DB_PASSWORD manquant dans .env (voir .env.example)" >&2
  exit 1
fi
case "$PW" in
  *"'"*|*\\*) echo "MONITOR_DB_PASSWORD ne doit contenir ni apostrophe ni antislash" >&2; exit 1 ;;
esac

docker compose exec -T db psql -v ON_ERROR_STOP=1 -q -U velov -d velov <<SQL
\set pw '${PW}'
SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'velov_ro') AS creer \gset
\if :creer
  CREATE ROLE velov_ro LOGIN;
\endif
ALTER ROLE velov_ro WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'pw';
ALTER ROLE velov_ro SET default_transaction_read_only = on;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM velov_ro;
-- Par défaut, tout rôle (PUBLIC) peut se connecter à toutes les bases : on le retire.
-- velov est propriétaire des trois bases, il garde ses droits (API, MLflow).
REVOKE CONNECT ON DATABASE velov, mlflow, postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE velov TO velov_ro;
GRANT USAGE ON SCHEMA public TO velov_ro;
GRANT SELECT ON predictions TO velov_ro;
SQL
echo "Rôle velov_ro prêt : SELECT sur predictions uniquement, transactions en lecture seule."
