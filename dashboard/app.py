"""NYC Airbnb analytics dashboard over the gold layer.

uv run streamlit run dashboard/app.py
"""

from __future__ import annotations

import importlib

import pandas as pd
import plotly.express as px
import streamlit as st

import queries as q

# Streamlit re-runs this script on every interaction but keeps imported modules for the server's
# lifetime, and its file watcher only evicts them for edits a connected session observed. Reloading
# keeps queries.py in step with this file (st.cache_data still hits: it keys on function source).
q = importlib.reload(q)

# Categorical slots in fixed order (validated palette); color follows the entity everywhere.
SLOT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
OTHER = "#8a8984"
BOROUGH_COLORS = dict(zip(["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"], SLOT, strict=False))
LL18_LABELS = {
    "long_term_30_plus": "30+ night minimum",
    "unregistered_short_term": "Unregistered short-term",
    "registered": "Registered",
    "exempt": "Exempt",
    "unknown": "Other",
}
LL18_COLORS = dict(zip(LL18_LABELS.values(), [*SLOT[:4], OTHER], strict=True))
MAP_GROUPS = {  # scatter/map forms are capped at three series
    "unregistered_short_term": "Unregistered short-term",
    "registered": "Registered or exempt",
    "exempt": "Registered or exempt",
    "long_term_30_plus": "30+ night minimum",
}
MAP_COLORS = {"30+ night minimum": SLOT[0], "Unregistered short-term": SLOT[1], "Registered or exempt": SLOT[2]}

st.set_page_config(page_title="NYC Airbnb Warehouse", layout="wide")
cached = st.cache_data(ttl=600)


def chart(fig, height: int = 360) -> None:
    fig.update_traces(selector={"type": "scatter", "mode": "lines"}, line={"width": 2})
    fig.update_traces(selector={"type": "scatter", "mode": "lines+markers"}, line={"width": 2}, marker={"size": 8})
    fig.update_layout(
        height=height, margin={"l": 8, "r": 8, "t": 36, "b": 8}, legend_title_text="", hovermode="x unified"
    )
    st.plotly_chart(fig, width="stretch")


snaps = cached(q.snapshots)()
latest = snaps[-1]

st.title("NYC Airbnb Warehouse")
st.caption(
    f"{len(snaps)} accepted Inside Airbnb snapshots, {snaps[0]:%d %b %Y} to {latest:%d %b %Y}. "
    "Partial or defective source files are quarantined by data contracts and excluded (see Data quality). "
    f"Warehouse: {q.backend_label()}."
)
snapshot = st.select_slider("Snapshot", options=snaps, value=latest, format_func=lambda d: f"{d:%d %b %Y}")

tabs = st.tabs(["Overview", "Neighbourhoods", "Local Law 18 radar", "Booking pace", "Listing history", "Data quality"])

# ---------------------------------------------------------------- Overview
with tabs[0]:
    k = cached(q.kpis)(snapshot)
    idx = snaps.index(snapshot)
    prev = cached(q.kpis)(snaps[idx - 1]) if idx else None

    def delta(col, fmt):
        return None if prev is None else fmt(k[col] - prev[col])

    c = st.columns(5)
    c[0].metric("Active listings", f"{k.active_listings:,.0f}", delta("active_listings", lambda d: f"{d:+,.0f}"))
    c[1].metric(
        "Median rate (30-night)",
        f"${k.median_30_night_rate:,.0f}",
        delta("median_30_night_rate", lambda d: f"{d:+,.0f}"),
    )
    c[2].metric("Short-term share", f"{k.share_short_term:.1%}", delta("share_short_term", lambda d: f"{d:+.1%}"))
    c[3].metric(
        "Unregistered ST",
        f"{k.unregistered_short_term:,.0f}",
        delta("unregistered_short_term", lambda d: f"{d:+,.0f}"),
        delta_color="inverse",
    )
    c[4].metric("30-day availability", f"{k.availability_30d:.1%}", delta("availability_30d", lambda d: f"{d:+.1%}"))

    trend = cached(q.borough_trend)()
    left, right = st.columns(2)
    with left:
        chart(
            px.line(
                trend,
                x="snapshot_date",
                y="median_30_night_rate_usd",
                color="borough",
                markers=True,
                color_discrete_map=BOROUGH_COLORS,
                category_orders={"borough": list(BOROUGH_COLORS)},
                title="Median nightly rate, 30-night quotes (USD)",
                labels={"snapshot_date": "", "median_30_night_rate_usd": "USD"},
            )
        )
    with right:
        chart(
            px.line(
                trend,
                x="snapshot_date",
                y="active_listings",
                color="borough",
                markers=True,
                color_discrete_map=BOROUGH_COLORS,
                category_orders={"borough": list(BOROUGH_COLORS)},
                title="Active listings",
                labels={"snapshot_date": "", "active_listings": "listings"},
            )
        )
    life = cached(q.lifecycle)().melt(
        id_vars="snapshot_date", value_vars=["new_listings", "relisted_listings", "delisted_listings"]
    )
    life["variable"] = life.variable.str.replace("_listings", "").str.capitalize()
    chart(
        px.bar(
            life,
            x="snapshot_date",
            y="value",
            color="variable",
            barmode="group",
            color_discrete_sequence=SLOT,
            title="Supply churn between consecutive snapshots",
            labels={"snapshot_date": "", "value": "listings", "variable": ""},
        )
    )
    st.caption(
        "`price` in Inside Airbnb is the nightly rate of one quoted stay whose dates and length vary, "
        "so rates are compared on 30-night quotes only (the most common quote length)."
    )

# ---------------------------------------------------------------- Neighbourhoods
with tabs[1]:
    nb = cached(q.neighbourhoods)(snapshot)
    borough = st.segmented_control("Borough", ["All", *BOROUGH_COLORS], default="All", key="nb_borough")
    view = nb if borough in (None, "All") else nb[nb.borough == borough]
    st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        column_config={
            "median_30_night_rate_usd": st.column_config.NumberColumn("Median rate (30-night)", format="$%.0f"),
            "avg_availability_rate_30d": st.column_config.ProgressColumn("30-day availability", format="percent"),
            "share_short_term": st.column_config.NumberColumn("Short-term share", format="percent"),
            "share_unregistered_short_term": st.column_config.NumberColumn("Unregistered ST share", format="percent"),
            "avg_rating": st.column_config.NumberColumn("Avg rating", format="%.2f"),
        },
    )
    pick = st.selectbox("Neighbourhood history", view.neighbourhood.tolist())
    if pick:
        hist = cached(q.neighbourhood_history)(pick)
        a, b = st.columns(2)
        with a:
            chart(
                px.line(
                    hist,
                    x="snapshot_date",
                    y="median_30_night_rate_usd",
                    markers=True,
                    title=f"{pick}: median nightly rate (30-night quotes)",
                    labels={"snapshot_date": "", "median_30_night_rate_usd": "USD"},
                    color_discrete_sequence=SLOT,
                ),
                300,
            )
        with b:
            chart(
                px.line(
                    hist,
                    x="snapshot_date",
                    y="avg_availability_rate_30d",
                    markers=True,
                    title=f"{pick}: average 30-day availability",
                    labels={"snapshot_date": "", "avg_availability_rate_30d": "share of nights open"},
                    color_discrete_sequence=SLOT,
                ).update_yaxes(tickformat=".0%"),
                300,
            )

        rev = cached(q.review_activity)(pick)
        chart(
            px.line(
                rev,
                x="review_month",
                y="reviews",
                color_discrete_sequence=SLOT,
                title=f"{pick}: reviews per month (demand proxy, full review history)",
                labels={"review_month": "", "reviews": "reviews"},
            ),
            300,
        )

# ---------------------------------------------------------------- Local Law 18
with tabs[2]:
    st.markdown(
        "**Local Law 18 radar.** Since 2023 NYC requires hosts offering stays under 30 nights to register "
        "with the Office of Special Enforcement and display the number. Each listing is classified from what it "
        "shows on Airbnb: a registration number, *Exempt*, a 30+ night minimum, or a short-term minimum with "
        "neither. The last group is a **risk signal, not a legal finding**: this data is not matched against the "
        "city's registry."
    )
    status = cached(q.ll18_status)()
    status["status"] = status.ll18_status.map(LL18_LABELS)
    unreg = status[status.ll18_status == "unregistered_short_term"]
    a, b = st.columns(2)
    with a:
        chart(
            px.line(
                unreg,
                x="snapshot_date",
                y="listings",
                markers=True,
                color_discrete_sequence=[SLOT[1]],
                title="Unregistered short-term listings",
                labels={"snapshot_date": "", "listings": "listings"},
            )
        )
    with b:
        st_only = status[status.ll18_status.isin(["registered", "exempt", "unregistered_short_term"])]
        chart(
            px.bar(
                st_only,
                x="snapshot_date",
                y="listings",
                color="status",
                color_discrete_map=LL18_COLORS,
                title="Listings open to short stays (<30 nights), by status",
                labels={"snapshot_date": "", "listings": "listings"},
            )
        )
    to_snap = snapshot if snapshot != snaps[0] else snaps[1]
    tr = cached(q.ll18_transitions)(to_snap)
    tr = tr[tr.from_status != "not_listed"].assign(
        move=lambda d: (
            d.from_status.map(LL18_LABELS).fillna(d.from_status)
            + " → "
            + d.to_status.map(LL18_LABELS).fillna("delisted")
        )
    )
    a, b = st.columns(2)
    with a:
        chart(
            px.bar(
                tr.head(10).iloc[::-1],
                x="listings",
                y="move",
                orientation="h",
                color_discrete_sequence=[SLOT[0]],
                title=f"Status changes into {to_snap:%d %b %Y} (existing listings)",
                labels={"move": "", "listings": "listings"},
            ).update_layout(hovermode="closest")
        )
    with b:
        hot = cached(q.ll18_hotspots)(snapshot)
        chart(
            px.bar(
                hot,
                x="unregistered_short_term",
                y="neighbourhood",
                orientation="h",
                color="borough",
                color_discrete_map=BOROUGH_COLORS,
                category_orders={"borough": list(BOROUGH_COLORS), "neighbourhood": hot.neighbourhood.tolist()},
                title="Neighbourhoods with the most unregistered short-term listings",
                hover_data={"share_unregistered_short_term": ":.1%", "active_listings": True},
                labels={"neighbourhood": "", "unregistered_short_term": "listings"},
            ).update_layout(hovermode="closest")
        )
    pts = cached(q.map_points)(snapshot)
    pts["group"] = pts.ll18_status.map(MAP_GROUPS)
    pts = pts.dropna(subset=["group"])
    show = st.multiselect("Map layers", list(MAP_COLORS), default=["Unregistered short-term", "Registered or exempt"])
    fig = px.scatter_map(
        pts[pts.group.isin(show)],
        lat="latitude",
        lon="longitude",
        color="group",
        color_discrete_map=MAP_COLORS,
        # Draw order = legend order: the small risk layer goes last so it sits on top.
        category_orders={"group": ["30+ night minimum", "Registered or exempt", "Unregistered short-term"]},
        hover_data={
            "listing_id": True,
            "neighbourhood": True,
            "room_type": True,
            "price_usd": ":$.0f",
            "latitude": False,
            "longitude": False,
        },
        zoom=9.6,
        center={"lat": 40.71, "lon": -73.95},
        map_style="carto-positron",
    )
    fig.update_traces(marker={"size": 8, "opacity": 0.75})
    fig.update_layout(
        height=560,
        margin={"l": 0, "r": 0, "t": 32, "b": 0},
        legend={"title_text": "", "orientation": "h", "y": 1.0, "yanchor": "bottom", "x": 0},
    )
    st.plotly_chart(fig, width="stretch")

    shared = cached(q.shared_registrations)(snapshot)
    st.subheader(f"Registration numbers shown on more than one listing: {len(shared)}")
    st.caption(
        "A registration covers one dwelling unit, so one number on several listings, and especially across "
        "different hosts, is worth a closer look. Sorted by host count."
    )
    st.dataframe(shared, hide_index=True, width="stretch")

# ---------------------------------------------------------------- Booking pace
with tabs[3]:
    st.markdown(
        "Each line is one future **stay month**; it shows the share of that month's nights still open as "
        "seen from each snapshot. A falling line means nights are being taken: booked, or blocked by hosts "
        "(the calendar does not tell the two apart, so this is an upper bound on bookings)."
    )
    b = st.segmented_control("Borough", ["All", *BOROUGH_COLORS], default="All", key="pace_borough")
    pace = cached(q.booking_pace)(None if b in (None, "All") else b)
    months = sorted(pace.stay_month.unique())
    pick = st.multiselect(
        "Stay months",
        months,
        default=[m for m in months if pd.Timestamp(m) >= pd.Timestamp("2026-10-01")][:6],
        format_func=lambda m: f"{pd.Timestamp(m):%b %Y}",
        max_selections=8,
    )
    sel = pace[pace.stay_month.isin(pick)].assign(stay=lambda d: pd.to_datetime(d.stay_month).dt.strftime("%b %Y"))
    chart(
        px.line(
            sel,
            x="snapshot_date",
            y="open_night_rate",
            color="stay",
            markers=True,
            color_discrete_sequence=SLOT,
            category_orders={"stay": [f"{pd.Timestamp(m):%b %Y}" for m in sorted(pick)]},
            title="Share of nights still open, by stay month",
            labels={"snapshot_date": "observed at snapshot", "open_night_rate": "open nights"},
        ).update_yaxes(tickformat=".0%"),
        440,
    )

# ---------------------------------------------------------------- Listing history (SCD2)
with tabs[4]:
    st.markdown(
        "Type 2 history of one listing: every version of its descriptive attributes with the dates it was valid, "
        "next to its per-snapshot measures."
    )
    lid = st.number_input("Listing id", value=cached(q.example_listing)(), step=1, format="%d")
    versions, facts = cached(q.listing_history)(int(lid))
    if versions.empty:
        st.info("No listing with that id in the accepted snapshots.")
    else:
        st.dataframe(versions, hide_index=True, width="stretch")
        chart(
            px.line(
                facts,
                x="snapshot_date",
                y="price_usd",
                markers=True,
                color_discrete_sequence=SLOT,
                hover_data=["quote_nights", "availability_30"],
                title="Quoted nightly rate by snapshot",
                labels={"snapshot_date": "", "price_usd": "USD"},
            ),
            300,
        )
    pc = cached(q.price_changes)()
    agg = pc.groupby("observed_on", as_index=False)[["increases", "decreases"]].sum().melt(id_vars="observed_on")
    chart(
        px.bar(
            agg,
            x="observed_on",
            y="value",
            color="variable",
            barmode="group",
            color_discrete_sequence=SLOT,
            title="Like-for-like price changes vs previous snapshot (same quote length)",
            labels={"observed_on": "", "value": "listings", "variable": ""},
        ),
        320,
    )

# ---------------------------------------------------------------- Data quality
with tabs[5]:
    dq = cached(q.data_quality)()
    acc = (dq.status == "accepted").mean()
    tests = q.test_history()
    c = st.columns(4)
    c[0].metric("Files passing contracts", f"{(dq.status == 'accepted').sum()} / {len(dq)}", f"{acc:.0%}")
    c[1].metric("Rows quarantined", f"{dq.loc[dq.status != 'accepted', 'row_count'].sum():,}")
    if not tests.empty:
        last = tests[tests.run_at == tests.run_at.max()]
        c[2].metric(
            "dbt tests, latest run", f"{(last.status == 'pass').sum()} pass", f"{(last.status == 'warn').sum()} warn"
        )
        c[3].metric("Failed / errored", f"{last.status.isin(['fail', 'error']).sum()}")
    st.subheader("Contract results per file")
    st.dataframe(
        dq[["dataset", "snapshot_date", "row_count", "status", "violations"]],
        hide_index=True,
        width="stretch",
    )
    comp = (
        dq[dq.dataset == "listings"]
        .melt(
            id_vars="snapshot_date",
            value_vars=["price_completeness", "license_completeness", "beds_completeness", "rating_completeness"],
        )
        .dropna()
    )
    comp["variable"] = comp.variable.str.replace("_completeness", "").str.capitalize()
    chart(
        px.line(
            comp,
            x="snapshot_date",
            y="value",
            color="variable",
            markers=True,
            color_discrete_sequence=SLOT,
            title="Field completeness, accepted listings snapshots",
            labels={"snapshot_date": "", "value": "non-null share", "variable": ""},
        ).update_yaxes(tickformat=".0%"),
        340,
    )
    st.caption(
        "Beds completeness swings between scrapes (11% on 2026-04-14, 0% on 2026-05-14), "
        "which is why beds and bedrooms are carried but not "
        "tracked in the SCD2 history."
    )
    if not tests.empty:
        runs = tests.groupby(["run_at", "status"]).size().unstack(fill_value=0).reset_index()
        st.subheader("dbt test runs")
        st.dataframe(runs.sort_values("run_at", ascending=False), hide_index=True, width="stretch")
