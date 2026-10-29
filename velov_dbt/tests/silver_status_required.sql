{{ config(severity='error', error_if='>0', store_failures=true) }}
SELECT * FROM {{ ref('stg_station_status') }}
WHERE (feed_updated_at IS NULL OR feed_updated_at <= CAST('{{ var("as_of", run_started_at.isoformat()) }}' AS TIMESTAMPTZ))
  AND (reported_at IS NULL OR feed_updated_at IS NULL)
