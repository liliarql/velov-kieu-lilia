{{ config(severity='error', warn_if='>0', error_if='>0', store_failures=true) }}
-- Politique stricte : une seule anomalie bloque la publication ; les lignes
-- sont conservees dans dbt_test__audit pour diagnostic (pas de rejet silencieux).
SELECT * FROM {{ ref('stg_status_checked') }}
WHERE station_id IS NULL OR station_id <= 0
   OR capacity IS NULL OR capacity NOT BETWEEN 1 AND 80
   OR bikes_available IS NULL OR bikes_available < 0 OR bikes_available > capacity
