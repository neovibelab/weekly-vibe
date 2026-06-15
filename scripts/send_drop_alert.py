#!/usr/bin/env python3
"""정시 리포트 드롭 누락 시 대표에게 메일 알림 (Gmail SMTP, app password).

report-drop-watchdog 전용. 백업 재발송 결과(HEAL_OUTCOME)에 따라 문구가 갈린다.
메일 발송 실패가 워크플로를 죽이지 않도록 항상 0으로 끝낸다(판정은 별도 스텝).

env:
  GMAIL_USER, GMAIL_APP_PASS  Gmail 계정 + 앱비밀번호 (IMAP 수집용과 동일)
  HEAL_OUTCOME                백업 재발송 스텝 outcome (success/failure)
  DRY_RUN=1                   발송 없이 본문만 출력
"""
import os
import ssl
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate

TO_ADDR = "woojin@neovibelab.com"


def build_message():
    ok = os.environ.get("HEAL_OUTCOME") == "success"
    if ok:
        subject = "[NVL] 주간 리포트 드롭 — 정시 누락, 백업 발송 완료"
        body = (
            "월요일 정시 스케줄(discord-report-drop, 10:17 KST)이 발화하지 않아\n"
            "백업 감시(report-drop-watchdog)가 드롭을 디스코드에 재발송했습니다.\n\n"
            "→ 드롭은 정상 전송됨. 별도 조치 불필요.\n"
            "→ 정시 누락이 반복되면 GitHub cron 신뢰성을 점검하세요.\n"
        )
    else:
        subject = "[NVL] ⚠️ 주간 리포트 드롭 — 정시 누락 + 백업 실패 (수동 조치 필요)"
        body = (
            "월요일 정시 스케줄이 발화하지 않았고, 백업 재발송도 실패했습니다.\n\n"
            "→ 수동 조치 필요: `gh workflow run discord-report-drop.yml` 실행\n"
            "  또는 GitHub Actions에서 워크플로 로그를 확인하세요.\n"
        )
    return subject, body


def main():
    subject, body = build_message()
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASS")

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = user or "nvl-bot"
    msg["To"] = TO_ADDR
    msg["Date"] = formatdate(localtime=True)

    if os.environ.get("DRY_RUN") == "1":
        print(f"[dry-run] To: {TO_ADDR}\n[dry-run] Subject: {subject}\n\n{body}")
        return 0
    if not user or not pw:
        print("[warn] GMAIL_USER/GMAIL_APP_PASS 미설정 — 메일 생략")
        return 0

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.sendmail(user, [TO_ADDR], msg.as_string())
        print(f"[info] 알림 메일 발송 완료 → {TO_ADDR}")
    except Exception as e:  # noqa: BLE001 — 메일 실패는 워크플로를 죽이지 않음
        print(f"[warn] 메일 발송 실패(무시): {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
