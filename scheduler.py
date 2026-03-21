import json
import time
from datetime import datetime, timezone

from db import init_db
from monitor import check_all

SITES_FILE = "sites.json"
INTERVAL_SECONDS = 2 * 60 * 60


def load_sites() -> list[dict[str, str]]:
    with open(SITES_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("sites", [])


def notify(results: list[dict[str, str]]) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"Ping cycle at {stamp}")
    for row in results:
        print(f"{row['name']}: {row['status']} ({row['url']})")


def run_loop() -> None:
    init_db()
    while True:
        sites = load_sites()
        if sites:
            results = check_all(sites)
            notify(results)
        else:
            print("No configured sites found in sites.json")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    run_loop()
