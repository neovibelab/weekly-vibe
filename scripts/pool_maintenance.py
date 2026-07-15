#!/usr/bin/env python3
"""radar_items 풀 유지보수 — 현황 집계(--stats) + pending 상한 + 픽 시효 + 묶음 시의성 시효(--apply).

집계: status × collector × 나이 분포.
정리 ①: 자동수집(newsletter·newsroom·vibe_search) pending을 created_at 최신순 POOL_KEEP개만
  남기고 초과분(오래된 것) → status=archived. manual 등은 영구 보존.
정리 ② (2026-07-09 신설 — "픽은 대기실이지 보관소가 아니다", 대표 확정): picked가
  PICKED_MAX_DAYS(20일, status_updated_at 기준) 넘게 승격(묶음→초안) 없이 머물면 → archived.
  단 **에버그린·to_draft/drafted 묶음 멤버인 픽은 보존**(2026-07-15 축소 — 구 "모든 묶음 멤버
  면제"는 suggest가 매 실행 같은 옛 픽으로 묶음을 재생성해 옛 픽이 영생하는 루프였음).
정리 ③ (2026-07-15 신설 — 묶음 2종 수명제, 대표 결정): 시의성 묶음(evergreen=false,
  status open·synthesized)이 CLUSTER_MAX_IDLE_DAYS(10일, updated_at 기준) 넘게 방치되면
  묶음+멤버링크 삭제. 에버그린 묶음·to_draft/drafted는 영구 보존. v12 마이그레이션
  (clusters.evergreen) 미적용이면 ③은 안전하게 생략(전 묶음 보호 = 구 동작 유지).
  매일 자동 실행. --apply 없으면 대상 미리보기만(DB 변경 0).

대시보드는 archived를 기본 뷰에서 숨긴다(app.py status!=archived·dashboard inPool).
?status=archived로 조회·복구 가능(status를 pending으로 되돌리면 부활).

env: SUPABASE_URL, SUPABASE_KEY
사용:
  python scripts/pool_maintenance.py --stats           # 분포 + 정리 대상 미리보기
  python scripts/pool_maintenance.py --stats --apply    # + 실제 전환·삭제
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
# 픽 시효(일) — status_updated_at(픽 시점) 기준. 보호 묶음 멤버는 면제. (2026-07-09 대표 확정: 20일)
PICKED_MAX_DAYS = 20
# 시의성 묶음 시효(일) — updated_at 기준. 에버그린·to_draft/drafted는 면제. (2026-07-15 대표 확정: 10일)
CLUSTER_MAX_IDLE_DAYS = 10
CLUSTER_EXPIRABLE_STATUS = {"open", "synthesized"}
CLUSTER_PROTECTED_STATUS = {"to_draft", "drafted"}


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"


def _hdr() -> dict:
    k = os.environ["SUPABASE_KEY"]
    return {"apikey": k, "Authorization": f"Bearer {k}"}


def _fetch_paged(url: str, params: dict) -> list[dict]:
    # PostgREST는 limit과 무관하게 서버 max-rows(기본 1,000)로 응답을 자른다 —
    # 테이블이 1,000행을 넘으면 부분 데이터로 계산해 정리 대상을 놓친다(2026-07-14 실측).
    # Range 헤더로 전 행을 페이지 순회. 고정 정렬로 페이지 간 중복·누락 방지
    # (기본 id.asc, id 없는 테이블은 params의 order가 우선 — cluster_items는 복합 PK).
    out: list[dict] = []
    page = 1000
    lo = 0
    while True:
        r = requests.get(url, headers={**_hdr(), "Range": f"{lo}-{lo + page - 1}"},
                         params={"order": "id.asc", **params}, timeout=30)
        r.raise_for_status()
        rows = r.json()
        out.extend(rows)
        if len(rows) < page:
            return out
        lo += page


def fetch_all() -> list[dict]:
    return _fetch_paged(_base(), {
        "select": "id,status,collector,created_at,status_updated_at,source,title",
    })


def fetch_clusters() -> list[dict]:
    """묶음 전체 — 시의성 시효 판정·픽 면제 판정용."""
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/clusters"
    return _fetch_paged(url, {"select": "*"})


def fetch_cluster_links() -> list[dict]:
    """cluster_items 전체 — cluster_id 기준 정렬(복합 PK라 id 컬럼 없음)."""
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/cluster_items"
    return _fetch_paged(url, {"select": "cluster_id,item_id", "order": "cluster_id.asc"})


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


def _cluster_idle_days(c, now):
    ts = c.get("updated_at") or c.get("created_at")
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (now - dt).days
    except Exception:
        return None


def cluster_expiry_targets(clusters, now):
    """시의성 묶음 시효 — evergreen=false + open/synthesized + 10일 방치 → 삭제 대상.
    v12 마이그레이션 전(evergreen 컬럼 없음)이면 빈 목록(전 묶음 보호 = 구 동작)."""
    if clusters and "evergreen" not in clusters[0]:
        print("[묶음 시효] clusters.evergreen 없음(v12 미적용) → 이번 런 묶음 시효 생략(안전 우선)")
        return []
    out = []
    for c in clusters:
        if c.get("evergreen") or c.get("status") not in CLUSTER_EXPIRABLE_STATUS:
            continue
        idle = _cluster_idle_days(c, now)
        if idle is not None and idle >= CLUSTER_MAX_IDLE_DAYS:
            out.append((c, idle))
    out.sort(key=lambda t: -(t[1] or 0))
    return out


def protected_member_ids(clusters, links, expired_ids):
    """픽 시효 면제 대상 = 보호 묶음(에버그린 또는 to_draft/drafted, 시효 삭제분 제외)의 멤버.
    v12 전(evergreen 컬럼 없음)이면 전 묶음 보호(구 동작 유지)."""
    pre_migration = bool(clusters) and "evergreen" not in clusters[0]
    protected = {c["id"] for c in clusters
                 if c["id"] not in expired_ids
                 and (pre_migration or c.get("evergreen") or c.get("status") in CLUSTER_PROTECTED_STATUS)}
    return {l["item_id"] for l in links if l.get("item_id") and l.get("cluster_id") in protected}


def _delete_cluster(cid) -> bool:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/"
    h = {**_hdr(), "Prefer": "return=minimal"}
    r1 = requests.delete(base + "cluster_items", headers=h, params={"cluster_id": f"eq.{cid}"}, timeout=15)
    r2 = requests.delete(base + "clusters", headers=h, params={"id": f"eq.{cid}"}, timeout=15)
    return r1.status_code in (200, 204) and r2.status_code in (200, 204)


def picked_expiry_targets(rows, now, cluster_member_ids):
    # 픽 시효: PICKED_MAX_DAYS 초과 + 묶음 미소속 → archived. 나이 미상은 보존(안전 우선).
    # collector='interview'는 면제 — 인터뷰는 에버그린 소재라 소스 뱅크 이관 전까지 픽 보존
    #   (2026-07-09 인터뷰 수집기 신설). 20일 시효는 뉴스성 픽에만 적용.
    out = []
    for r in rows:
        if (r.get("status") != "picked" or r["id"] in cluster_member_ids
                or r.get("collector") == "interview"):
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

    # 묶음 시의성 시효 (정리 ③) + 픽 시효 (정리 ②) — clusters/cluster_items 조회 실패 시 둘 다 생략(안전 우선)
    try:
        clusters = fetch_clusters()
        links = fetch_cluster_links()
    except Exception as e:
        clusters = links = None
        print(f"\n[묶음·픽 시효] clusters/cluster_items 조회 실패 → 이번 런 생략(안전 우선): {e}")

    cluster_targets = cluster_expiry_targets(clusters, now) if clusters is not None else []
    if clusters is not None:
        ever_n = sum(1 for c in clusters if c.get("evergreen"))
        print(f"\n[삭제 대상 ③ 묶음 시효] {len(cluster_targets)}건 (시의성 {CLUSTER_MAX_IDLE_DAYS}일+ 방치 · "
              f"전체 {len(clusters)}건 중 에버그린 {ever_n}건·to_draft/drafted 면제)")
        for c, idle in cluster_targets:
            print(f"  {idle}일 방치 [{c.get('status')}] {(c.get('title') or '')[:50]}")

    picked_targets = []
    member_ids = set()
    if clusters is not None and links is not None:
        expired_ids = {c["id"] for c, _ in cluster_targets}  # 미리보기에서도 시효분 제외하고 면제 계산
        member_ids = protected_member_ids(clusters, links, expired_ids)
        picked_targets = picked_expiry_targets(rows, now, member_ids)
        print(f"\n[archive 대상 ② 픽 시효] {len(picked_targets)}건 (picked {PICKED_MAX_DAYS}일+ · 보호 묶음 멤버 {len(member_ids)}건 면제)")
        for r, age in picked_targets[:30]:
            print(f"  {age}일 | {(r.get('title') or '')[:50]}")
        if len(picked_targets) > 30:
            print(f"  … 외 {len(picked_targets) - 30}건")

    if do_apply:
        all_targets = targets + picked_targets
        done = sum(1 for r, _ in all_targets if _archive(r["id"]) in (200, 204))
        cl_done = sum(1 for c, _ in cluster_targets if _delete_cluster(c["id"]))
        print(f"\narchived 전환 완료: {done}/{len(all_targets)} (pending {len(targets)} + 픽시효 {len(picked_targets)})"
              f" · 묶음 삭제 {cl_done}/{len(cluster_targets)}")
    else:
        print("\n(미리보기 — 실제 전환·삭제는 --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
