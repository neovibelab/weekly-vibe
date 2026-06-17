#!/usr/bin/env python3
"""일회성: 과거 적재된 Netflix(newsroom) pending 글 중 홍보(promo)를 status=filtered_out로 정리.

newsroom_ingest.classify()를 재사용해 promo 판별 → promo만 status=filtered_out + filter_verdict=promo
로 갱신(signal은 pending 보존). 되돌리려면 filtered_out→pending.

옵션:
  (기본)         dry-run — 조회·promo 판별만 출력, DB 변경 없음
  --apply        실제 갱신
  --all          promo 판별 없이 대상 pending 전부 promo 취급(넷플릭스는 대부분 홍보)
  --source NAME  대상 소스명(기본 Netflix)

env: SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests  # noqa: E402
from newsroom_ingest import classify  # noqa: E402  (promo 판별 로직 재사용 — 단일 정본)


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"


def _hdr(extra: dict | None = None) -> dict:
    key = os.environ["SUPABASE_KEY"]
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        h.update(extra)
    return h


def fetch_pending(source: str) -> list[dict]:
    r = requests.get(_base(), headers=_hdr(), params={
        "select": "id,title,url,summary,status",
        "collector": "eq.newsroom", "source": f"eq.{source}", "status": "eq.pending",
    }, timeout=20)
    r.raise_for_status()
    return r.json()


def mark_filtered(item_id: str) -> int:
    r = requests.patch(
        _base(),
        headers=_hdr({"Content-Type": "application/json", "Prefer": "return=minimal"}),
        params={"id": f"eq.{item_id}"},
        json={"status": "filtered_out", "filter_verdict": "promo"}, timeout=15,
    )
    return r.status_code


def main() -> int:
    apply = "--apply" in sys.argv
    all_mode = "--all" in sys.argv
    source = "Netflix"
    if "--source" in sys.argv:
        source = sys.argv[sys.argv.index("--source") + 1]

    rows = fetch_pending(source)
    mode = "ALL(판별없이 전부)" if all_mode else "promo판별"
    run = "APPLY" if apply else "DRY-RUN"
    print(f"[{source}] newsroom pending: {len(rows)}건  (mode: {mode}, {run})\n")

    targets = []
    for it in rows:
        if all_mode:
            promo = True
        else:
            promo = classify(it["title"], it.get("summary") or "", "global-en").get("is_promo", False)
        print(f"  [{'PROMO ' if promo else 'signal'}] {it['title'][:72]}")
        if promo:
            targets.append(it)

    print(f"\n→ filtered_out 대상: {len(targets)}/{len(rows)}")
    if not apply:
        print("(DRY-RUN — DB 변경 없음. 실제 적용은 --apply)")
        return 0

    done = 0
    for it in targets:
        code = mark_filtered(it["id"])
        if code in (200, 204):
            done += 1
        else:
            print(f"  ! 실패 HTTP {code}: {it['title'][:40]}")
    print(f"완료: {done}/{len(targets)} → status=filtered_out, filter_verdict=promo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
