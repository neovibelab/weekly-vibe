#!/usr/bin/env python3
"""백필 — 크레딧 아웃 등으로 미번역·미분류로 적재된 newsroom/newsletter radar_items를
재번역+재분류해 Supabase에 갱신한다. 크레딧 충전 후 1회성(필요 시 반복) 실행.

대상 식별 (둘 중 하나):
  - filter_verdict == 'classify_failed'  (견고화 이후 하드 실패로 표시된 항목)
  - collector in (newsroom, newsletter) AND 제목·요약에 한글이 전혀 없음
    (견고화 이전 누적 오염 — 분류 성공 항목은 summary_ko가 한국어라 항상 한글 포함)

유저 큐레이션 보존: status가 pending/filtered_out(미큐레이션)일 때만 자동 재설정.
picked/rejected/hold/newsletter/signal/approved/archived는 텍스트만 갱신하고 상태 유지.

vibe_search(웹수집)는 인라인 번역이라 대상 아님 — newsroom·newsletter만.
classify 로직은 두 수집기에서 그대로 재사용(중복 정의 금지).

환경변수: SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY
사용:
  python scripts/backfill_translate.py --scan-only            # API 호출 없이 대상만 집계
  python scripts/backfill_translate.py --dry-run [--limit N]  # 재분류만, 쓰기 없음
  python scripts/backfill_translate.py [--limit N]            # 실제 갱신
  python scripts/backfill_translate.py --env path/.env ...    # 로컬 .env 로드(선택)
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import re
import sys

import requests

# classify 재사용 (같은 scripts/ 디렉터리 — vibe_search가 supabase_writer를 import하는 것과 동일 패턴)
from newsroom_ingest import classify as classify_newsroom
from newsletter_ingest import classify as classify_newsletter

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HANGUL_RE = re.compile(r"[가-힣]")
# 상태 자동 갱신 대상 — 유저가 손대지 않은 것만. 나머지는 텍스트만 갱신하고 보존.
AUTO_STATUS = {"pending", "filtered_out"}


def _load_env(path: str) -> None:
    if not path or not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _base() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        log.error("SUPABASE_URL/SUPABASE_KEY 미설정")
        raise SystemExit(1)
    return url, key


def fetch_collector(collector: str) -> list[dict]:
    url, key = _base()
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    sel = "id,collector,region,title,summary,status,filter_verdict,topics,is_entertainment"
    out, step, off = [], 1000, 0
    while True:
        r = requests.get(f"{url}/rest/v1/radar_items", headers=h, timeout=20, params={
            "select": sel, "collector": f"eq.{collector}",
            "order": "created_at.desc", "limit": step, "offset": off,
        })
        r.raise_for_status()
        batch = r.json()
        out += batch
        if len(batch) < step:
            break
        off += step
    return out


def needs_backfill(it: dict) -> bool:
    if it.get("filter_verdict") == "classify_failed":
        return True
    hay = (it.get("title") or "") + " " + (it.get("summary") or "")
    return not HANGUL_RE.search(hay)


def recompute(collector: str, cls: dict, current_status: str) -> tuple[str, str]:
    """재분류 결과로 status·filter_verdict 산정. 유저 큐레이션 상태는 보존."""
    is_ent = cls.get("is_entertainment", True)
    is_gos = cls.get("is_gossip", False)
    is_promo = cls.get("is_promo", False)  # newsroom 전용 필드
    if collector == "newsroom":
        bad = is_promo or (not is_ent) or is_gos
        verdict = "promo" if is_promo else "non_ent" if not is_ent else "gossip" if is_gos else "pass"
    else:  # newsletter
        bad = (not is_ent) or is_gos
        verdict = "non_ent" if not is_ent else "gossip" if is_gos else "pass"
    new_status = ("filtered_out" if bad else "pending") if current_status in AUTO_STATUS else current_status
    return new_status, verdict


def patch_row(item_id: str, fields: dict) -> int:
    url, key = _base()
    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json", "Prefer": "return=minimal"}
    r = requests.patch(f"{url}/rest/v1/radar_items?id=eq.{item_id}", headers=h, json=fields, timeout=20)
    return r.status_code


def main() -> int:
    ap = argparse.ArgumentParser(description="newsroom/newsletter 미번역 항목 재번역 백필")
    ap.add_argument("--dry-run", action="store_true", help="재분류만, Supabase 쓰기 없음")
    ap.add_argument("--scan-only", action="store_true", help="API 호출 없이 대상 집계만")
    ap.add_argument("--limit", type=int, default=0, help="처리 상한(0=무제한)")
    ap.add_argument("--env", default="", help="로컬 .env 경로(선택, GitHub Actions는 불필요)")
    args = ap.parse_args()
    _load_env(args.env)

    items = fetch_collector("newsroom") + fetch_collector("newsletter")
    targets = [it for it in items if needs_backfill(it)]
    by_region: dict[str, int] = {}
    for it in targets:
        r = it.get("region") or "?"
        by_region[r] = by_region.get(r, 0) + 1
    log.info("newsroom+newsletter 총 %d건 중 백필 대상 %d건 | 지역별 %s",
             len(items), len(targets), by_region)

    if args.scan_only:
        for it in targets[:25]:
            log.info("  [%s/%s] %s | %s",
                     it.get("collector"), it.get("region"),
                     it.get("filter_verdict"), (it.get("title") or "")[:55])
        if len(targets) > 25:
            log.info("  ... 외 %d건", len(targets) - 25)
        return 0

    if args.limit:
        targets = targets[:args.limit]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY 미설정 — 재번역 불가")
        return 1

    updated = patch_fail = skipped = 0
    consec_fail = 0
    for i, it in enumerate(targets, 1):
        collector = it.get("collector")
        title0 = it.get("title") or ""
        summary0 = it.get("summary") or ""
        classify = classify_newsroom if collector == "newsroom" else classify_newsletter
        cls = classify(title0, summary0, it.get("region") or "global-en")
        if cls.get("_failed"):
            # 분류 호출 실패. 크레딧 소진·키 누락이면 전 항목이 연속 실패하므로 3회 연속 시에만 중단,
            # JSON 파싱 깨짐 같은 단발 오류면 그 항목만 건너뛰고 계속 — 한 건 때문에 전체가 멈추고
            # "크레딧 미충전"으로 오인되던 문제 수정(2026-06-23, 66/67 중단 사고).
            consec_fail += 1
            skipped += 1
            log.warning("분류 실패 건너뜀 (%d/%d · 연속 %d): %s", i, len(targets), consec_fail, title0[:48])
            if consec_fail >= 3:
                log.error("연속 %d회 분류 실패 — 크레딧 소진/키 문제로 보고 중단. 갱신 %d · 건너뜀 %d",
                          consec_fail, updated, skipped)
                return 2
            continue
        consec_fail = 0
        new_status, verdict = recompute(collector, cls, it.get("status") or "pending")
        fields = {
            "title": (cls.get("title_ko") or title0)[:500],
            "summary": (cls.get("summary_ko") or summary0)[:1000],
            "topics": cls.get("topics", []),
            "tags": cls.get("topics", []),
            "is_entertainment": cls.get("is_entertainment", True),
            "status": new_status,
            "filter_verdict": verdict,
        }
        if args.dry_run:
            log.info("[dry %d/%d] %s → %s | %s · %s",
                     i, len(targets), title0[:34], fields["title"][:34], verdict, new_status)
            updated += 1
            continue
        code = patch_row(it["id"], fields)
        if code in (200, 204):
            updated += 1
            log.info("갱신 %d/%d [%s·%s] %s", i, len(targets), verdict, new_status, fields["title"][:48])
        else:
            patch_fail += 1
            log.warning("갱신 실패 HTTP %d: %s", code, title0[:48])

    log.info("백필 완료 — 갱신 %d · 분류건너뜀 %d · 적재실패 %d%s",
             updated, skipped, patch_fail, " (dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
