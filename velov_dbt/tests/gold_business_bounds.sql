{{ config(severity='error', error_if='>0', store_failures=true) }}
SELECT * FROM {{ ref('station_hourly') }}
WHERE station_id <= 0 OR capacity NOT BETWEEN 1 AND 80
   OR temperature NOT BETWEEN -25 AND 50
