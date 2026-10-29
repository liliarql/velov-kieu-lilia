{{ config(severity='error', warn_if='>0', error_if='>0', store_failures=true) }}
SELECT * FROM {{ ref('stg_weather') }}
WHERE (collected_at IS NULL OR collected_at <= CAST('{{ var("as_of", run_started_at.isoformat()) }}' AS TIMESTAMPTZ))
  AND (valid_at IS NULL OR collected_at IS NULL OR valid_at > collected_at
       OR temperature IS NULL OR temperature NOT BETWEEN -25 AND 50
       OR rain_mm IS NULL OR rain_mm < 0 OR is_raining IS NULL)
