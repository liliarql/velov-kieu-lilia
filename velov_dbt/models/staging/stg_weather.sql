
WITH bronze AS (
    SELECT
        CAST(collected_at AS BIGINT) AS collected_at_epoch,
        CAST(response.current.time AS BIGINT) AS weather_epoch,
        CAST(
            response.current.temperature_2m AS DOUBLE
        ) AS temperature,
        CAST(response.current.rain AS DOUBLE) AS rain_mm
    FROM read_json_auto(
        '{{ var("bronze_dir", "../data/bronze") }}/weather/dt=*/*.json'
    )
),

typed AS (
    SELECT
        TO_TIMESTAMP(weather_epoch) AS valid_at,
        TO_TIMESTAMP(collected_at_epoch) AS collected_at,
        CAST(temperature AS DOUBLE) AS temperature,
        CAST(rain_mm > 0 AS BOOLEAN) AS is_raining,
        rain_mm
    FROM bronze

)

SELECT DISTINCT
    valid_at,
    collected_at,
    temperature,
    is_raining,
    rain_mm
FROM typed