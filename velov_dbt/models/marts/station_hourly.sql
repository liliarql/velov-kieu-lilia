
WITH status_raw AS (
    SELECT
        station_id,
        reported_at,
        feed_updated_at,
        bikes_available,
        is_installed,
        is_renting,

        -- Horaire h : dernier relevé dans ]h-1h, h]
        DATE_TRUNC(
            'hour',
            reported_at - INTERVAL '1 microsecond'
        ) + INTERVAL '1 hour' AS hour_ts

    FROM {{ ref('stg_station_status') }}
    WHERE reported_at IS NOT NULL
      AND feed_updated_at IS NOT NULL
),

status_hourly AS (
    SELECT
        station_id,
        hour_ts,
        bikes_available

    FROM status_raw
    WHERE is_installed = TRUE
      AND is_renting = TRUE
      AND bikes_available >= 0

      -- Le relevé doit être connu à l'heure h
      AND reported_at <= hour_ts
      AND feed_updated_at <= hour_ts

    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY station_id, hour_ts
        ORDER BY reported_at DESC, feed_updated_at ASC
    ) = 1
),

with_capacity AS (
    SELECT
        s.station_id,
        s.hour_ts,
        s.bikes_available,
        i.capacity

    FROM status_hourly AS s

    -- Dernière information de station connue à h
    ASOF JOIN {{ ref('stg_station_information') }} AS i
        ON s.station_id = i.station_id
       AND s.hour_ts >= i.feed_updated_at

    WHERE i.capacity > 0
      AND s.bikes_available <= i.capacity
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

    WHERE w.temperature BETWEEN -25 AND 50
      AND w.is_raining IS NOT NULL

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