#!/usr/bin/env python3
"""Read-only Datadog queries for unattended on-call runs, with the saved keys.

A background `claude -p` run cannot use the Datadog plugin's tools (they need an
interactive login), so the runner allows this script instead. Every command
reads; nothing writes to Datadog. The keys come from DD_API_KEY / DD_APP_KEY,
which the runner sets, and are never printed.

    dd.py monitors                                   monitors in alert, warn or no data now
    dd.py logs "<query>" [--minutes 60] [--limit 25]  matching log lines, newest first
    dd.py logs-count "<query>" --by <facet> [--minutes 60]
                                                      matching logs counted per value of a facet
    dd.py metric "<metric query>" [--minutes 120]     each series: min, max, last, and a few points
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any, Callable

API = "https://api.datadoghq.com/api"
Opener = Callable[[urllib.request.Request], Any]


def _urlopen(request: urllib.request.Request) -> Any:
    return urllib.request.urlopen(request, timeout=60)


class Client:
    def __init__(self, opener: Opener = _urlopen) -> None:
        api, app = os.environ.get("DD_API_KEY"), os.environ.get("DD_APP_KEY")
        if not api or not app:
            raise SystemExit("DD_API_KEY and DD_APP_KEY are not set")
        self.headers = {"DD-API-KEY": api, "DD-APPLICATION-KEY": app, "Content-Type": "application/json"}
        self.opener = opener

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{API}/{path}", data=data, headers=self.headers, method=method)
        with self.opener(request) as response:
            return json.load(response)


def stamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        at = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
    else:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return at.strftime("%m-%d %H:%M:%S UTC")


def cmd_monitors(client: Client) -> int:
    query = urllib.parse.urlencode({"query": 'status:(alert OR warn OR "no data")', "per_page": 100})
    result = client.request("GET", f"v1/monitor/search?{query}")
    rows = result.get("monitors") or []
    for monitor in rows:
        print(f"{monitor.get('status', '?'):8} {monitor.get('id')}  {monitor.get('name')}")
    print(f"\n{len(rows)} monitors not OK")
    return 0


def cmd_logs(client: Client, query: str, minutes: int, limit: int) -> int:
    body = {
        "filter": {"query": query, "from": f"now-{minutes}m", "to": "now"},
        "sort": "-timestamp",
        "page": {"limit": min(limit, 200)},
    }
    rows = client.request("POST", "v2/logs/events/search", body).get("data") or []
    for row in rows:
        attrs = row.get("attributes") or {}
        extra = attrs.get("attributes") or {}
        keys = ("error_type", "error_message", "organization_id", "provider", "task", "queue")
        detail = " ".join(f"{key}={extra[key]}" for key in keys if extra.get(key) not in (None, ""))
        message = " ".join(str(attrs.get("message") or "").split())[:300]
        print(f"{stamp(attrs.get('timestamp'))} {attrs.get('service', '?')} {attrs.get('status', '?')}: {message}")
        if detail:
            print(f"    {detail[:300]}")
    print(f"\n{len(rows)} log lines (newest first, last {minutes} minutes)")
    return 0


def cmd_logs_count(client: Client, query: str, facet: str, minutes: int) -> int:
    body = {
        "filter": {"query": query, "from": f"now-{minutes}m", "to": "now"},
        "compute": [{"aggregation": "count", "type": "total"}],
        "group_by": [
            {"facet": facet, "limit": 25, "sort": {"aggregation": "count", "order": "desc", "type": "measure"}}
        ],
    }
    buckets = (client.request("POST", "v2/logs/analytics/aggregate", body).get("data") or {}).get("buckets") or []
    for bucket in buckets:
        value = (bucket.get("by") or {}).get(facet)
        print(f"{(bucket.get('computes') or {}).get('c0', 0):>8}  {value}")
    print(f"\n{len(buckets)} values of {facet} (last {minutes} minutes)")
    return 0


def cmd_metric(client: Client, query: str, minutes: int) -> int:
    now = int(time.time())
    params = urllib.parse.urlencode({"from": now - minutes * 60, "to": now, "query": query})
    series = client.request("GET", f"v1/query?{params}").get("series") or []
    for one in series:
        points = [(at, value) for at, value in one.get("pointlist") or [] if value is not None]
        if not points:
            print(f"{one.get('scope')}: no points")
            continue
        values = [value for _, value in points]
        step = max(1, len(points) // 8)
        sample = ", ".join(f"{stamp(at)[6:11]} {value:g}" for at, value in points[::step])
        print(f"{one.get('scope')}: min {min(values):g}, max {max(values):g}, last {values[-1]:g}")
        print(f"    {sample}")
    print(f"\n{len(series)} series (last {minutes} minutes, times UTC)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("monitors")
    logs = sub.add_parser("logs")
    logs.add_argument("query")
    logs.add_argument("--minutes", type=int, default=60)
    logs.add_argument("--limit", type=int, default=25)
    count = sub.add_parser("logs-count")
    count.add_argument("query")
    count.add_argument("--by", required=True)
    count.add_argument("--minutes", type=int, default=60)
    metric = sub.add_parser("metric")
    metric.add_argument("query")
    metric.add_argument("--minutes", type=int, default=120)
    args = parser.parse_args(argv)
    client = Client()
    try:
        return run(client, args)
    except urllib.error.HTTPError as exc:
        # Datadog says what was wrong with the query; show it so the next try can fix it.
        print(f"datadog {exc.code}: {exc.read()[:500].decode(errors='replace')}", file=sys.stderr)
        return 1


def run(client: Client, args: argparse.Namespace) -> int:
    if args.command == "monitors":
        return cmd_monitors(client)
    if args.command == "logs":
        return cmd_logs(client, args.query, args.minutes, args.limit)
    if args.command == "logs-count":
        return cmd_logs_count(client, args.query, args.by, args.minutes)
    return cmd_metric(client, args.query, args.minutes)


if __name__ == "__main__":
    sys.exit(main())
