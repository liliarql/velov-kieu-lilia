-- ref('station_hourly') designe le candidat ; le hook publie apres les tests.
WITH with_capacity AS (
    SELECT station_id, hour_ts, bikes_available, capacity
    FROM {{ ref('stg_status_checked') }}
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY station_id, hour_ts
        ORDER BY reported_at DESC, feed_updated_at ASC
    ) = 1
),

with_weather AS (
    SELECT
        s.station_id,
        s.hour_ts,
        s.capacity,
        s.bikes_available,
        w.temperature,
        w.is_raining

    FROM with_capacity AS s

    INNER JOIN {{ ref('stg_weather') }} AS w
        ON w.valid_at <= s.hour_ts
       AND w.valid_at > s.hour_ts - INTERVAL '1 hour'
       AND w.collected_at <= s.hour_ts

    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY s.station_id, s.hour_ts
        ORDER BY w.collected_at DESC, w.valid_at DESC
    ) = 1
)

SELECT
    CAST(station_id AS INTEGER) AS station_id,
    CAST(hour_ts AS TIMESTAMPTZ) AS timestamp,
    CAST(capacity AS INTEGER) AS capacity,
    CAST(bikes_available AS INTEGER) AS bikes_available,
    CAST(temperature AS DOUBLE) AS temperature,
    CAST(is_raining AS BOOLEAN) AS is_raining

FROM with_weather
WHERE hour_ts <= DATE_TRUNC(
    'hour',
    CAST('{{ var("as_of", run_started_at.isoformat()) }}' AS TIMESTAMPTZ)
)