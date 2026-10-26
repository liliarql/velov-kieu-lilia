
WITH bronze AS (
    SELECT
        last_updated,
        UNNEST(data.stations) AS station
    FROM read_json_auto(
        '../data/bronze/station_information/dt=*/*.json'
    )
),

typed AS (
    SELECT
        CAST(station.station_id AS INTEGER) AS station_id,
        CAST(station.capacity AS INTEGER) AS capacity,
        CAST(station.name AS VARCHAR) AS station_name,
        CAST(station.address AS VARCHAR) AS address,
        CAST(station.lat AS DOUBLE) AS latitude,
        CAST(station.lon AS DOUBLE) AS longitude,
        TO_TIMESTAMP(last_updated) AS feed_updated_at
    FROM bronze
)

SELECT DISTINCT *
FROM typed
