#!/usr/bin/env python3
"""행위자 사각지대 감시 - 수집하지 않고 「못 본 것」만 보고한다.

왜 수집기가 아닌가
  사건은 당사자명으로 보도된다. 그래서 회사명을 질의로 넣으면 사건이 잡힌다.
  그런데 큰 기획사의 회사명 질의는 **터진다** - 2026-10-08 실측에서 룩백 3일
  신규가 티빙 86 · JYP 85 · YG 82 · SM 66 · 하이브 57건이었고 9개를 넣으면
  한국 수집이 63 -> 516건(+719%)이 된다. 피드가 컴백·직캠·특징주로 가득하기
  때문이다. 대표 불만이 「수집이 너무 많다」인데 정반대 방향이라 안 넣었다.

  대신 **보는 것만 한다.** 구글 뉴스에 있는데 우리 풀에 한 번도 들어온 적이
  없는 건을 골라 제목만 보여준다. RSS는 공짜이고 LLM을 안 쓴다. 적재도
  하지 않으므로 풀 상한·게이트 비용에 영향이 없다.

  신뢰 문제의 본질은 빠진 것 자체가 아니라 **빠진 걸 모르는 상태**다
  (「구글 뉴스에선 보이는데 여기선 없으니까」). 이건 그걸 한 줄로 만든다.

무엇을 거르나
  행위자 피드 전체를 보여주면 80건이 쏟아져 쓸모가 없다. 그래서 제목에
  **사건어**가 있는 것만 추린다(제휴·인수·지분·정산·국감 등). 정밀 우선이라
  놓치는 게 있고, **이 목록으로 비율을 계산하지 않는다** - 사람이 눈으로
  볼 후보를 좁히는 용도다.

  대조는 `radar_items` 전체(archived 포함)로 한다. 질문이 「지금 풀에 있나」가
  아니라 「한 번이라도 들어왔나」이기 때문이다.

쓰는 법
  python scripts/actor_watch.py                 # 최근 3일
  python scripts/actor_watch.py --days 7
  python scripts/actor_watch.py --all           # 사건어 필터 없이 전부

  반복해서 같은 행위자가 걸리면 그 이름을 gnews QUERIES에 넣을 근거가 된다.
  그때 한계 수확을 먼저 재고(앞 질의가 URL을 선점하므로 목록 끝에서) 넣는다.
"""
from __future__ import annotations

import argparse
import datetime
import email.utils as eu
import io
import os
import re
import sys
import urllib.parse

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                              line_buffering=True)

UA = {"User-Agent": "Mozilla/5.0 (compatible; NVLVibeRadar/1.0)"}

# gnews QUERIES에 **넣지 않은** 행위자들. 넣은 쪽(카카오엔터·CJ ENM·네이버웹툰)은
# 거기서 잡히니 여기 둘 필요가 없다. 「멜론」은 넣지 않는다 - 고창멜론연합회
# 농업마이스터가 섞인다(동음이의어, 2026-10-08 실측).
ACTORS = [
    "하이브", "SM엔터테인먼트", "YG엔터테인먼트", "JYP엔터테인먼트",
    "티빙", "스튜디오드래곤", "지니뮤직", "빅플래닛메이드",
]

# 사건어. 활동(컴백·직캠)·주가(특징주·목표주가)는 여기 없으므로 자연히 빠진다.
DEAL = ("제휴", "파트너십", "맞손", "체결", "인수", "합병", "지분", "투자", "유치",
        "펀드", "매각", "실적", "영업이익", "흑자", "적자", "정산", "소송", "분쟁",
        "국감", "수수료", "계약", "선임", "대표이사", "진출", "철수", "종료",
        "구독", "이용자", "회원", "저작권", "플랫폼", "독점")


def tokens(title: str) -> set:
    """제목에서 매체명·기호를 떼고 낱말 집합을 만든다."""
    t = re.sub(r"\s*-\s*[^-]+$", "", title)              # 끝의 매체명
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)          # [단독] (종합2보)
    t = re.sub(r"['\"“”‘’…·,\.!?⋯]", " ", t)
    t = re.sub(r"[美韓日中社]", "", t)   # 美 韓 日 中 社
    return {w for w in t.split() if len(w) > 1}


def fold_events(titles: list[str], thr: float = 0.34) -> list[list[str]]:
    """같은 사건을 한 묶음으로. 낱말 집합의 겹침(자카드)이 기준 이상이면 같은 사건.

    왜 자카드인가 - 처음에는 「정렬한 낱말 앞 4개」를 키로 썼는데 매체마다 조사·
    수식어가 달라 43건이 40묶음으로밖에 안 접혔다(2026-10-08 실측). 같은 국감
    기사가 서로 다른 사건으로 세어지면 사각지대 크기를 10배로 읽는다.

    기준 0.34는 느슨하다. 사각지대 **크기**를 가늠하는 자리라 과소 추정보다
    과대 추정이 위험하기 때문이다 - 서로 다른 사건이 한 묶음에 섞이면 「못 본
    사건 2개」로 작게 보이고 그러면 보고가 아무 일도 안 한다. 대표가 제목을
    눈으로 보는 전제이고, **이 수치로 비율을 계산하지 않는다.**
    """
    groups: list[tuple[set, list[str]]] = []
    for t in titles:
        ws = tokens(t)
        if not ws:
            groups.append((ws, [t]))
            continue
        for gw, gt in groups:
            inter = len(ws & gw)
            if inter and inter / len(ws | gw) >= thr:
                gt.append(t)
                gw |= ws
                break
        else:
            groups.append((ws, [t]))
    return [gt for _, gt in groups]


def _hdr() -> dict:
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY", "")
    return {"apikey": key, "Authorization": "Bearer " + key}


def known_urls(days: int) -> set:
    """우리가 한 번이라도 들인 gnews URL. archived·filtered_out도 포함한다 -
    질문이 「지금 보이나」가 아니라 「한 번이라도 들어왔나」다."""
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days + 4)).date().isoformat()
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"
    out, lo, page = set(), 0, 1000
    while True:
        r = requests.get(url, headers={**_hdr(), "Range": f"{lo}-{lo + page - 1}"},
                         params={"select": "url", "collector": "eq.gnews",
                                 "created_at": "gte." + since, "order": "id.asc"},
                         timeout=40)
        r.raise_for_status()
        rows = r.json()
        for x in rows:
            if x.get("url"):
                out.add(x["url"])
        if len(rows) < page:
            return out
        lo += page


def fetch(q: str) -> list[tuple[str, str, datetime.datetime | None]]:
    u = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
         + "&hl=ko&gl=KR&ceid=KR:ko")
    r = requests.get(u, headers=UA, timeout=25)
    r.raise_for_status()
    out = []
    for it in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        lk = re.search(r"<link>(.*?)</link>", it, re.S)
        ti = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
        pd = re.search(r"<pubDate>(.*?)</pubDate>", it)
        if not lk:
            continue
        try:
            dt = eu.parsedate_to_datetime(pd.group(1)) if pd else None
        except Exception:
            dt = None
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        out.append((lk.group(1).strip(), (ti.group(1).strip() if ti else ""), dt))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--all", action="store_true", help="사건어 필터 없이 전부 보인다")
    ap.add_argument("--show", type=int, default=5, help="행위자당 보일 제목 수")
    args = ap.parse_args()

    if not (os.environ.get("SUPABASE_URL") and
            (os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY"))):
        print("SUPABASE_URL / SUPABASE_KEY 없음")
        return 1

    seen = known_urls(args.days)
    cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.days)
    print("행위자 사각지대 - 최근 %d일 · 대조 대상 gnews URL %d건 (수집하지 않는다)\n"
          % (args.days, len(seen)))

    gaps = []
    for a in ACTORS:
        try:
            rows = fetch(a)
        except Exception as e:
            print("  %-16s 조회 실패 %s" % (a, str(e)[:40]))
            continue
        win = [(u, t) for u, t, d in rows if d and d >= cut]
        miss = [(u, t) for u, t in win if u not in seen]
        if not args.all:
            miss = [(u, t) for u, t in miss if any(k in t for k in DEAL)]
        # 사건으로 접는다 - 국감 하나가 43개 매체로 보도되면 「43건」이 아니라 1사건이다
        folded = fold_events([t for _, t in miss])
        print("  %-16s 룩백내 %3d · 못 본 기사 %3d · 못 본 사건 %3d"
              % (a, len(win), len(miss), len(folded)))
        for ts in folded[:args.show]:
            dup = (" (+%d개 매체)" % (len(ts) - 1)) if len(ts) > 1 else ""
            print("        %s%s" % (ts[0][:84], dup))
        if len(folded) > args.show:
            print("        외 %d사건" % (len(folded) - args.show))
        if folded:
            gaps.append((a, len(folded)))

    print()
    if not gaps:
        print("사각지대 없음.")
        return 0
    gaps.sort(key=lambda x: -x[1])
    print("사각지대 %d행위자 (못 본 사건 수) - %s"
          % (len(gaps), " · ".join("%s %d" % g for g in gaps)))
    print("  같은 행위자가 반복해서 걸리면 gnews QUERIES에 이름을 넣을 근거다.")
    print("  넣기 전에 한계 수확을 먼저 잰다 - 큰 기획사는 룩백 3일에 57~86건이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
