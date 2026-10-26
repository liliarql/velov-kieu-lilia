-- Référentiel des stations : la dernière version de chaque fiche.
select distinct on (station_id)
    station_id,
    name,
    lat,
    lon,
    capacity
from {{ ref('silver_station_information') }}
order by station_id, valid_from desc
