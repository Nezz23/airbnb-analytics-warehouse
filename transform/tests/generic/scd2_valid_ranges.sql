{#- SCD2 integrity: per key, version ranges never overlap and at most one version is open. -#}
{% test scd2_valid_ranges(model, key, valid_from='valid_from', valid_to='valid_to') %}
with ordered as (
    select
        {{ key }} as k,
        {{ valid_from }} as vf,
        {{ valid_to }} as vt,
        lead({{ valid_from }}) over (partition by {{ key }} order by {{ valid_from }}) as next_vf
    from {{ model }}
)
select k, vf, vt, next_vf
from ordered
where vt <= vf                                -- empty or inverted range
   or (next_vf is not null and vt is null)    -- an open version that is not the last
   or (next_vf is not null and vt > next_vf)  -- overlap with the next version
{% endtest %}
