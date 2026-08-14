#!/usr/bin/env python3
"""Ko‘prik staging MVP uchun xavfsiz read-only HTTP capacity probe."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def percentile(values, percent):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(
        0,
        min(len(ordered) - 1, math.ceil(percent * len(ordered)) - 1),
    )
    return ordered[index]


def fetch(base_url, path, token="", timeout=10):
    request = Request(base_url.rstrip("/") + path)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read(256)
            status = response.status
            error = ""
    except HTTPError as exc:
        status = exc.code
        error = str(exc)
    except (URLError, TimeoutError, OSError) as exc:
        status = 0
        error = str(exc)
    return {
        "path": path.split("?", 1)[0],
        "status": status,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "error": error,
    }


def run_probe(base_url, token="", concurrency=25, requests=200, timeout=10):
    paths = [
        "/api/search?" + urlencode({"q": "non", "district": "Termiz"}),
        "/api/map?" + urlencode({"district": "Termiz"}),
        "/api/home/district-offers?"
        + urlencode({"district": "Termiz"}),
    ]
    jobs = [paths[index % len(paths)] for index in range(requests)]
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [
            pool.submit(fetch, base_url, path, token, timeout)
            for path in jobs
        ]
        results = [future.result() for future in as_completed(futures)]
    latencies = [row["latency_ms"] for row in results]
    statuses = Counter(str(row["status"]) for row in results)
    server_errors = sum(
        row["status"] == 0 or row["status"] >= 500 for row in results
    )
    return {
        "base_url": base_url,
        "requests": len(results),
        "concurrency": concurrency,
        "status_distribution": dict(sorted(statuses.items())),
        "server_error_count": server_errors,
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
        },
        "passed": server_errors == 0 and percentile(latencies, 0.95) < 1000,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Ko‘prik MVP read-only HTTP load probe"
    )
    parser.add_argument("--base-url", help="Masalan: https://staging.koprik.uz")
    parser.add_argument("--token", default="", help="Ixtiyoriy Bearer token")
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Himoyalangan: bu build HTTP write probe bajarmaydi",
    )
    args = parser.parse_args()
    if not args.base_url:
        parser.error("--base-url majburiy")
    if args.allow_writes:
        parser.error(
            "Write probe faqat maxsus staging fixture bilan ishlatiladi; "
            "bu release CLI read-only."
        )
    result = run_probe(
        args.base_url,
        token=args.token,
        concurrency=args.concurrency,
        requests=args.requests,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
