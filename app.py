"""Run with: streamlit run app.py"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from carbon_window.analysis import daily, latest, plan
from carbon_window.evaluate import evaluate
from carbon_window.pipeline import DB, ROOT, collect, floor_slot, save_snapshot, utc

st.set_page_config(page_title="Carbon Window", page_icon="◒", layout="wide")
alt.data_transformers.disable_max_rows()
st.markdown("""<style>
.block-container {max-width:1180px; padding-top:4.2rem; padding-bottom:3rem;}
h1 {letter-spacing:-.055em; font-weight:750!important; font-size:3.2rem!important;}
h2 {letter-spacing:-.025em;}
[data-testid="stMetric"] {border:1px solid #dbe4da; border-radius:12px; padding:18px; background:white;}
[data-testid="stMetricLabel"] {color:#60766c;}
.eyebrow {font-size:.76rem;letter-spacing:.17em;color:#13765a;font-weight:700;}
.intro {font-size:1.16rem;color:#5c7167;max-width:780px;line-height:1.65;margin-bottom:1.5rem;}
.window {background:#123f33;color:#fff;padding:26px 30px;border-radius:16px;margin:10px 0 22px;}
.window small {color:#b4dace;letter-spacing:.1em;text-transform:uppercase;}
.window strong {display:block;font-size:2rem;line-height:1.3;margin:10px 0;letter-spacing:-.025em;}
.window p {color:#d0e5db;margin:0;}
</style>""", unsafe_allow_html=True)

if not DB.exists():
    demo = ROOT / "data/demo.json"
    if not demo.exists():
        st.info("No snapshot yet. Run `python -m carbon_window.pipeline --days 90 --export-demo` to collect the public data.")
        st.stop()
    save_snapshot(json.loads(demo.read_text()), DB)

data, run = latest()
captured = utc(run["fetched_at"])
wall_now = datetime.now(timezone.utc)
with st.sidebar:
    st.markdown("### ◒ Carbon Window")
    st.caption("A small experiment in better timing.")
    st.divider()
    mode = st.radio("Planning clock", ["Explore this snapshot", "Plan from now"])
    st.caption(f"Data fetched: {captured.astimezone().strftime('%d %b %Y, %H:%M %Z')}")
    if st.button("Refresh public data", width="stretch"):
        try:
            with st.spinner("Collecting and checking 90 days of electricity data…"):
                collect(90)
            st.rerun()
        except Exception as exc:
            st.error(f"Refresh failed. Your previous snapshot is still available. {exc}")
    st.divider()
    st.markdown("**Coverage**\n\nGreat Britain · national average\n\nHalf-hour intervals · gCO₂/kWh")
    st.caption("Data: NESO Carbon Intensity API · CC BY 4.0. Includes generation-related CO₂; not electricity prices or a full lifecycle footprint.")
    st.link_button("About the source", "https://carbonintensity.org.uk/")

st.markdown('<div class="eyebrow">ELECTRICITY · CARBON · CURIOSITY</div>', unsafe_allow_html=True)
st.title("Same activity. Better timing.")
st.markdown('<div class="intro">Britain’s electricity changes through the day. Explore the patterns, find a cleaner window and see how much the forecast can tell us.</div>', unsafe_allow_html=True)

snapshot_mode = mode == "Explore this snapshot"
reference = captured if snapshot_mode else wall_now
if snapshot_mode:
    st.info(f"Snapshot explorer · planning as of {captured.strftime('%d %b %Y, %H:%M UTC')}. This is a recorded forecast, not a live recommendation.")
elif wall_now-captured > timedelta(hours=2):
    st.warning("This forecast was fetched over two hours ago. Refresh public data before planning from now.")

planner, history, experiment, reliability = st.tabs(["Find a window", "Explore the data", "Model experiment", "Data quality"])


def local_label(value):
    return pd.Timestamp(value).tz_convert("Europe/London").strftime("%a %d %b · %H:%M %Z")


def line_chart(frame, fields, height=270):
    frame = frame.copy()
    frame["utc_label"] = frame.start_utc.dt.strftime("%d %b %Y %H:%M UTC")
    chart_data = frame.melt(id_vars=["start_utc", "utc_label"], value_vars=fields, var_name="series", value_name="intensity")
    return alt.Chart(chart_data).mark_line(strokeWidth=2.5).encode(
        x=alt.X("start_utc:T", title="Time (UTC)", scale=alt.Scale(type="utc")),
        y=alt.Y("intensity:Q", title="gCO₂ / kWh", scale=alt.Scale(zero=True)),
        color=alt.Color("series:N", scale=alt.Scale(range=["#13765a", "#d39d4d"]), title=None),
        tooltip=[alt.Tooltip("utc_label:N", title="Time"), "series:N", alt.Tooltip("intensity:Q", format=".1f")]
    ).properties(height=height).configure_view(stroke=None)


with planner:
    st.subheader("When could you move it?")
    a,b,c,d = st.columns(4)
    minutes = a.select_slider("Activity duration", options=[30,60,90,120,180,240], value=120,
                              format_func=lambda x: f"{x/60:g} hours")
    energy = b.number_input("Total energy (kWh)", min_value=0.1, max_value=100.0, value=1.0, step=0.1)
    delay = c.selectbox("Earliest start", [0,1,2,4,8,12,24], format_func=lambda x: "Next half-hour" if x==0 else f"In {x} hours")
    horizon = d.selectbox("Finish within", [6,12,24,36,48], index=2, format_func=lambda x: f"{x} hours")
    earliest = floor_slot(reference) + timedelta(minutes=30, hours=delay)
    deadline = reference + timedelta(hours=horizon)
    recommendation = plan(data, earliest, deadline, minutes, energy)
    fresh = snapshot_mode or wall_now-captured <= timedelta(hours=2)
    if not fresh:
        st.info("Planning is paused until a fresh forecast is available. The history and experiment remain usable.")
    elif recommendation is None:
        st.warning("No complete window fits these settings and the available forecast. Try an earlier start, a shorter activity or a later finish.")
    else:
        best, first = recommendation["best"], recommendation["first"]
        st.markdown(f'<div class="window"><small>Lowest forecast average in your available window</small><strong>{local_label(best["start"])} → {pd.Timestamp(best["end"]).tz_convert("Europe/London").strftime("%H:%M %Z")}</strong><p>{minutes/60:g} hours · {best["intensity"]:.0f} gCO₂/kWh · {recommendation["candidates"]} complete windows compared</p></div>', unsafe_allow_html=True)
        a,b,c = st.columns(3)
        a.metric("Estimated CO₂ · selected window", f'{recommendation["best_g"]:.0f} g')
        b.metric("Estimated CO₂ · earliest window", f'{recommendation["first_g"]:.0f} g')
        c.metric("Estimated difference", f'{recommendation["difference_g"]:.0f} g', f'{recommendation["difference_pct"]:.0f}% lower', delta_color="off")
        st.caption(f"Compared with {local_label(first['start'])}. Assumes {energy:g} kWh spread evenly through the activity. This is average-intensity accounting, not a measured emissions saving or a bill saving.")
    forward = data[(data.start_utc >= floor_slot(reference)) & (data.start_utc < deadline)]
    if not forward.empty:
        st.altair_chart(line_chart(forward, ["forecast"]), width="stretch")
    with st.expander("How the calculation works"):
        st.markdown("Each candidate needs consecutive half-hours with a forecast. The app averages their intensities, then multiplies by the activity’s total kWh. Gaps are excluded, not filled. Equal scores choose the earlier window. The input is **NESO’s forecast**, not a prediction from this project’s experimental model.")

with history:
    st.subheader("How variable is the grid?")
    days = st.selectbox("History to display", [7,30,60,90], index=1)
    historical = data[(data.end_utc <= pd.Timestamp(captured)) & (data.start_utc >= pd.Timestamp(captured)-pd.Timedelta(days=days))]
    observed = historical.dropna(subset=["actual"])
    if observed.empty:
        st.info("There are no actual estimates in this period.")
    else:
        a,b,c = st.columns(3)
        a.metric("Average estimated actual", f"{observed.actual.mean():.0f} gCO₂/kWh")
        b.metric("Half-hours with an actual estimate", f"{len(observed):,}")
        c.metric("10th–90th percentile", f"{observed.actual.quantile(.1):.0f}–{observed.actual.quantile(.9):.0f}")
        st.altair_chart(line_chart(historical, ["actual", "forecast"], 320), width="stretch")
        st.caption("‘Actual’ is the provider’s estimate from metered generation. The archived forecast is shown for context; its original issue time is not supplied here.")
        heat = observed.copy()
        local = heat.start_utc.dt.tz_convert("Europe/London")
        heat["weekday"], heat["hour"] = local.dt.day_name(), local.dt.hour
        heat = heat.groupby(["weekday","hour"]).actual.agg(["mean","count"]).reset_index()
        st.markdown("#### Does time of day matter?")
        st.altair_chart(alt.Chart(heat).mark_rect(cornerRadius=2).encode(
            x=alt.X("hour:O", title="Hour · Europe/London"),
            y=alt.Y("weekday:N", sort=["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"], title=None),
            color=alt.Color("mean:Q", title="gCO₂/kWh", scale=alt.Scale(scheme="yellowgreenblue")),
            tooltip=["weekday","hour",alt.Tooltip("mean:Q",format=".1f"),"count:Q"]
        ).properties(height=220), width="stretch")
        st.caption("Historical averages describe this sample. They do not guarantee that the same hour will be cleanest tomorrow.")
        st.download_button("Download daily SQL summary", daily().to_csv(index=False), "carbon-window-daily.csv", "text/csv")

with experiment:
    st.subheader("Does complexity earn its place?")
    st.write("A calendar-only gradient-boosting model competes with two simple baselines on three later weeks. Training always precedes testing; no random train/test shuffle.")
    report_path = ROOT / "reports/evaluation.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    if st.button("Evaluate this snapshot"):
        try:
            with st.spinner("Running three chronological folds…"):
                report = evaluate(data, run)
                report_path.parent.mkdir(exist_ok=True)
                report_path.write_text(json.dumps(report, indent=2) + "\n")
        except ValueError as exc:
            st.error(str(exc))
    if report and report["run_id"] == run["run_id"]:
        table = pd.DataFrame(report["summary"]).rename(columns={"model":"Model", "mae":"MAE", "rmse":"RMSE", "bias":"Bias"})
        st.dataframe(table, hide_index=True, width="stretch", column_config={x:st.column_config.NumberColumn(format="%.2f") for x in ["MAE","RMSE","Bias"]})
        st.caption("gCO₂/kWh · MAE and RMSE: lower is better. Bias: positive means overprediction. Values are the arithmetic mean of the three weekly scores.")
        winner = min(report["summary"], key=lambda x:x["mae"])
        st.success(f"Lowest mean error in this run: {winner['model']} ({winner['mae']:.2f} MAE). This is a result for this sample, not a universal winner.")
        with st.expander("Inspect every fold"):
            st.dataframe(pd.DataFrame(report["folds"]), hide_index=True)
        st.markdown("**What this experiment can’t establish**")
        for limitation in report["limitations"]:
            st.write(f"• {limitation}")
    else:
        st.info("This snapshot has no matching evaluation yet. Run the experiment to produce checked results.")

with reliability:
    st.subheader("A result is only as good as its inputs.")
    q = run["manifest"]["quality"]
    a,b,c = st.columns(3)
    a.metric("Intervals returned", f"{q['observed_intervals']:,}")
    b.metric("Missing intervals", f"{q['missing_intervals']:,}")
    c.metric("Missing historical actuals", f"{q['historical_actual_missing']:,}")
    st.caption("Historical missingness uses a two-hour reporting grace period; missing forecasts and missing half-hour rows are counted separately.")
    st.dataframe(pd.DataFrame([{"Check":k.replace("_"," "), "Value":v} for k,v in q.items()]), hide_index=True)
    with st.expander("Source requests and content hashes"):
        st.dataframe(pd.DataFrame(run["manifest"]["requests"]), hide_index=True)
        st.code(f"Snapshot ID: {run['run_id']}\nFetched: {run['fetched_at']}\nSource: {run['source']}", language=None)
    st.markdown("**Guardrails in the pipeline**\n\nTimestamps must be timezone-aware half-hours. Negative, infinite and conflicting duplicate values are rejected. A failed refresh leaves the previous database run intact. Missing values remain missing.")
    st.download_button("Download this snapshot as CSV", data.to_csv(index=False), "carbon-window-snapshot.csv", "text/csv")
    st.markdown("[API documentation](https://carbon-intensity.github.io/api-definitions/) · [Data licence: CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)")

st.divider()
st.caption("Carbon Window · Great Britain’s electricity, one half-hour at a time. Data from NESO and its Carbon Intensity API partners. Analysis and interface are independent of NESO.")
