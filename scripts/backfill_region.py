#!/usr/bin/env python3
"""백필 — 소스 고정 힌트로 region이 매겨진 기존 항목(collector in newsletter·newsroom·feed)의
region을 기사 내용 기준으로 재분류한다. 발신 매체 국적 ≠ 기사 내용 지역 문제 해결
(예: 한국 뉴스레터 Longblack의 글로벌·일본 기사가 전부 'korea'로 찍히던 것, 2026-06-23).

vibe_search는 지역별 검색이라 이미 내용 기준 — 대상 아님. region이 바뀌는 항목만 PATCH.
status·title·summary·topics 등 다른 필드는 안 건드림.

견고화: 단발 분류오류는 건너뛰고, 연속 3회 실패(크레딧 소진·키 누락)면 중단.

환경변수: SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY
사용:
  python scripts/backfill_region.py --scan-only          # API 호출 없이 대상 집계
  python scripts/backfill_region.py --dry-run [--limit N] # 재분류만, 쓰기 없음
  python scripts/backfill_region.py [--limit N]           # 실제 갱신
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

COLLECTORS = ["newsletter", "newsroom", "feed"]  # 소스 힌트 기반 — vibe_search·manual 제외
VALID = {"korea", "global-en", "china", "japan", "southeast-asia"}
MODEL = "claude-haiku-4-5-20251001"


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
    out, step, off = [], 1000, 0
    while True:
        r = requests.get(f"{url}/rest/v1/radar_items", headers=h, timeout=20, params={
            "select": "id,title,summary,region,collector", "collector": f"eq.{collector}",
            "order": "created_at.desc", "limit": step, "offset": off,
        })
        r.raise_for_status()
        batch = r.json()
        out += batch
        if len(batch) < step:
            break
        off += step
    return out


def classify_region(title: str, summary: str) -> tuple[str | None, bool]:
    """제목·요약 → 내용 기준 지역 1개. 반환 (region|None, failed)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None, True
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        prompt = (
            "다음 기사가 주로 다루는 시장·지역을 기사 내용 기준으로 하나만 골라 JSON으로만 응답.\n"
            "값: korea / china / japan / southeast-asia / global-en.\n"
            "발신 매체나 기업의 국적이 아니라 기사 내용 기준 — 한국 매체의 일본 기업 기사는 japan, "
            "글로벌 브랜드는 global-en, 인니·태국·베트남·필리핀은 southeast-asia, "
            "특정 아시아국이 아니면 global-en.\n\n"
            f"제목: {title}\n요약: {summary[:600]}\n\n"
            '{"region": "..."}'
        )
        msg = client.messages.create(model=MODEL, max_tokens=60,
                                     messages=[{"role": "user", "content": prompt}])
        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        reg = (data.get("region") or "").strip()
        return (reg if reg in VALID else None), False
    except Exception as e:
        log.warning("region 분류 실패: %s", e)
        return None, True


def patch_region(item_id: str, region: str) -> int:
    url, key = _base()
    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json", "Prefer": "return=minimal"}
    r = requests.patch(f"{url}/rest/v1/radar_items?id=eq.{item_id}", headers=h,
                       json={"region": region}, timeout=20)
    return r.status_code


def main() -> int:
    ap = argparse.ArgumentParser(description="newsletter·newsroom·feed region 내용 기준 재분류")
    ap.add_argument("--dry-run", action="store_true", help="재분류만, Supabase 쓰기 없음")
    ap.add_argument("--scan-only", action="store_true", help="API 호출 없이 대상 집계만")
    ap.add_argument("--limit", type=int, default=0, help="처리 상한(0=무제한)")
    args = ap.parse_args()

    items: list[dict] = []
    for c in COLLECTORS:
        items += fetch_collector(c)
    log.info("재분류 후보(소스 힌트 기반) %d건 | 수집기별 %s", len(items),
             {c: sum(1 for i in items if i.get("collector") == c) for c in COLLECTORS})

    if args.scan_only:
        return 0
    if args.limit:
        items = items[:args.limit]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY 미설정")
        return 1

    changed = same = skipped = patch_fail = 0
    consec_fail = 0
    for i, it in enumerate(items, 1):
        title = it.get("title") or ""
        summary = it.get("summary") or ""
        new_reg, failed = classify_region(title, summary)
        if failed:
            consec_fail += 1
            skipped += 1
            log.warning("분류 실패 건너뜀 (%d/%d · 연속 %d): %s", i, len(items), consec_fail, title[:42])
            if consec_fail >= 3:
                log.error("연속 %d회 실패 — 크레딧 소진/키 문제로 보고 중단. 변경 %d · 동일 %d",
                          consec_fail, changed, same)
                return 2
            continue
        consec_fail = 0
        cur = it.get("region")
        if not new_reg or new_reg == cur:
            same += 1
            continue
        if args.dry_run:
            log.info("[dry %d/%d] %s: %s → %s", i, len(items), title[:40], cur, new_reg)
            changed += 1
            continue
        code = patch_region(it["id"], new_reg)
        if code in (200, 204):
            changed += 1
            log.info("변경 %d/%d  %s → %s | %s", i, len(items), cur, new_reg, title[:42])
        else:
            patch_fail += 1
            log.warning("갱신 실패 HTTP %d: %s", code, title[:42])

    log.info("region 백필 완료 — 변경 %d · 동일 %d · 분류건너뜀 %d · 적재실패 %d%s",
             changed, same, skipped, patch_fail, " (dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
