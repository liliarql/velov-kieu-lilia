-- Versions successives des fiches stations, avec leur début de validité (point-in-time).
-- La première version connue d'une station vaut depuis toujours : sinon les relevés antérieurs
-- à la première collecte des fiches n'auraient pas de capacité.
with versions as (
    select
        cast(station_id as integer)                  as station_id,
        feed_last_updated,
        payload ->> 'name'                           as name,
        cast(payload ->> 'lat' as double precision)  as lat,
        cast(payload ->> 'lon' as double precision)  as lon,
        cast(payload ->> 'capacity' as integer)      as capacity,
        row_number() over (partition by station_id order by feed_last_updated, payload_sha256) as version
    from {{ source('bronze', 'station_information') }}
    where station_id ~ '^[0-9]+$'
)

select
    station_id,
    case when version = 1 then '-infinity'::timestamptz else feed_last_updated end as valid_from,
    name,
    lat,
    lon,
    capacity
from versions
