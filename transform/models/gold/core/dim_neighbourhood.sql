select
    {{ dbt_utils.generate_surrogate_key(['neighbourhood']) }} as neighbourhood_sk,
    neighbourhood,
    -- Each neighbourhood belongs to one borough; mode() keeps the grain safe if a source row disagrees.
    mode(borough)                    as borough,
    avg(latitude)                    as centroid_latitude,
    avg(longitude)                   as centroid_longitude
from {{ ref('stg_listings') }}
group by neighbourhood
