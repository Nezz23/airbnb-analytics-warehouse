{#-
  NYC Local Law 18 (short-term rental registration) status signal for one listing snapshot.
  Stays under 30 nights need an OSE registration number unless exempt; 30+ night minimums are
  outside the law. This uses only what the host displays on Airbnb: it is a risk signal,
  not a determination against the city's registry.
-#}
{% macro ll18_status(license_col, minimum_nights_col) -%}
    case
        when {{ license_col }} like 'OSE-STRREG-%' then 'registered'
        when {{ license_col }} = 'EXEMPT' then 'exempt'
        when {{ minimum_nights_col }} >= 30 then 'long_term_30_plus'
        when {{ minimum_nights_col }} < 30 then 'unregistered_short_term'
        else 'unknown'
    end
{%- endmacro %}
