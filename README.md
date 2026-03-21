# Sentry 

Sentry monitors websites, logs status history in SQLite, and provides analytics in Streamlit.

## Status model

Each ping classifies a site as:

- `active`: HTTP 200 response
- `asleep`: request timeout
- `inactive`: non-200 response or request failure

## Timing behavior

- Ping interval: every 2 hours.
- Dashboard refresh interval: every 60 seconds.
- Dashboard performs a new ping only when the 2-hour interval is reached.

## Components

- `app.py`: dashboard, analytics, and site management UI.
- `monitor.py`: HTTP ping engine.
- `db.py`: SQLite schema and log writes.
- `scheduler.py`: optional background loop that pings every 2 hours and prints site states.
- `sites.json`: monitored sites list.

## Site management

In the dashboard sidebar:

- Add a new site by name and URL.
- Remove one or more sites.

Changes are written directly to `sites.json`.

## Run dashboard

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Run 2-hour background pinger

```bash
python scheduler.py
```

This loop checks all sites every 2 hours and prints whether each site is active, asleep, or inactive.

## Deployment files

- `.streamlit/config.toml`
- `Procfile`
- `requirements.txt`
