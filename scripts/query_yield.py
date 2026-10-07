#!/usr/bin/env python3
"""질의별 수확 보고 - 죽은 질의를 드러낸다.

왜 있나
  2026-10-08까지 적재 레코드에 요인 태그만 남고 **어떤 질의가 그 기사를
  데려왔는지** 기록하지 않았다. 그래서 「음악 IP 라이선스」가 몇 주째 쓸 것을
  0건 내는 상태와 조용한 뉴스 주간이 구별되지 않았다. 그 질의는 51건을 긁으면서
  10/07 카카오엔터x아틀란틱 제휴를 0건 잡았고, 그걸 알아낸 경로는 대표가 아는
  사건이 빠진 걸 눈으로 본 것뿐이었다.

  질의 집합은 조용히 썩는다. 유행어를 질의로 쓰면 그 말을 제목에 박은 무관한
  기사를 긁어 수확 숫자는 멀쩡해 보이고, 정작 사건은 당사자명으로 보도되니 빠진다.
  **수확량이 아니라 깔때기를 봐야 그게 보인다.**

무엇을 세나 - 질의별 깔때기 4단
  수확     이 질의로 적재된 건수
  통과     엔터 게이트를 지나 풀에 선 것(status != filtered_out)
  좌표     시제 판정이 요인·단계를 붙인 것 = 「엔터 산업의 변화」로 인정된 것
  타깃     대표가 팩트시트로 넘긴 타깃의 근거가 된 것

  **좌표가 0이면 그 질의는 산업 신호를 못 가져온다.** 수확이 100건이어도
  좌표가 0이면 유행어를 긁고 있다는 뜻이다(오대산 문화축전이 「AI 음악」에
  걸린 그 양상).

읽는 법
  수확 대비 좌표 비율이 그 질의의 적중률이다. 타깃은 표본이 작아 0이 정상이고
  (전체 타깃이 수십 건이다) 참고로만 본다. **판단은 좌표 열로 한다.**

쓰는 법
  python scripts/query_yield.py                # 최근 30일
  python scripts/query_yield.py --days 90
  python scripts/query_yield.py --days 60 --min-harvest 20

  `q:` 태그는 2026-10-08부터 붙는다. 그 전 적재분은 「(태그 없음)」으로 묶인다 -
  날짜 범위를 그 전으로 넓히면 대부분 거기 들어간다.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import io
import os
import sys

import requests

if (getattr(sys.stdout, "encoding", "") or "").lower() != "utf-8":
    # 재감싸기 방지 - 이미 감싼 뒤 또 감싸면 앞 래퍼가 닫혀 ValueError가 난다
    # (radar_audit이 이 모듈과 actor_watch를 같이 import하면서 터졌다, 2026-10-08).
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                  line_buffering=True)

NO_TAG = "(태그 없음)"


def _hdr() -> dict:
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")
    return {"apikey": key, "Authorization": "Bearer " + key}


def _fetch_paged(url: str, params: dict) -> list[dict]:
    # PostgREST는 limit과 무관하게 서버 max-rows(기본 1,000)로 응답을 자른다.
    # Range 헤더로 전 행을 순회한다 (pool_maintenance.py와 같은 패턴).
    out: list[dict] = []
    page = 1000
    lo = 0
    while True:
        r = requests.get(url, headers={**_hdr(), "Range": f"{lo}-{lo + page - 1}"},
                         params={"order": "id.asc", **params}, timeout=40)
        r.raise_for_status()
        rows = r.json()
        out.extend(rows)
        if len(rows) < page:
            return out
        lo += page


def aim_source_ids() -> set:
    """살아있는·끝난 타깃 전부의 근거 신호 id. 질의가 타깃까지 갔는지 보는 자리라
    status로 걸러내지 않는다 - drafting으로 끝난 것도 그 질의의 성과다."""
    try:
        url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/aims"
        rows = _fetch_paged(url, {"select": "id,source_ids"})
    except Exception as e:
        print("  (aims 조회 생략: %s)" % str(e)[:70])
        return set()
    out = set()
    for a in rows:
        for sid in (a.get("source_ids") or []):
            out.add(sid)
    return out


def qname(row: dict) -> str:
    for t in (row.get("tags") or []):
        if isinstance(t, str) and t.startswith("q:"):
            return t[2:]
    return NO_TAG


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-harvest", type=int, default=0,
                    help="수확이 이보다 적은 질의는 숨긴다(표본 부족)")
    args = ap.parse_args()

    if not (os.environ.get("SUPABASE_URL") and
            (os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY"))):
        print("SUPABASE_URL / SUPABASE_KEY 없음")
        return 1

    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=args.days)).date().isoformat()
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"
    rows = _fetch_paged(url, {
        "select": "id,tags,status,factor,stage,created_at",
        "collector": "eq.gnews", "created_at": "gte." + since})

    if not rows:
        print("최근 %d일 gnews 적재 0건" % args.days)
        return 0

    aims = aim_source_ids()
    stat: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        c = stat[qname(r)]
        c["수확"] += 1
        if r.get("status") != "filtered_out":
            c["통과"] += 1
        if r.get("factor") and r.get("stage"):
            c["좌표"] += 1
        if r.get("id") in aims:
            c["타깃"] += 1

    print("gnews 질의별 수확 - 최근 %d일 (%s 이후) · 적재 %d건\n" % (args.days, since, len(rows)))
    print("%-16s %6s %6s %6s %6s  %7s" % ("질의", "수확", "통과", "좌표", "타깃", "좌표율"))
    print("-" * 56)
    order = sorted(stat.items(), key=lambda kv: -kv[1]["수확"])
    dead = []
    for name, c in order:
        if c["수확"] < args.min_harvest:
            continue
        rate = (c["좌표"] / c["수확"] * 100) if c["수확"] else 0
        mark = ""
        # 좌표 0은 「산업 신호를 못 가져온다」는 뜻이다. 표본이 쌓인 질의만 지목한다.
        if c["수확"] >= 20 and c["좌표"] == 0:
            mark, _ = " <- 죽었다", dead.append(name)
        elif c["수확"] >= 30 and rate < 5:
            mark, _ = " <- 묽다", dead.append(name)
        print("%-16s %6d %6d %6d %6d  %6.1f%%%s"
              % (name[:16], c["수확"], c["통과"], c["좌표"], c["타깃"], rate, mark))
    print("-" * 56)
    tot = collections.Counter()
    for _, c in stat.items():
        tot.update(c)
    rate = (tot["좌표"] / tot["수확"] * 100) if tot["수확"] else 0
    print("%-16s %6d %6d %6d %6d  %6.1f%%" % ("합계", tot["수확"], tot["통과"],
                                              tot["좌표"], tot["타깃"], rate))

    if NO_TAG in stat:
        print("\n「%s」 %d건 = 2026-10-08 이전 적재분. 질의별로 갈 수 없다."
              % (NO_TAG, stat[NO_TAG]["수확"]))
    if dead:
        print("\n손볼 질의 %d개 - %s" % (len(dead), " · ".join(dead)))
        print("  바꿀 때는 기사가 쓰는 말로 고른다. 우리 분류 용어는 그 말을 쓴 기사가 없다.")
        print("  정본 = nvl-vibe-radar/google-news-queries.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
