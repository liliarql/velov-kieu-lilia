-- Donnees eligibles a l'instant de prediction, AVANT filtrage metier Gold.
WITH status AS (
    SELECT *,
        DATE_TRUNC('hour', reported_at - INTERVAL '1 microsecond')
            + INTERVAL '1 hour' AS hour_ts
    FROM {{ ref('stg_station_status') }}
), eligible AS (
    SELECT * FROM status
    WHERE is_installed = TRUE AND is_renting = TRUE
      AND reported_at <= hour_ts AND feed_updated_at <= hour_ts
      AND hour_ts <= DATE_TRUNC('hour', CAST('{{ var("as_of", run_started_at.isoformat()) }}' AS TIMESTAMPTZ))
)
SELECT s.*, i.capacity
FROM eligible s
ASOF LEFT JOIN {{ ref('stg_station_information') }} i
    ON s.station_id = i.station_id AND s.hour_ts >= i.feed_updated_at
