
WITH bronze AS (
    SELECT
        last_updated,
        UNNEST(data.stations) AS station
    FROM read_json_auto(
        '../data/bronze/station_status/dt=*/*.json'
    )
),

typed AS (
    SELECT
        CAST(station.station_id AS INTEGER) AS station_id,
        TO_TIMESTAMP(last_updated) AS feed_updated_at,
        TO_TIMESTAMP(station.last_reported) AS reported_at,
        CAST(
            station.num_bikes_available AS INTEGER
        ) AS bikes_available,
        CAST(
            station.num_docks_available AS INTEGER
        ) AS docks_available,
        CAST(station.is_installed AS BOOLEAN) AS is_installed,
        CAST(station.is_renting AS BOOLEAN) AS is_renting,
        CAST(station.is_returning AS BOOLEAN) AS is_returning
    FROM bronze
)

SELECT *
FROM typed
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY station_id, reported_at
    ORDER BY feed_updated_at ASC
) = 1
