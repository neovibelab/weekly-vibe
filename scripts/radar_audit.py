#!/usr/bin/env python3
"""레이더 검수 - 다섯 숫자를 로그 한 줄로 쌓는다.

왜 만들었나 (2026-10-08)
------------------------
같은 날 측정기 둘을 만들었다(`query_yield.py`·`actor_watch.py`). 그런데 이
저장소에는 **측정기가 있는데도 학습이 멈춘 선례**가 있다. `diff-harvest.py`의
머리말이 그 기록이다 - 측정기(`convergence-check.py`)는 있었고 로그 기입이
사람 손이어서 perspective-diff-log는 08-08에 멈췄고 나머지 세 로그는 0항목인
채 3편이 발행됐다. 그 사이에 규칙만 40번 늘었다. **빠진 것은 측정이 아니라
적재였다.**

그래서 여기서도 측정을 새로 만들지 않는다. **있는 측정기를 불러 쓰고
write-back만 한다.** 중복 구현하지 않는다(diff-harvest와 같은 방침).

무엇을 재나 - 다섯 숫자
-----------------------
  질의        gnews QUERIES 수. 늘었나 줄었나
  수집/일     gnews 일당 적재. **올리면 안 되는 쪽**이다
  좌표율      시제 판정이 요인·단계를 붙인 비율 = 「엔터 산업의 변화」 인정률
  지목        query_yield이 「죽었다·묽다」로 지목한 질의 수
  못본사건    actor_watch의 사각지대. **내리는 쪽**이다
  발행일치    발행본이 인용한 URL 중 레이더가 갖고 있던 것

**쌍으로 읽는다.** 못본사건 하나만 보면 틀린다 - 질의를 늘리면 사각지대는
줄고 수집량이 같이 오른다(2026-10-08 실측: 행위자 9개 전부 넣으면 사각지대는
거의 0이 되지만 한국 수집이 +719%다). **못본사건이 내려가면서 수집/일이 안
오르는 것**이 개선이다.

좌표율은 **내부 신호라 순환한다.** 우리 판정자(haiku)를 기쁘게 하도록 질의가
수렴할 수 있다. 그 순환을 끊는 외부 기준이 **발행일치**다. 좌표율로 양을 보고
발행일치로 타당성을 본다. 둘이 반대로 가면 판정자를 의심한다.

쓰는 법
-------
  python scripts/radar_audit.py              # 재고 로그에 한 줄 쓴다
  python scripts/radar_audit.py --days 14
  python scripts/radar_audit.py --no-write    # 재기만 한다

같은 날 두 번 돌리면 그 날 행을 덮어쓴다(중복 행을 쌓지 않는다).
로그 = `radar-audit-log.md`. 추세가 쌓이기 전에는 숫자 하나가 기준선일 뿐이다.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # weekly-vibe/
NVL = os.path.dirname(ROOT)                        # claude-NeoVibeLab/
sys.path.insert(0, HERE)

if (getattr(sys.stdout, "encoding", "") or "").lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                  line_buffering=True)

import query_yield as qy          # noqa: E402  수확 깔때기
import actor_watch as aw          # noqa: E402  사각지대

LOG = os.path.join(ROOT, "radar-audit-log.md")
HEAD = ("| 날짜 | 창 | 질의 | 수집/일 | 좌표율 | 지목 | 못본사건 | 발행일치 | 메모 |\n"
        "|---|---|---|---|---|---|---|---|---|\n")


def query_count() -> int:
    """gnews QUERIES 수. 모듈을 import하면 로깅 설정이 따라오므로 소스를 읽는다."""
    src = io.open(os.path.join(HERE, "gnews_ingest.py"), encoding="utf-8").read()
    m = re.search(r"QUERIES = \[(.*?)\n\]", src, re.S)
    if not m:
        return -1
    return sum(1 for l in m.group(1).splitlines() if l.strip().startswith("("))


def funnel(days: int) -> tuple[int, float, int, list[str]]:
    """gnews 적재 수 · 좌표율 · 지목 질의 수. query_yield의 판정 기준을 그대로 쓴다."""
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)).date().isoformat()
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"
    rows = qy._fetch_paged(url, {"select": "id,tags,status,factor,stage",
                                 "collector": "eq.gnews", "created_at": "gte." + since})
    if not rows:
        return 0, 0.0, 0, []
    coord = sum(1 for r in rows if r.get("factor") and r.get("stage"))
    per: dict[str, list[int]] = {}
    for r in rows:
        c = per.setdefault(qy.qname(r), [0, 0])
        c[0] += 1
        if r.get("factor") and r.get("stage"):
            c[1] += 1
    # query_yield과 같은 기준 - 수확 20+ 좌표 0은 「죽었다」, 30+ 5% 미만은 「묽다」
    dead = []
    for name, (h, cd) in per.items():
        if name == qy.NO_TAG:
            continue
        if (h >= 20 and cd == 0) or (h >= 30 and cd / h < 0.05):
            dead.append(name)
    return len(rows), coord / len(rows) * 100, len(dead), sorted(dead)


def blind(days: int) -> tuple[int, list[tuple[str, int]]]:
    """사각지대 - 못 본 **사건** 수. actor_watch의 접기를 그대로 쓴다."""
    seen = aw.known_urls(days)
    cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    total, per = 0, []
    for a in aw.ACTORS:
        try:
            rows = aw.fetch(a)
        except Exception:
            continue
        miss = [t for u, t, d in rows
                if d and d >= cut and u not in seen and any(k in t for k in aw.DEAL)]
        n = len(aw.fold_events(miss))
        total += n
        if n:
            per.append((a, n))
    per.sort(key=lambda x: -x[1])
    return total, per


def cited() -> tuple[int, int]:
    """발행본이 인용한 URL 중 레이더가 갖고 있던 것.

    **외부 기준이다.** 좌표율은 우리 판정자의 의견이고 이건 대표가 실제로 쓴 것이다.
    URL 완전 일치만 센다 - 호스트 일치는 느슨해서(같은 매체의 다른 기사) 성과로
    못 읽는다. 라이브 풀의 41%가 구글 뉴스 리다이렉트라 상한이 낮다는 것을 알고 본다.
    """
    pub = sorted(f for f in glob.glob(os.path.join(NVL, "ecri-newsletter", "md-archive", "*.md"))
                 if re.match(r"2026(0[5-9]|1[0-2])", os.path.basename(f)))
    if not pub:
        return 0, 0
    urls = []
    for f in pub:
        t = io.open(f, encoding="utf-8", errors="replace").read()
        urls += ["https://" + m.group(1) for m in re.finditer(r"https?://([^\s\)\]\"'<>]+)", t)]
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"
    rows = qy._fetch_paged(url, {"select": "url"})
    have = {x["url"] for x in rows if x.get("url")}
    return sum(1 for u in urls if u in have), len(urls)


def write_row(row: str) -> None:
    """같은 날 행은 덮어쓴다. 하루 두 번 돌려 중복 행을 쌓지 않는다."""
    today = row.split("|")[1].strip()
    if os.path.exists(LOG):
        txt = io.open(LOG, encoding="utf-8").read()
    else:
        txt = ("# 레이더 검수 로그\n\n"
               "`python scripts/radar_audit.py`가 쌓는다. **손으로 고치지 않는다.**\n\n"
               "읽는 법 - **쌍으로 본다.** 「못본사건」이 내려가면서 「수집/일」이 안 오르는 것이\n"
               "개선이다. 질의를 늘리면 둘이 같이 움직인다. 「좌표율」은 우리 판정자의 의견이라\n"
               "순환하고, 그 순환을 끊는 외부 기준이 「발행일치」다 - 둘이 반대로 가면 판정자를\n"
               "의심한다. 「지목」은 query_yield이 죽었다·묽다로 지목한 질의 수다.\n\n"
               + HEAD)
    if HEAD not in txt:
        txt += "\n" + HEAD
    lines = txt.splitlines(True)
    out, replaced = [], False
    for l in lines:
        if l.startswith("| " + today + " |"):
            out.append(row)
            replaced = True
        else:
            out.append(l)
    if not replaced:
        out.append(row)
    io.open(LOG, "w", encoding="utf-8", newline="").write("".join(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--memo", default="", help="로그 행 끝에 남길 한 줄")
    args = ap.parse_args()

    if not (os.environ.get("SUPABASE_URL") and
            (os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY"))):
        print("SUPABASE_URL / SUPABASE_KEY 없음")
        return 1

    nq = query_count()
    n, rate, ndead, dead = funnel(args.days)
    nblind, per = blind(min(args.days, 7))
    hit, tot = cited()

    print("=" * 66)
    print("레이더 검수 | 최근 %d일" % args.days)
    print("=" * 66)
    print("  질의        %d개" % nq)
    print("  수집/일     %.0f건  (gnews %d건 / %d일)   <- 올리면 안 되는 쪽" % (n / args.days, n, args.days))
    print("  좌표율      %.1f%%  <- 내부 신호(우리 판정자)" % rate)
    print("  지목        %d개 질의%s" % (ndead, ("  " + " · ".join(dead)) if dead else ""))
    print("  못본사건    %d개  (%d일 창)   <- 내리는 쪽" % (nblind, min(args.days, 7)))
    if per:
        print("              " + " · ".join("%s %d" % p for p in per))
    print("  발행일치    %d / %d  (%.1f%%)  <- 외부 기준" % (hit, tot, hit / max(tot, 1) * 100))
    print("-" * 66)
    print("  쌍으로 읽는다 - 못본사건이 내려가면서 수집/일이 안 오르는 것이 개선이다.")
    print("  좌표율과 발행일치가 반대로 가면 판정자를 의심한다.")

    today = datetime.date.today().isoformat()
    row = ("| %s | %d일 | %d | %.0f | %.1f%% | %d | %d | %d/%d | %s |\n"
           % (today, args.days, nq, n / args.days, rate, ndead, nblind, hit, tot, args.memo))
    if args.no_write:
        print("\n--no-write - 쓰지 않았다. 쓸 행:\n  " + row.strip())
        return 0
    write_row(row)
    print("\n%s 에 한 줄 적었다." % os.path.relpath(LOG, NVL))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
