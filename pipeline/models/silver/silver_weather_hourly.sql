-- Météo horaire typée. Convention Open-Meteo : temperature_2m est instantanée à hour_utc,
-- precipitation est le cumul de l'heure précédente : les deux sont connues à hour_utc (EX-11).
select
    hour_utc,
    cast(payload ->> 'temperature_2m' as double precision)     as temperature,
    cast(payload ->> 'precipitation' as double precision)      as precipitation_mm,
    cast(payload ->> 'precipitation' as double precision) > 0  as is_raining
from {{ source('bronze', 'weather_hourly') }}
where payload ->> 'temperature_2m' is not null
