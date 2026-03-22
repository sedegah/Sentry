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


def asleep_events(site_history: pd.DataFrame) -> int:
    if site_history.empty:
        return 0
    series = site_history.sort_values("timestamp")["status"].fillna("unknown")
    starts = (series == "asleep") & (series.shift(1) != "asleep")
    return int(starts.sum())


def build_status_mix(history_view: pd.DataFrame) -> pd.DataFrame:
    if history_view.empty:
        return pd.DataFrame()
    rows = []
    for site in sorted(history_view["name"].dropna().unique()):
        site_hist = history_view[history_view["name"] == site]
        total = len(site_hist)
        if total == 0:
            continue
        status_counts = site_hist["status"].value_counts(dropna=False)
        rows.append(
            {
                "site": site,
                "checks": int(total),
                "active_pct": round(float(status_counts.get("active", 0) / total * 100), 2),
                "asleep_pct": round(float(status_counts.get("asleep", 0) / total * 100), 2),
                "inactive_pct": round(float(status_counts.get("inactive", 0) / total * 100), 2),
                "unknown_pct": round(float(status_counts.get("unknown", 0) / total * 100), 2),
            }
        )
    return pd.DataFrame(rows).sort_values(["inactive_pct", "asleep_pct"], ascending=[False, False]) if rows else pd.DataFrame()


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
    latency_values = site_hist["latency"].dropna()
    active_ratio = float((site_hist["status"] == "active").mean()) if len(site_hist) else 0.0
    inactive_count = inactive_events(site_hist)
    asleep_count = asleep_events(site_hist)
    rows.append(
        {
            "site": site,
            "checks": int(len(site_hist)),
            "uptime_percent": round(active_ratio * 100, 2),
            "active_checks": int((site_hist["status"] == "active").sum()),
            "inactive_events": inactive_count,
            "asleep_events": asleep_count,
            "issue_events_total": inactive_count + asleep_count,
            "error_budget_burn_percent": round((1.0 - active_ratio) * 100, 2),
            "avg_latency_s": round(float(latency_values.mean()), 3) if not latency_values.empty else None,
            "p50_latency_s": round(float(latency_values.quantile(0.50)), 3) if not latency_values.empty else None,
            "p95_latency_s": round(float(latency_values.quantile(0.95)), 3) if not latency_values.empty else None,
            "max_latency_s": round(float(latency_values.max()), 3) if not latency_values.empty else None,
            "last_status": site_hist.sort_values("timestamp").iloc[-1]["status"],
            "last_seen": site_hist["timestamp"].max(),
        }
    )
summary = (
    pd.DataFrame(rows).sort_values(["uptime_percent", "checks"], ascending=[False, False])
    if rows
    else pd.DataFrame()
)
st.dataframe(summary, use_container_width=True)

if not summary.empty:
    st.subheader("Operational Analytics")
    total_checks = int(summary["checks"].sum())
    total_issues = int(summary["issue_events_total"].sum())
    fleet_uptime = round(float(summary["active_checks"].sum() / total_checks * 100), 2) if total_checks else 0.0
    least_reliable = summary.sort_values(["uptime_percent", "checks"], ascending=[True, False]).iloc[0]["site"]
    noisiest = summary.sort_values(["issue_events_total", "inactive_events"], ascending=[False, False]).iloc[0]["site"]

    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Fleet Uptime %", fleet_uptime)
    g2.metric("Total Checks", total_checks)
    g3.metric("Issue Events", total_issues)
    g4.metric("Most Incident-Prone Site", noisiest)

    st.caption(f"Lowest uptime in this window: {least_reliable}")

    top_risk = summary.sort_values(["uptime_percent", "issue_events_total"], ascending=[True, False]).head(5)[
        ["site", "uptime_percent", "error_budget_burn_percent", "issue_events_total", "last_status", "last_seen"]
    ]
    st.markdown("**Top Risk Sites (action priority)**")
    st.dataframe(top_risk, use_container_width=True)

    status_mix = build_status_mix(history_view)
    st.markdown("**Status Distribution by Site (%)**")
    if status_mix.empty:
        st.info("No status distribution data is available in this window.")
    else:
        st.dataframe(status_mix, use_container_width=True)

    latency_rank = summary.dropna(subset=["p95_latency_s"]).sort_values(
        ["p95_latency_s", "avg_latency_s"], ascending=[False, False]
    )
    st.markdown("**Latency Risk Ranking (P95)**")
    if latency_rank.empty:
        st.info("No latency values are available for ranking in this window.")
    else:
        st.dataframe(
            latency_rank[["site", "avg_latency_s", "p50_latency_s", "p95_latency_s", "max_latency_s", "last_status"]],
            use_container_width=True,
        )

st.subheader("Recent Ping Log")
recent = history.sort_values("timestamp", ascending=False).head(100)
st.dataframe(recent[["timestamp", "name", "url", "status", "latency"]], use_container_width=True)

st.caption(f"Ping interval is {CHECK_INTERVAL_HOURS} hours. Dashboard refreshes every {REFRESH_INTERVAL_SECONDS} seconds.")
time.sleep(REFRESH_INTERVAL_SECONDS)
st.rerun()
