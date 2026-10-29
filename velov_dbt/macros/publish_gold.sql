{% macro publish_gold(results) %}
  {% set candidate = ref('station_hourly') %}
  {# Seul un build COMPLET et valide peut remplacer la table publique.
     dbt run/test et les selections partielles ne publient jamais. #}
  {% if execute and flags.WHICH == 'build' %}
    {% set completed = {} %}
    {% for result in results %}
      {% do completed.update({result.node.unique_id: result.status | string}) %}
    {% endfor %}
    {% set gate = namespace(ok=true) %}
    {% for node in graph.nodes.values() %}
      {% if node.package_name == project_name and node.resource_type in ['model', 'test'] %}
        {% if completed.get(node.unique_id) not in ['success', 'pass'] %}
          {% set gate.ok = false %}
        {% endif %}
      {% endif %}
    {% endfor %}
    {% if gate.ok %}
      {% set published = api.Relation.create(database=candidate.database,
          schema=candidate.schema, identifier='station_hourly', type='table') %}
      {# DuckDB : une instruction transactionnelle, l'ancienne table reste
         visible si l'instruction echoue. Le candidat est deja teste. #}
      {% do run_query('CREATE OR REPLACE TABLE ' ~ published ~ ' AS SELECT * FROM ' ~ candidate) %}
      {{ log('Gold publie : ' ~ published, info=true) }}
    {% else %}
      {{ log('Gold conserve : build incomplet ou controle en echec.', info=true) }}
    {% endif %}
  {% endif %}
{% endmacro %}
