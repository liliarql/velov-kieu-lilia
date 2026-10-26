{{ config(
    severity='error',
    error_if='>0'
) }}

SELECT
    station_id,
    timestamp,
    bikes_available,
    capacity
FROM {{ ref('station_hourly') }}
WHERE capacity <= 0
   OR bikes_available < 0
   OR bikes_available > capacity