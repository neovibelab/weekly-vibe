#!/usr/bin/env python3
"""일회성 — 뉴스룸 allowlist에서 제외한 미디어 소스(야후 엔타메·36氪·钛媒体)의 기존 적재분을
DB·풀에는 남기되 collector를 'newsroom'→'feed'로 바꿔 '뉴스룸' 분류(collector=newsroom)에서만 뺀다.
status는 안 건드리므로 풀(지역·토픽)에 그대로 남고, 대시보드 '📰 뉴스룸' 칩·배지에서만 빠진다.
sources_newsrooms.json에서 이미 제거됨 → 신규 유입 없음. 이건 레거시 정리용(2026-06-23).

환경변수: SUPABASE_URL, SUPABASE_KEY
사용: python scripts/recollector_legacy_newsroom.py [--dry-run]
"""
from __future__ import annotations

import io
import os
import sys

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 뉴스룸에서 뺀 미디어/어그리게이터 소스 (sources_newsrooms.json에서 제거된 것과 동일)
SOURCES = ["36氪", "钛媒体", "Yahoo!ニュース エンタメ"]
NEW_COLLECTOR = "feed"


def _base() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        print("SUPABASE_URL/SUPABASE_KEY 미설정")
        raise SystemExit(1)
    return url, key


def main() -> int:
    dry = "--dry-run" in sys.argv
    url, key = _base()
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    total = 0
    for src in SOURCES:
        r = requests.get(f"{url}/rest/v1/radar_items", headers=h, timeout=20, params={
            "select": "id,title", "collector": "eq.newsroom", "source": f"eq.{src}",
        })
        r.raise_for_status()
        rows = r.json()
        print(f"[{src}] collector=newsroom {len(rows)}건")
        if dry:
            total += len(rows)
            continue
        for it in rows:
            pr = requests.patch(
                f"{url}/rest/v1/radar_items?id=eq.{it['id']}",
                headers={**h, "Content-Type": "application/json", "Prefer": "return=minimal"},
                json={"collector": NEW_COLLECTOR, "category": NEW_COLLECTOR}, timeout=20,
            )
            if pr.status_code in (200, 204):
                total += 1
            else:
                print(f"  실패 HTTP {pr.status_code}: {(it.get('title') or '')[:40]}")
    print(f"{'(dry-run) ' if dry else ''}collector→{NEW_COLLECTOR} 변경: {total}건 (풀·status 유지, 뉴스룸 분류만 해제)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
