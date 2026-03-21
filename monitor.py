import time
from typing import Any, Dict, List

import requests

from db import log_status

TIMEOUT_SECONDS = 5


def check_site(site: Dict[str, str]) -> Dict[str, Any]:
    name = site["name"]
    url = site["url"]
    start = time.time()
    try:
        res = requests.get(url, timeout=TIMEOUT_SECONDS)
        latency = round(time.time() - start, 2)
        status = "active" if res.status_code == 200 else "inactive"
    except requests.exceptions.Timeout:
        status = "asleep"
        latency = None
    except requests.RequestException:
        status = "inactive"
        latency = None
    log_status(name, url, status, latency)
    return {"name": name, "url": url, "status": status, "latency": latency}


def check_all(sites: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    return [check_site(s) for s in sites]
