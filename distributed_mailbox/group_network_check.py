"""Exercise node-1 endpoints and record mailbox placement and latency.

Run this from any course node after every site has the same MAIL_SITES value.
Example: python3 group_network_check.py --base-url http://node-1:8000
"""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request(base_url: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict, float]:
    started = time.perf_counter()
    data = None if body is None else json.dumps(body).encode("utf-8")
    request_object = Request(base_url.rstrip("/") + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request_object, timeout=5) as response:
            raw = response.read()
            return response.status, json.loads(raw or b"{}"), (time.perf_counter() - started) * 1000
    except HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw or b"{}"), (time.perf_counter() - started) * 1000
    except URLError as error:
        raise RuntimeError(f"request failed: {error.reason}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Distributed mailbox group-network check")
    parser.add_argument("--base-url", required=True, help="node-1 URL, for example http://node-1:8000")
    parser.add_argument("--prefix", default="teamcheck", help="unique mailbox prefix for this run")
    parser.add_argument("--count", type=int, default=9, help="number of mailboxes to create")
    arguments = parser.parse_args()

    status, payload, latency = request(arguments.base_url, "GET", "/status")
    print(json.dumps({"check": "status", "status": status, "latency_ms": round(latency, 2), "payload": payload}))
    if status != 200:
        raise SystemExit("node-1 status check failed")

    results = []
    for number in range(arguments.count):
        user = f"{arguments.prefix}-{number}"
        message = {"from": "routing-test", "to": user, "subject": "network check", "body": "safe test message"}
        sent_status, sent_payload, sent_latency = request(arguments.base_url, "POST", "/mail", message)
        listed_status, listed_payload, listed_latency = request(arguments.base_url, "GET", f"/mailbox/{user}")
        results.append({"user": user, "send_status": sent_status, "list_status": listed_status, "site": sent_payload.get("site"), "messages": len(listed_payload.get("messages", [])), "send_latency_ms": round(sent_latency, 2), "list_latency_ms": round(listed_latency, 2)})

    print(json.dumps({"check": "mailbox_distribution", "results": results}, indent=2))
    if any(result["send_status"] != 200 or result["list_status"] != 200 or result["messages"] < 1 for result in results):
        raise SystemExit("one or more mailbox checks failed")


if __name__ == "__main__":
    main()
