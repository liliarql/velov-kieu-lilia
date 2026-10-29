-- TP3 · Audit d'une jointure écrite par une IA (mode IA imposée).
--
-- Requête produite par un assistant à partir de la demande :
--   « Ajoute à la table horaire la moyenne des vélos sur l'heure et la météo. »
--
-- Elle tourne, depuis la racine du repo, une fois le pipeline construit :
--   python -m velov.ingest --replay data/fixtures
--   (cd velov_dbt && dbt build --profiles-dir .)
--   duckdb -readonly data/velov.duckdb < exercices/s3_audit/features_ia.sql
--
-- Votre mission : l'auditer avant de l'accepter dans la table d'entraînement.
-- Pour chaque problème : la ligne, la conséquence en production, la correction.

with releves as (
    select
        cast(s.station_id as integer)  as station_id,
        to_timestamp(s.last_reported)  as reported_at,
        s.num_bikes_available          as bikes_available
    from (
        select unnest(data.stations) as s
        from read_json('data/bronze/station_status/*/*.json')
    )
),

meteo as (
    select
        cast(strptime(unnest(hourly.time), '%Y-%m-%dT%H:%M') as timestamp) as heure,
        unnest(hourly.temperature_2m)                                      as temperature,
        unnest(hourly.precipitation)                                       as precipitation
    from read_json('data/bronze/weather/*/*.json')
)

select
    h.station_id,
    h."timestamp",
    h.capacity,
    h.bikes_available,
    avg(r.bikes_available)    as bikes_mean_hour,
    m.temperature,
    m.precipitation > 0       as is_raining
from station_hourly h
left join releves r
    on  r.station_id = h.station_id
    and r.reported_at >= h."timestamp"
    and r.reported_at <  h."timestamp" + interval 1 hour
left join meteo m
    on m.heure = h."timestamp" at time zone 'Europe/Paris'
group by all
order by h.station_id, h."timestamp";
