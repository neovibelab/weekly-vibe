#!/usr/bin/env python3
"""radar_items 풀 유지보수 — 현황 집계(--stats) + 풀 개수 상한 초과 pending archived 전환(--apply).

집계: status × collector × 나이 분포.
정리: 자동수집(newsletter·newsroom·vibe_search) pending을 created_at 최신순 POOL_KEEP개만
  남기고 초과분(오래된 것) → status=archived. picked·manual 등은 영구 보존(여기선 pending만 다룬다).
  매일 자동 실행으로 풀을 ~POOL_KEEP개 선에서 유지. --apply 없으면 대상 미리보기만(DB 변경 0).

대시보드는 archived를 기본 뷰에서 숨긴다(app.py status!=archived·dashboard inPool).
?status=archived로 조회·복구 가능(status를 pending으로 되돌리면 부활).

env: SUPABASE_URL, SUPABASE_KEY
사용:
  python scripts/pool_maintenance.py --stats           # 분포 + archive 대상 미리보기
  python scripts/pool_maintenance.py --stats --apply    # + 실제 archived 전환
"""
import datetime
import os
import sys
from collections import Counter

import requests

# 자동수집 pending 풀에서 created_at 최신순 POOL_KEEP개만 유지, 초과분(오래된 것) → archived.
# MANAGED_COLLECTORS 밖(manual)·picked 등은 정리 대상 아님(영구 보존).
POOL_KEEP = 50
MANAGED_COLLECTORS = {"newsletter", "newsroom", "vibe_search"}


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"


def _hdr() -> dict:
    k = os.environ["SUPABASE_KEY"]
    return {"apikey": k, "Authorization": f"Bearer {k}"}


def fetch_all() -> list[dict]:
    r = requests.get(_base(), headers=_hdr(), params={
        "select": "id,status,collector,created_at,source,title", "limit": "10000",
    }, timeout=30)
    r.raise_for_status()
    return r.json()


def _age_days(row, now):
    ca = row.get("created_at")
    if not ca:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ca.replace("Z", "+00:00"))
        return (now - dt).days
    except Exception:
        return None


def _bucket(days):
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


def print_stats(rows, now):
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


def archive_targets(rows, now):
    # 자동수집 collector의 pending을 created_at 최신순 POOL_KEEP개만 남기고
    # 초과분(오래된 것)을 archived 대상으로. picked·manual·기타 status는 영구 보존.
    pend = [r for r in rows
            if r.get("status") == "pending" and r.get("collector") in MANAGED_COLLECTORS]
    pend.sort(key=lambda r: r.get("created_at") or "", reverse=True)  # 최신 먼저
    over = pend[POOL_KEEP:]  # POOL_KEEP 초과분 = 오래된 것
    return [(r, _age_days(r, now)) for r in over]


def _archive(item_id) -> int:
    r = requests.patch(
        _base(),
        headers={**_hdr(), "Content-Type": "application/json", "Prefer": "return=minimal"},
        params={"id": f"eq.{item_id}"}, json={"status": "archived"}, timeout=15,
    )
    return r.status_code


def main() -> int:
    do_apply = "--apply" in sys.argv
    rows = fetch_all()
    now = datetime.datetime.now(datetime.timezone.utc)

    if "--stats" in sys.argv:
        print_stats(rows, now)

    targets = archive_targets(rows, now)
    mc = "·".join(sorted(MANAGED_COLLECTORS))
    print(f"\n[archive 대상] {len(targets)}건 (최신 {POOL_KEEP}개 유지 · {mc} pending 대상 · picked·manual 영구)")
    for r, age in targets[:30]:
        print(f"  [{r.get('collector')}] {age}일 | {(r.get('title') or '')[:50]}")
    if len(targets) > 30:
        print(f"  … 외 {len(targets) - 30}건")

    if do_apply:
        done = sum(1 for r, _ in targets if _archive(r["id"]) in (200, 204))
        print(f"\narchived 전환 완료: {done}/{len(targets)}")
    else:
        print("\n(미리보기 — 실제 전환은 --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
