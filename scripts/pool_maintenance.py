#!/usr/bin/env python3
"""radar_items 풀 유지보수 — 현황 집계(--stats). DB 변경 없음.

status × collector × 나이(주차) 분포를 출력해 정리 임계(며칠/어느 status) 결정의
근거를 제공한다. 정리(--apply)는 임계·메커니즘 합의 후 별도 추가.

env: SUPABASE_URL, SUPABASE_KEY
사용: python scripts/pool_maintenance.py --stats
"""
import datetime
import os
import sys
from collections import Counter

import requests


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"


def _hdr() -> dict:
    k = os.environ["SUPABASE_KEY"]
    return {"apikey": k, "Authorization": f"Bearer {k}"}


def fetch_all() -> list[dict]:
    r = requests.get(_base(), headers=_hdr(), params={
        "select": "status,collector,created_at,source", "limit": "10000",
    }, timeout=30)
    r.raise_for_status()
    return r.json()


def _age_days(row: dict, now: datetime.datetime):
    ca = row.get("created_at")
    if not ca:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ca.replace("Z", "+00:00"))
        return (now - dt).days
    except Exception:
        return None


def _bucket(days) -> str:
    if days is None:
        return "미상"
    if days <= 2:
        return "0-2일"
    if days < 7:
        return "3-6일"
    if days < 14:
        return "7-13일"
    if days < 30:
        return "14-29일"
    return "30일+"


def main() -> int:
    if "--stats" not in sys.argv:
        print("사용: pool_maintenance.py --stats")
        return 1
    rows = fetch_all()
    now = datetime.datetime.now(datetime.timezone.utc)
    print(f"총 {len(rows)}건\n")

    print("[status별]")
    for s, n in Counter(r.get("status") for r in rows).most_common():
        print(f"  {s}: {n}")

    print("\n[collector별]")
    for c, n in Counter(r.get("collector") for r in rows).most_common():
        print(f"  {c}: {n}")

    print("\n[나이별]")
    ab = Counter(_bucket(_age_days(r, now)) for r in rows)
    for b in ["0-2일", "3-6일", "7-13일", "14-29일", "30일+", "미상"]:
        if ab.get(b):
            print(f"  {b}: {ab[b]}")

    print("\n[status × collector]")
    for (s, c), n in Counter((r.get("status"), r.get("collector")) for r in rows).most_common():
        print(f"  {s} / {c}: {n}")

    print("\n[정리 후보 추정]")
    pend14 = sum(1 for r in rows if r.get("status") == "pending" and (_age_days(r, now) or 0) >= 14)
    filt = sum(1 for r in rows if r.get("status") == "filtered_out")
    rej = sum(1 for r in rows if r.get("status") == "rejected")
    aux_old = sum(1 for r in rows
                  if r.get("collector") in ("newsletter", "newsroom")
                  and (_age_days(r, now) or 0) >= 7
                  and r.get("status") == "pending")
    print(f"  pending 14일+: {pend14}")
    print(f"  보조수집기(newsletter·newsroom) pending 7일+: {aux_old}")
    print(f"  filtered_out 전체: {filt}")
    print(f"  rejected 전체: {rej}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
