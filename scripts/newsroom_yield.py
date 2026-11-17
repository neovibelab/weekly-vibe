#!/usr/bin/env python3
"""뉴스룸 수율 - 소스별 되돌림을 접고 그 자리를 받는다.

왜 있나
  2026-11-18까지 뉴스룸 등재의 되돌림이 **소스별 두 줄**이었고 임계가
  「4주간 풀에 한 건도 안 남으면 뺀다」였다. 등재 사유는 **밀도**(밀도 낮은
  피드는 안 붙인다)였는데 **임계 1건은 그 의도를 못 받는다** - RIAA는 전수
  1건, Deezer는 마지막 적재가 10-28인데도 조건상 유지였다. 같은 창에서
  테크M이 216건이었다. 2026-11-16 만기 판정이 그 결함을 드러냈고
  **대표가 「조건을 접는다」로 정했다**(소스별을 없애고 뉴스룸 전체 수율만 본다).

  소스 하나하나를 기계가 처분하지 않는다. **묻는 것은 뉴스룸 묶음 전체가
  밀도를 내고 있나**이고, 아니면 그때 밀도 낮은 피드를 사람이 골라 접는다.

무엇을 세나
  적재     `collector='newsroom'`으로 들어온 건수
  통과     엔터 게이트를 지나 풀에 선 것(`status != 'filtered_out'`)
  수율     통과 / 적재. **이것이 되돌림 조건의 분모다.**

  소스별 줄도 같이 찍지만 **판정은 합계 수율로 한다** - 소스 하나의 0건은
  그 피드가 조용한 주였다는 뜻이고, 합계가 반 토막이면 묶음이 썩은 것이다.

내가 안 보는 축
  **좌표·타깃까지는 안 본다.** 게이트 통과가 곧 쓸 수 있는 신호라는 뜻은
  아니다(시제 판정이 요인·단계를 붙이는 층이 그 뒤에 있다). 그 축을 보는
  자리는 `query_yield.py`이고 **거기는 gnews 전용이라 뉴스룸을 안 본다** -
  뉴스룸의 좌표율을 보는 자리는 **없다.**

쓰는 법
  cd weekly-vibe && python scripts/newsroom_yield.py          # 최근 7일
  python scripts/newsroom_yield.py --days 28
  python scripts/newsroom_yield.py --days 7 --quiet            # 합계만
"""
from __future__ import annotations

import argparse
import collections
import datetime
import io
import os
import sys
from pathlib import Path

import requests


def _load_local_env() -> None:
    """로컬 실행 시 인접 .env에서 키 자동 로드 (GitHub Actions에선 no-op)."""
    root = Path(__file__).resolve().parents[2]  # claude-NeoVibeLab/
    p = root / "nvl-vibe-radar" / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_local_env()

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                  line_buffering=True)

BASELINE = 34.8  # 2026-11-18 실측 (최근 7일 · 적재 158 · 통과 55)


def fetch(days: int) -> list[dict]:
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")
    hdr = {"apikey": key, "Authorization": "Bearer " + key}
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)).isoformat()
    rows: list[dict] = []
    off = 0
    while True:
        r = requests.get(url, headers=hdr, params={
            "select": "source,status,created_at", "collector": "eq.newsroom",
            "created_at": "gte." + since, "limit": 1000, "offset": off}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        rows += batch
        if len(batch) < 1000:
            return rows
        off += 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--quiet", action="store_true", help="합계만 찍는다")
    a = ap.parse_args()

    if not (os.environ.get("SUPABASE_URL")
            and (os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY"))):
        print("SUPABASE_URL / SUPABASE_KEY 없음 - nvl-vibe-radar/.env를 본다")
        return 1

    rows = fetch(a.days)
    load: collections.Counter = collections.Counter()
    passed: collections.Counter = collections.Counter()
    for x in rows:
        s = x.get("source") or "(없음)"
        load[s] += 1
        if x.get("status") != "filtered_out":
            passed[s] += 1

    tot, ok = sum(load.values()), sum(passed.values())
    rate = (ok / tot * 100) if tot else 0.0
    print(f"뉴스룸 최근 {a.days}일 - 적재 {tot} · 통과 {ok} · 수율 {rate:.1f}% "
          f"(기준선 {BASELINE}% · 절반 {BASELINE / 2:.1f}%)")
    if tot and rate < BASELINE / 2:
        print("  되돌림 발동 - 밀도 낮은 피드를 골라 접는다 (사람이 고른다)")
    elif not tot:
        print("  적재 0건 - 수집이 멎은 것과 조용한 주가 안 갈린다. 잡 로그를 먼저 본다")
    if not a.quiet:
        for s, n in load.most_common():
            print(f"   {s:30s} 적재 {n:4d} · 통과 {passed[s]:4d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
