#!/usr/bin/env python3
"""주간 신규 뉴스레터 발신자 감지 — allowlist·ignore 밖 발신자를 모아 woojin@에 후보 리포트.

왜 필요한가: 수집기가 allowlist 방식이라 대표가 새 뉴스레터를 구독해도 말하지 않으면
조용히 샌다(2026-07-16 대표 "추가할 때마다 알려줘야 할까?" → 자동 감지로 해소).
등재 판단은 사람이(세션/대표), 감지만 자동.

동작: 최근 SCAN_DAYS(7)일 받은편지함 발신자를 집계 → sources_newsletters.json의
sources(from, 비활성 _ 포함)·catchall_ignore와 대조 → 남는 발신자를 통수·예시 제목과
함께 woojin@ 메일로 보고. 후보 0이면 메일 없음. 발송 실패는 워크플로를 죽이지 않는다.

캐치올(제목 게이트, newsletter_ingest.py)과의 관계: 캐치올은 미등재 발신자의 개별
'신호'를 건져오고, 이 스캔은 발신자 자체의 allowlist '승격'을 제안한다(등재하면
지역힌트·broad 게이트가 정확해짐). 반복 후보를 무시하려면 catchall_ignore에 추가.

env:
  GMAIL_USER, GMAIL_APP_PASS   Gmail 계정 + 앱비밀번호(IMAP·SMTP 공용)
  SCAN_DAYS                    기본 7
  DRY_RUN=1                    발송 없이 본문만 출력
사용: python scripts/newsletter_sender_scan.py
"""
import datetime
import email
import imaplib
import json
import os
import smtplib
import ssl
import sys
from collections import defaultdict
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import formatdate, parseaddr

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWLIST_PATH = os.path.join(os.path.dirname(HERE), "sources_newsletters.json")
TO_ADDR = "woojin@neovibelab.com"
SCAN_DAYS = int(os.environ.get("SCAN_DAYS", "7"))


def decode_hdr(s: str) -> str:
    if not s:
        return ""
    out = []
    for txt, enc in decode_header(s):
        out.append(txt.decode(enc or "utf-8", "replace") if isinstance(txt, bytes) else txt)
    return "".join(out)


def collect_candidates() -> list[tuple[str, int, str]]:
    """(발신자, 통수, 예시 제목) 목록 — allowlist·ignore 밖만, 통수 내림차순."""
    with open(ALLOWLIST_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    known = [s.get("from", "").lstrip("_").lower() for s in cfg["sources"] if s.get("from")]
    known += [k.lower() for k in cfg.get("catchall_ignore", [])]

    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASS"].replace(" ", ""))
    M.select("INBOX", readonly=True)
    since = (datetime.date.today() - datetime.timedelta(days=SCAN_DAYS)).strftime("%d-%b-%Y")
    typ, data = M.search(None, f'(SINCE "{since}")')
    nums = data[0].split() if typ == "OK" and data and data[0] else []

    counts: dict[str, int] = defaultdict(int)
    samples: dict[str, str] = {}
    for num in nums:
        typ, data = M.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            continue
        h = email.message_from_bytes(data[0][1])
        sender = (parseaddr(decode_hdr(h.get("From", "")))[1] or "").lower()
        if not sender or any(k in sender for k in known):
            continue
        counts[sender] += 1
        samples.setdefault(sender, decode_hdr(h.get("Subject", "")).strip())
    try:
        M.logout()
    except Exception:
        pass
    return sorted(((s, n, samples.get(s, "")) for s, n in counts.items()),
                  key=lambda t: -t[1])


def build_message(cands: list[tuple[str, int, str]]) -> tuple[str, str]:
    subject = f"[NVL] 📬 미등재 뉴스레터 발신자 {len(cands)}건 — allowlist 후보"
    lines = [
        f"최근 {SCAN_DAYS}일 받은편지함에서 수집 allowlist 밖 발신자가 감지됐습니다.",
        "등재하면 지역힌트·broad 게이트가 정확해집니다 (미등재여도 캐치올 제목 게이트는 개별 신호를 건져옵니다).",
        "",
    ]
    for sender, n, sample in cands[:30]:
        lines.append(f"- {sender} ({n}통)  예: {sample[:70]}")
    if len(cands) > 30:
        lines.append(f"… 외 {len(cands) - 30}건")
    lines += [
        "",
        "처리: Claude Code 세션에서 \"뉴스레터 스윕\" — 등재는 sources_newsletters.json sources에,",
        "무시(다시 안 보기)는 catchall_ignore에 추가.",
    ]
    return subject, "\n".join(lines)


def main() -> int:
    cands = collect_candidates()
    if not cands:
        print("[info] 미등재 발신자 없음 — 메일 생략")
        return 0
    subject, body = build_message(cands)
    if os.environ.get("DRY_RUN") == "1":
        print(f"[dry-run] To: {TO_ADDR}\n[dry-run] Subject: {subject}\n\n{body}")
        return 0
    user, pw = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASS")
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
        print(f"[info] 발신자 스캔 리포트 발송 완료 → {TO_ADDR} ({len(cands)}건)")
    except Exception as e:  # noqa: BLE001 — 메일 실패는 워크플로를 죽이지 않음
        print(f"[warn] 메일 발송 실패(무시): {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
