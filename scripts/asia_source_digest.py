#!/usr/bin/env python3
"""엔터 아시아 소스 다이제스트 — 매주 금요일 낮 대표 메일.

무료 위클리 뉴스레터 "엔터 아시아"(letter-01-mon-asia, 매주 월요일 발행)의
제작용 소스를 금요일 낮에 woojin@neovibelab.com로 전달한다. Supabase
radar_items에서 최근 14일·일본/중국/동남아 활성 신호를 지역별로 정리해
헤드라인 후보와 함께 보낸다. 실제 초안 작성은 대표 세션의 몫(주말 검토 확보).

왜 GitHub Actions인가: 앱 상태에 의존하는 로컬 예약작업과 달리 클라우드에서
확실히 발화한다. 07-17 금요일 제작 세션이 프로세스 개편에 밀려 3호가 조용히
누락된 사고의 재발 방지 — 소스가 매주 금요일 대표 받은편지함에 도착한다.

env:
  SUPABASE_URL, SUPABASE_KEY    radar_items 조회 (기존 수집기와 동일 시크릿)
  GMAIL_USER, GMAIL_APP_PASS    Gmail 계정 + 앱비밀번호 (실패 경보·드롭 알림과 동일)
  RUN_URL                       (선택) GitHub Actions 실행 URL
  --dry-run / DRY_RUN=1         발송 없이 본문만 출력

로컬 DRY_RUN 테스트: SUPABASE_URL 미설정 시 weekly-vibe/.env →
nvl-vibe-radar/.env 순으로 크리덴셜을 읽는다(개발 편의, Actions에선 무영향).
"""
import os
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TO_ADDR = "woojin@neovibelab.com"
KST = timezone(timedelta(hours=9))
FIRST_ISSUE_MONDAY = date(2026, 7, 6)  # 통권 1호 발행일 (회차 추정 기준점)
REGIONS = [
    ("japan", "🇯🇵 일본"),
    ("china", "🇨🇳 중국"),
    ("southeast_asia", "🌏 동남아"),
    ("southeast-asia", "🌏 동남아"),  # region 값 변형(하이픈) 방어
]
LOOKBACK_DAYS = 14
HIDDEN_STATUS = {"archived", "filtered_out"}


def _load_local_env() -> None:
    """Actions 밖(로컬)에서만 동작 — 크리덴셜을 .env에서 setdefault."""
    if os.environ.get("SUPABASE_URL"):
        return
    here = Path(__file__).resolve()
    for cand in (
        here.parent.parent / ".env",                       # weekly-vibe/.env
        here.parent.parent.parent / "nvl-vibe-radar" / ".env",
    ):
        if cand.exists():
            for line in cand.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def upcoming_monday(today: date) -> date:
    days_ahead = (0 - today.weekday() + 7) % 7
    if days_ahead == 0:
        days_ahead = 7  # 오늘이 월요일이면 다음 주 월요일
    return today + timedelta(days=days_ahead)


def issue_number(monday: date) -> int:
    return ((monday - FIRST_ISSUE_MONDAY).days // 7) + 1


def fetch_items() -> list[dict]:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    regions = ",".join(sorted({r for r, _ in REGIONS}))
    params = {
        "select": "*",
        "region": f"in.({regions})",
        "published_date": f"gte.{cutoff}",
        "order": "total_score.desc.nullslast,published_date.desc",
    }
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(f"{base}/rest/v1/radar_items", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return [it for it in r.json() if it.get("status") not in HIDDEN_STATUS]


def _fmt_item(it: dict) -> str:
    score = it.get("total_score") or 0
    pd = (it.get("published_date") or "")[:10]
    coll = it.get("collector") or "?"
    title = it.get("title") or "(제목 없음)"
    url = it.get("url") or ""
    summ = (it.get("summary") or "").strip().replace("\n", " ")
    if len(summ) > 220:
        summ = summ[:220] + "…"
    lines = [f"  [{score}] {pd} · {coll}", f"    {title}", f"    {url}"]
    if summ:
        lines.append(f"    {summ}")
    return "\n".join(lines)


def build_message(items: list[dict], monday: date, n: int) -> tuple[str, str]:
    # 지역 정규화(하이픈 → 언더스코어 라벨 통합)
    label_of = {r: lab for r, lab in REGIONS}
    buckets: dict[str, list[dict]] = {}
    for it in items:
        lab = label_of.get(it.get("region"), it.get("region") or "기타")
        buckets.setdefault(lab, []).append(it)

    total = len(items)
    subject = f"🌏 엔터 아시아 소스 — {monday.isoformat()}(월) {n}호 제작용 · 활성 {total}건"

    top = sorted(items, key=lambda x: (x.get("total_score") or 0), reverse=True)[:3]
    head = "\n".join(
        f"  · [{t.get('total_score') or 0}] {t.get('title')}  ({label_of.get(t.get('region'), t.get('region'))})"
        for t in top
    ) or "  (후보 없음 — 이번 주는 WebSearch 보강 비중을 크게)"

    body = [
        f"다가오는 월요일 {monday.isoformat()} 발행 예정 '엔터 아시아 {n}호(추정)' 제작용 소스입니다.",
        f"Supabase radar_items 최근 {LOOKBACK_DAYS}일 · 일본·중국·동남아 활성 {total}건.",
        "",
        "── 헤드라인 후보 (점수순 상위 3, 최종 판단은 세션에서) ──",
        head,
        "",
        "※ 소재 우선순위: 해설·기획·분석 > 스트레이트. 한국 매체 소스는 2순위(현지·영문 현지보도 우선).",
        "",
    ]

    seen_labels: list[str] = []
    for _, lab in REGIONS:
        if lab in seen_labels:
            continue
        seen_labels.append(lab)
        group = buckets.get(lab, [])
        body.append(f"{'='*60}")
        body.append(f"{lab} ({len(group)}건)")
        body.append(f"{'='*60}")
        if not group:
            body.append("  (이번 주 활성 신호 없음 — WebSearch로 보강 필요)")
        else:
            for it in group:
                body.append(_fmt_item(it))
        body.append("")

    body += [
        "───────────────────────────────",
        "▶ 착수: 세션에서 \"미네바로 엔터 아시아 {}호 제작해줘\" 라고 하면 됩니다.".format(n),
        "  · 파이프라인 정본: ecri-newsletter/letter-01-mon-asia/CLAUDE.md",
        "  · WebSearch 보강(step 1.5) 필수 — radar 풀만으로 큐레이션하지 않음(특히 동남아).",
        "  · 산출: draft-v1 → notation-check → -draft-final 동결 → -발행 사본(대표 검토).",
        "  · 이 메일은 소스 전달·리마인더까지입니다. 초안은 대표 세션에서 작성(주말 검토 확보).",
    ]
    run_url = os.environ.get("RUN_URL", "").strip()
    if run_url:
        body += ["", f"실행 로그: {run_url}"]
    return subject, "\n".join(body)


def main() -> int:
    _load_local_env()
    dry = "--dry-run" in sys.argv or os.environ.get("DRY_RUN") == "1"

    today_kst = datetime.now(KST).date()
    monday = upcoming_monday(today_kst)
    n = issue_number(monday)

    try:
        items = fetch_items()
    except Exception as e:  # noqa: BLE001 — 조회 실패해도 리마인더는 보낸다
        print(f"[warn] radar_items 조회 실패: {e} — 빈 소스로 리마인더만 발송")
        items = []

    subject, body = build_message(items, monday, n)

    if dry:
        print(f"[dry-run] To: {TO_ADDR}\n[dry-run] Subject: {subject}\n\n{body}")
        return 0

    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASS")
    if not user or not pw:
        print("[warn] GMAIL_USER/GMAIL_APP_PASS 미설정 — 메일 생략")
        return 0

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = TO_ADDR
    msg["Date"] = formatdate(localtime=True)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.sendmail(user, [TO_ADDR], msg.as_string())
        print(f"[info] 엔터 아시아 소스 메일 발송 완료 → {TO_ADDR} (활성 {len(items)}건, {n}호)")
    except Exception as e:  # noqa: BLE001 — 메일 실패는 워크플로를 죽이지 않음
        print(f"[warn] 메일 발송 실패(무시): {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
