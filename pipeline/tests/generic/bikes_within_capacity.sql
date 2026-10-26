{#-
    Échoue si la part des lignes où bikes_available > capacity dépasse max_ratio.
    Renvoie une ligne (le bilan) quand le seuil est franchi, aucune sinon.
-#}
{% test bikes_within_capacity(model, max_ratio=0) %}
with stats as (
    select
        count(*)                                                  as n_rows,
        count(*) filter (where bikes_available > capacity)        as n_violations
    from {{ model }}
)

select n_rows, n_violations, n_violations::float / nullif(n_rows, 0) as violation_ratio
from stats
where n_violations > {{ max_ratio }} * n_rows
{% endtest %}
