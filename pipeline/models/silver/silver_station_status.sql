-- Relevés de disponibilité typés. observed_at = instant de publication du flux GBFS.
select
    cast(station_id as integer)                                as station_id,
    feed_last_updated                                          as observed_at,
    cast(payload ->> 'num_bikes_available' as integer)         as bikes_available,
    cast(payload ->> 'num_docks_available' as integer)         as docks_available,
    cast(payload ->> 'num_bikes_disabled' as integer)          as bikes_disabled,
    cast(payload ->> 'is_installed' as boolean)                as is_installed,
    cast(payload ->> 'is_renting' as boolean)                  as is_renting
from {{ source('bronze', 'station_status') }}
where station_id ~ '^[0-9]+$'
