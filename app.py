import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from db import DB_NAME, init_db
from monitor import check_all

SITES_FILE = "sites.json"
CHECK_INTERVAL_HOURS = 2
REFRESH_INTERVAL_SECONDS = 60
STATUS_VALUE = {"active": 1.0, "asleep": 0.5, "inactive": 0.0}


def load_sites() -> list[dict[str, str]]:
    with open(SITES_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("sites", [])


def save_sites(sites: list[dict[str, str]]) -> None:
    with open(SITES_FILE, "w", encoding="utf-8") as f:
        json.dump({"sites": sites}, f, indent=2)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_history(history: pd.DataFrame) -> pd.DataFrame:
    parsed = history.copy()
    parsed["timestamp"] = pd.to_datetime(parsed["timestamp"], errors="coerce", utc=True)
    parsed["status_value"] = parsed["status"].map(STATUS_VALUE)
    return parsed.dropna(subset=["timestamp"])


def next_check_time(history: pd.DataFrame) -> datetime:
    if history.empty:
        return utc_now()
    latest = history["timestamp"].max()
    return latest + timedelta(hours=CHECK_INTERVAL_HOURS)


def current_snapshot(history: pd.DataFrame) -> pd.DataFrame:
    ordered = history.sort_values("timestamp")
    snapshot = ordered.groupby("name", as_index=False).tail(1)
    return snapshot.sort_values("name")


def uptime_percent(site_history: pd.DataFrame) -> float:
    if site_history.empty:
        return 0.0
    return float((site_history["status"] == "active").mean() * 100)


def inactive_events(site_history: pd.DataFrame) -> int:
    if site_history.empty:
        return 0
    series = site_history.sort_values("timestamp")["status"].fillna("unknown")
    starts = (series == "inactive") & (series.shift(1) != "inactive")
    return int(starts.sum())


st.set_page_config(page_title="Sentry Analytics", layout="wide")
st.title("Sentry Monitoring and Analytics")

init_db()

st.sidebar.header("Site Configuration")
sites = load_sites()

with st.sidebar.form("add_site_form", clear_on_submit=True):
    new_name = st.text_input("Site name")
    new_url = st.text_input("Site URL")
    add_submitted = st.form_submit_button("Add site")

if add_submitted:
    exists = any(s["name"].strip().lower() == new_name.strip().lower() for s in sites)
    if not new_name.strip() or not new_url.strip():
        st.sidebar.error("Both name and URL are required.")
    elif exists:
        st.sidebar.error("A site with this name already exists.")
    else:
        sites.append({"name": new_name.strip(), "url": new_url.strip()})
        save_sites(sites)
        st.sidebar.success("Site added.")
        st.rerun()

site_names = [s["name"] for s in sites]
remove_selection = st.sidebar.multiselect("Remove sites", options=site_names)
if st.sidebar.button("Remove selected sites", disabled=not remove_selection):
    sites = [s for s in sites if s["name"] not in remove_selection]
    save_sites(sites)
    st.sidebar.success("Selected sites removed.")
    st.rerun()

if not sites:
    st.warning("No sites configured. Add at least one site from the sidebar.")
    st.stop()

conn = sqlite3.connect(DB_NAME)
history_raw = pd.read_sql_query("SELECT * FROM uptime_logs", conn)
conn.close()

history = parse_history(history_raw) if not history_raw.empty else pd.DataFrame()
run_at = next_check_time(history) if not history.empty else utc_now()
should_check = utc_now() >= run_at

if should_check:
    check_all(sites)
    conn = sqlite3.connect(DB_NAME)
    history_raw = pd.read_sql_query("SELECT * FROM uptime_logs", conn)
    conn.close()
    history = parse_history(history_raw)
    run_at = next_check_time(history)

snapshot = current_snapshot(history) if not history.empty else pd.DataFrame(columns=["name", "url", "status", "latency", "timestamp"])

st.subheader("Current Site State")
st.dataframe(snapshot[["name", "url", "status", "latency", "timestamp"]], use_container_width=True)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Active", int((snapshot["status"] == "active").sum()) if not snapshot.empty else 0)
c2.metric("Asleep", int((snapshot["status"] == "asleep").sum()) if not snapshot.empty else 0)
c3.metric("Inactive", int((snapshot["status"] == "inactive").sum()) if not snapshot.empty else 0)
c4.metric("Next Ping UTC", run_at.strftime("%Y-%m-%d %H:%M:%S"))

if not snapshot.empty:
    inactive_now = snapshot[snapshot["status"] == "inactive"]["name"].tolist()
    asleep_now = snapshot[snapshot["status"] == "asleep"]["name"].tolist()
    if inactive_now:
        st.error(f"Inactive services: {', '.join(inactive_now)}")
    if asleep_now:
        st.warning(f"Asleep services: {', '.join(asleep_now)}")
    if not inactive_now and not asleep_now:
        st.success("All services are active.")

if history.empty:
    st.info("No history available yet. The first ping is executed immediately, then every 2 hours.")
    st.caption(f"Dashboard refreshes every {REFRESH_INTERVAL_SECONDS} seconds.")
    time.sleep(REFRESH_INTERVAL_SECONDS)
    st.rerun()

period_label = st.sidebar.selectbox("Analytics window", ["24h", "7d", "30d", "all"], index=1)
if period_label == "24h":
    cutoff = utc_now() - timedelta(hours=24)
    history_view = history[history["timestamp"] >= cutoff]
elif period_label == "7d":
    cutoff = utc_now() - timedelta(days=7)
    history_view = history[history["timestamp"] >= cutoff]
elif period_label == "30d":
    cutoff = utc_now() - timedelta(days=30)
    history_view = history[history["timestamp"] >= cutoff]
else:
    history_view = history

st.subheader("Service Reliability Summary")
rows = []
for site in sorted(history_view["name"].dropna().unique()):
    site_hist = history_view[history_view["name"] == site]
    rows.append(
        {
            "site": site,
            "checks": int(len(site_hist)),
            "uptime_percent": round(uptime_percent(site_hist), 2),
            "inactive_events": inactive_events(site_hist),
            "avg_latency_s": round(float(site_hist["latency"].dropna().mean()), 3) if site_hist["latency"].notna().any() else None,
            "p95_latency_s": round(float(site_hist["latency"].dropna().quantile(0.95)), 3) if site_hist["latency"].notna().any() else None,
            "last_status": site_hist.sort_values("timestamp").iloc[-1]["status"],
            "last_seen": site_hist["timestamp"].max(),
        }
    )
summary = pd.DataFrame(rows).sort_values(["uptime_percent", "checks"], ascending=[False, False]) if rows else pd.DataFrame()
st.dataframe(summary, use_container_width=True)

st.subheader("Uptime Trend")
for site in sorted(history_view["name"].dropna().unique()):
    site_data = history_view[history_view["name"] == site].sort_values("timestamp")
    st.caption(f"{site} status over time")
    st.line_chart(site_data.set_index("timestamp")[["status_value"]])

st.subheader("Latency Trend")
latency_data = history_view.dropna(subset=["latency"])
if latency_data.empty:
    st.info("No latency values in this window.")
else:
    for site in sorted(latency_data["name"].dropna().unique()):
        site_latency = latency_data[latency_data["name"] == site].sort_values("timestamp")
        st.caption(f"{site} latency")
        st.line_chart(site_latency.set_index("timestamp")[["latency"]])

st.subheader("Recent Ping Log")
recent = history.sort_values("timestamp", ascending=False).head(100)
st.dataframe(recent[["timestamp", "name", "url", "status", "latency"]], use_container_width=True)

st.caption(f"Ping interval is {CHECK_INTERVAL_HOURS} hours. Dashboard refreshes every {REFRESH_INTERVAL_SECONDS} seconds.")
time.sleep(REFRESH_INTERVAL_SECONDS)
st.rerun()
