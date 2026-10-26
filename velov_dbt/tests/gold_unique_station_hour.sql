SELECT
    station_id,
    timestamp,
    COUNT(*) AS occurrences
FROM {{ ref('station_hourly') }}
GROUP BY station_id, timestamp
HAVING COUNT(*) > 1