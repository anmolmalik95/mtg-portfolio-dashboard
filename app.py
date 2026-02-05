import streamlit as st
import pandas as pd
import altair as alt
from typing import Any, cast

from scripts.dashboard_data import get_dashboard_data

st.set_page_config(page_title="MTG Collection Dashboard", layout="wide")
st.title("🧙‍♂️ Magic: The Gathering Collection Dashboard")

# -------------------------
# Sidebar controls
# -------------------------
st.sidebar.header("Settings")
days = st.sidebar.selectbox("Price change window", options=[1, 7, 30], index=0)
top_n = st.sidebar.selectbox("Top N cards", options=[5, 10], index=0)

chart_granularity = st.sidebar.selectbox(
    "Portfolio chart granularity",
    options=["Weekly", "Daily", "Monthly"],
    index=1,  # default Weekly
    help="Aggregate total portfolio value over time for readability.",
)

# -------------------------
# Load data
# -------------------------
with st.spinner("Loading portfolio data..."):
    data = cast(dict[str, Any], get_dashboard_data(days=days, top_n=top_n))

latest_snapshot = cast(str, data["latest_snapshot"])
baseline_snapshot = cast(str | None, data["baseline_snapshot"])
total_value_usd = float(cast(float, data["total_value_usd"]))
num_positions = int(cast(int, data["num_positions"]))

top_holdings = cast(pd.DataFrame, data["top_holdings"])
gainers = cast(pd.DataFrame, data["gainers"])
losers = cast(pd.DataFrame, data["losers"])
rarity_breakdown = cast(pd.DataFrame, data["rarity_breakdown"])
type_breakdown = cast(pd.DataFrame, data["type_breakdown"])
portfolio_ts = cast(pd.DataFrame, data["portfolio_ts"])

# -------------------------
# KPI row
# -------------------------
c1, c2, c3 = st.columns(3)
c1.metric("Total Collection Value (USD)", f"${total_value_usd:,.2f}")
c2.metric("Positions Tracked", num_positions)
c3.metric("Latest Snapshot Date", latest_snapshot)

st.caption(
    f"Latest snapshot: {latest_snapshot} | "
    f"Baseline (for movers): {baseline_snapshot or 'N/A'} | "
    f"Window: {days} day(s)"
)

st.divider()

# -------------------------
# Hero chart: Total portfolio value over time (Daily/Weekly/Monthly toggle)
# Weekly uses explicit YYYY-Www labels to avoid repeated "Oct 2025" ticks.
# -------------------------
st.subheader("📈 Total Portfolio Value Over Time")

ts = portfolio_ts.copy()
if ts.empty or ts.shape[0] < 2:
    st.caption("Not enough history yet — add more snapshots to populate this chart.")
else:
    ts["snapshot_date"] = pd.to_datetime(ts["snapshot_date"])
    ts = ts.sort_values("snapshot_date").set_index("snapshot_date")

    if chart_granularity == "Daily":
        series = ts["total_value_usd"].dropna()
        plot_df = series.reset_index()
        plot_df.columns = ["snapshot_date", "total_value_usd"]

        chart = (
            alt.Chart(plot_df)
            .mark_line()
            .encode(
                x=alt.X(
                    "snapshot_date:T",
                    title="Date",
                    axis=alt.Axis(format="%d %b %Y", labelAngle=0),
                ),
                y=alt.Y("total_value_usd:Q", title="Total Value (USD)"),
                tooltip=[
                    alt.Tooltip("snapshot_date:T", title="Date"),
                    alt.Tooltip("total_value_usd:Q", title="Value (USD)", format=",.2f"),
                ],
            )
            .properties(height=320)
            .interactive()
        )
        st.altair_chart(chart, use_container_width=True)

    elif chart_granularity == "Weekly":
        # Week close: last snapshot in each week, week ending Monday for consistency
        weekly = ts["total_value_usd"].resample("W-MON").last().dropna()
        plot_df = weekly.reset_index()
        plot_df.columns = ["snapshot_date", "total_value_usd"]

        # Explicit labels to avoid repeated month labels
        plot_df["week_label"] = plot_df["snapshot_date"].dt.strftime("%Y-W%U")

        chart = (
            alt.Chart(plot_df)
            .mark_line()
            .encode(
                x=alt.X(
                    "week_label:N",
                    title="Week",
                    axis=alt.Axis(labelAngle=0),
                ),
                y=alt.Y("total_value_usd:Q", title="Total Value (USD)"),
                tooltip=[
                    alt.Tooltip("snapshot_date:T", title="Week ending"),
                    alt.Tooltip("total_value_usd:Q", title="Value (USD)", format=",.2f"),
                ],
            )
            .properties(height=320)
        )
        st.altair_chart(chart, use_container_width=True)

    else:  # Monthly
        # Month close: last snapshot in each month
        monthly = ts["total_value_usd"].resample("M").last().dropna()
        plot_df = monthly.reset_index()
        plot_df.columns = ["snapshot_date", "total_value_usd"]

        # Explicit labels to avoid repeated month ticks
        plot_df["month_label"] = plot_df["snapshot_date"].dt.strftime("%Y-%m")

        chart = (
            alt.Chart(plot_df)
            .mark_line()
            .encode(
                x=alt.X(
                    "month_label:N",
                    title="Month",
                    axis=alt.Axis(labelAngle=0),
                ),
                y=alt.Y("total_value_usd:Q", title="Total Value (USD)"),
                tooltip=[
                    alt.Tooltip("snapshot_date:T", title="Month ending"),
                    alt.Tooltip("total_value_usd:Q", title="Value (USD)", format=",.2f"),
                ],
            )
            .properties(height=320)
        )
        st.altair_chart(chart, use_container_width=True)


st.divider()

# -------------------------
# Top holdings
# -------------------------
st.subheader(f"🏆 Top {top_n} Cards by Value")

holdings_cols = [
    "name",
    "finish",
    "qty",
    "usd",
    "position_value_usd",
    "set_code",
    "collector_number",
]
st.dataframe(top_holdings[holdings_cols], use_container_width=True)

st.divider()

# -------------------------
# Movers
# -------------------------
st.subheader(f"📈 Movers (last {days} day(s))")
g_col, l_col = st.columns(2)

with g_col:
    st.markdown("### 🔼 Top Gainers")
    if gainers.empty:
        st.caption("No positive price movements in this window.")
    else:
        st.dataframe(
            gainers[["name", "finish", "usd_baseline", "usd", "delta_usd", "pct_change"]],
            use_container_width=True,
        )

with l_col:
    st.markdown("### 🔽 Top Losers")
    if losers.empty:
        st.caption("No negative price movements in this window.")
    else:
        st.dataframe(
            losers[["name", "finish", "usd_baseline", "usd", "delta_usd", "pct_change"]],
            use_container_width=True,
        )

st.divider()

# -------------------------
# Portfolio Breakdown
# -------------------------
st.subheader("📊 Portfolio Breakdown")
b1, b2 = st.columns(2)

with b1:
    st.markdown("### By Rarity (Value USD)")
    st.bar_chart(rarity_breakdown.set_index("rarity")["value_usd"])
    st.dataframe(rarity_breakdown, use_container_width=True)

with b2:
    st.markdown("### By Card Type (Value USD)")
    st.bar_chart(type_breakdown.set_index("type_bucket")["value_usd"])
    st.dataframe(type_breakdown, use_container_width=True)

st.caption("Prices sourced from Scryfall. Dashboard uses daily snapshots for reproducibility.")
