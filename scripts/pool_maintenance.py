#!/usr/bin/env python3
"""radar_items 풀 유지보수 — 현황 집계(--stats) + 풀 개수 상한 초과 pending archived 전환 + 픽 시효(--apply).

집계: status × collector × 나이 분포.
정리 ①: 자동수집(newsletter·newsroom·vibe_search) pending을 created_at 최신순 POOL_KEEP개만
  남기고 초과분(오래된 것) → status=archived. manual 등은 영구 보존.
정리 ② (2026-07-09 신설 — "픽은 대기실이지 보관소가 아니다", 대표 확정): picked가
  PICKED_MAX_DAYS(20일, status_updated_at 기준) 넘게 승격(묶음→초안) 없이 머물면 → archived.
  단 **묶음(cluster_items) 멤버인 픽은 보존**(작업 중 — 묶음 7일 시효가 풀리면 다음 사이클에 연쇄 소멸).
  매일 자동 실행. --apply 없으면 대상 미리보기만(DB 변경 0).

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
# MANAGED_COLLECTORS 밖(manual)은 정리 대상 아님(영구 보존).
POOL_KEEP = 50
MANAGED_COLLECTORS = {"newsletter", "newsroom", "vibe_search"}
# 픽 시효(일) — status_updated_at(픽 시점) 기준. 묶음 멤버는 면제. (2026-07-09 대표 확정: 20일)
PICKED_MAX_DAYS = 20


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"


def _hdr() -> dict:
    k = os.environ["SUPABASE_KEY"]
    return {"apikey": k, "Authorization": f"Bearer {k}"}


def fetch_all() -> list[dict]:
    r = requests.get(_base(), headers=_hdr(), params={
        "select": "id,status,collector,created_at,status_updated_at,source,title", "limit": "10000",
    }, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_cluster_member_ids() -> set:
    """묶음에 물려 있는 카드 id — 픽 시효 면제 대상(작업 중)."""
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/cluster_items"
    r = requests.get(url, headers=_hdr(), params={"select": "item_id", "limit": "10000"}, timeout=30)
    r.raise_for_status()
    return {row["item_id"] for row in r.json() if row.get("item_id")}


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
    # 초과분(오래된 것)을 archived 대상으로. manual·기타 status는 여기서 안 다룸.
    pend = [r for r in rows
            if r.get("status") == "pending" and r.get("collector") in MANAGED_COLLECTORS]
    pend.sort(key=lambda r: r.get("created_at") or "", reverse=True)  # 최신 먼저
    over = pend[POOL_KEEP:]  # POOL_KEEP 초과분 = 오래된 것
    return [(r, _age_days(r, now)) for r in over]


def _picked_age_days(row, now):
    """픽 시효 나이 — status_updated_at(픽 시점) 우선, 없으면 created_at 폴백."""
    ts = row.get("status_updated_at") or row.get("created_at")
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (now - dt).days
    except Exception:
        return None


def picked_expiry_targets(rows, now, cluster_member_ids):
    # 픽 시효: PICKED_MAX_DAYS 초과 + 묶음 미소속 → archived. 나이 미상은 보존(안전 우선).
    out = []
    for r in rows:
        if r.get("status") != "picked" or r["id"] in cluster_member_ids:
            continue
        age = _picked_age_days(r, now)
        if age is not None and age >= PICKED_MAX_DAYS:
            out.append((r, age))
    out.sort(key=lambda t: -(t[1] or 0))
    return out


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
    print(f"\n[archive 대상 ① pending 상한] {len(targets)}건 (최신 {POOL_KEEP}개 유지 · {mc} pending 대상 · manual 영구)")
    for r, age in targets[:30]:
        print(f"  [{r.get('collector')}] {age}일 | {(r.get('title') or '')[:50]}")
    if len(targets) > 30:
        print(f"  … 외 {len(targets) - 30}건")

    try:
        member_ids = fetch_cluster_member_ids()
    except Exception as e:
        member_ids = None
        print(f"\n[픽 시효] cluster_items 조회 실패 → 이번 런 픽 시효 생략(안전 우선): {e}")
    picked_targets = picked_expiry_targets(rows, now, member_ids) if member_ids is not None else []
    if member_ids is not None:
        print(f"\n[archive 대상 ② 픽 시효] {len(picked_targets)}건 (picked {PICKED_MAX_DAYS}일+ · 묶음 멤버 {len(member_ids)}건 면제)")
        for r, age in picked_targets[:30]:
            print(f"  {age}일 | {(r.get('title') or '')[:50]}")
        if len(picked_targets) > 30:
            print(f"  … 외 {len(picked_targets) - 30}건")

    if do_apply:
        all_targets = targets + picked_targets
        done = sum(1 for r, _ in all_targets if _archive(r["id"]) in (200, 204))
        print(f"\narchived 전환 완료: {done}/{len(all_targets)} (pending {len(targets)} + 픽시효 {len(picked_targets)})")
    else:
        print("\n(미리보기 — 실제 전환은 --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
