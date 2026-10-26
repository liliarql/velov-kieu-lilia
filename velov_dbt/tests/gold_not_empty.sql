SELECT 1 AS error
WHERE NOT EXISTS (
    SELECT 1
    FROM {{ ref('station_hourly') }}
)