-- Une ligne par station et par heure, au schéma du contrat d'entrée du modèle (velov.features.RAW_COLUMNS).
--
-- Point-in-time (EX-11) : la ligne datée H décrit l'état CONNU à l'instant H, c'est-à-dire le dernier
-- relevé publié dans [H - 1 h, H[. On n'étiquette jamais un relevé avec un instant antérieur à sa publication.
-- Déterminisme (EX-10) : tout est dérivé de bronze, jamais de now() ; rejouer une période redonne la même table.
with status as (
    select
        station_id,
        observed_at,
        bikes_available,
        date_trunc('hour', observed_at) + interval '1 hour' as snapshot_hour
    from {{ ref('silver_station_status') }}
    where is_installed and is_renting
),

last_in_hour as (
    select distinct on (station_id, snapshot_hour)
        station_id,
        snapshot_hour,
        observed_at,
        bikes_available
    from status
    -- L'heure en cours n'est pas terminée : elle sera publiée au prochain build.
    where snapshot_hour <= (select max(observed_at) from status)
    order by station_id, snapshot_hour, observed_at desc
)

select
    s.station_id::integer                      as station_id,
    s.snapshot_hour                            as timestamp,
    info.capacity::integer                     as capacity,
    s.bikes_available::integer                 as bikes_available,
    w.temperature::double precision            as temperature,
    w.is_raining::boolean                      as is_raining
from last_in_hour as s
-- Capacité valide au moment du relevé, pas la capacité d'aujourd'hui.
cross join lateral (
    select i.capacity
    from {{ ref('silver_station_information') }} as i
    where i.station_id = s.station_id
      and i.valid_from <= s.observed_at
    order by i.valid_from desc
    limit 1
) as info
-- Inner join : une heure sans météo n'est pas publiée plutôt que publiée avec un trou.
inner join {{ ref('silver_weather_hourly') }} as w
    on w.hour_utc = s.snapshot_hour
where info.capacity > 0
