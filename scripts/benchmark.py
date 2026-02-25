#!/usr/bin/env python3
"""
Quick benchmark for Admitverse CRM API endpoints.

Usage:
  1. Start the backend: python -m uvicorn app.main:app --reload
  2. Login to get a JWT token, or pass one directly
  3. Run: python scripts/benchmark.py <ACCESS_TOKEN>
     Or:  python scripts/benchmark.py --login <email> <password>
"""
import sys
import time
import httpx

BASE = "http://localhost:8000"

ENDPOINTS = [
    ("GET",  "/health", False),
    ("GET",  "/api/v1/users/me", True),
    ("GET",  "/api/v1/reports/dashboard", True),
    ("GET",  "/api/v1/reports/pipeline", True),
    ("GET",  "/api/v1/reports/agents", True),
    ("GET",  "/api/v1/reports/sources", True),
    ("GET",  "/api/v1/reports/tasks/compliance", True),
    ("GET",  "/api/v1/reports/trends?days=30", True),
    ("GET",  "/api/v1/leads?page=1&page_size=25", True),
    ("GET",  "/api/v1/notifications/unread-count", True),
]


def login(email: str, password: str) -> str:
    resp = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": email, "password": password}, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


def run(token: str):
    headers = {"Authorization": f"Bearer {token}"}
    results = []

    print(f"\nBenchmarking {BASE} ...\n")

    for method, path, needs_auth in ENDPOINTS:
        h = headers if needs_auth else {}
        url = f"{BASE}{path}"

        start = time.perf_counter()
        try:
            resp = httpx.request(method, url, headers=h, timeout=60)
            elapsed = (time.perf_counter() - start) * 1000
            server_time = resp.headers.get("x-response-time", "n/a")
            results.append((path, resp.status_code, elapsed, server_time))
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            results.append((path, f"ERR", elapsed, str(e)[:30]))

    print(f"{'Endpoint':<45} {'Status':<8} {'Round-trip':<12} {'Server':<12}")
    print("-" * 77)
    for path, status, client_ms, server_ms in results:
        flag = "  !!!" if isinstance(client_ms, float) and client_ms > 1000 else ""
        print(f"{path:<45} {status:<8} {client_ms:>8.0f}ms   {server_ms:<12}{flag}")

    print()
    total = sum(r[2] for r in results if isinstance(r[2], float))
    print(f"Total round-trip: {total:.0f}ms")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--login":
        token = login(sys.argv[2], sys.argv[3])
        print(f"Logged in. Token: {token[:20]}...")
    elif len(sys.argv) >= 2:
        token = sys.argv[1]
    else:
        print("Usage:")
        print("  python scripts/benchmark.py <ACCESS_TOKEN>")
        print("  python scripts/benchmark.py --login <email> <password>")
        sys.exit(1)
    run(token)
