#!/usr/bin/env python3
"""오늘(KST) 주간 리포트 드롭이 이미 발송됐는지 GitHub 런 이력으로 판정.

report-drop-watchdog 전용. posted=yes/no 를 GITHUB_OUTPUT 에 기록.

판정 규칙:
  - discord-report-drop.yml 런이 오늘(KST) success 또는 진행중(queued/in_progress) → yes
    (진행중이면 정시 드롭이 지연 실행 중 → 백업이 끼어들지 않음 = 중복 방지)
  - report-drop-watchdog.yml 런이 오늘(KST) success → yes
    (이미 백업이 한 번 처리함 → 수동 재실행 시 중복 발송 방지. 현재 런은 in_progress라 제외됨)
  - 그 외 → no (백업 재발송 필요)

env: GH_TOKEN (gh CLI 인증)
"""
import os
import json
import subprocess
import datetime

ACTIVE = {"queued", "in_progress", "waiting", "requested", "pending"}


def kst_today():
    now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    return now.date()


def to_kst_date(iso_z):
    dt = datetime.datetime.fromisoformat(iso_z.replace("Z", "+00:00"))
    return (dt + datetime.timedelta(hours=9)).date()


def list_runs(workflow):
    p = subprocess.run(
        ["gh", "run", "list", f"--workflow={workflow}",
         "--json", "conclusion,status,createdAt", "--limit", "30"],
        capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        print(f"[warn] gh run list 실패({workflow}): {p.stderr.strip()}")
        return []
    return json.loads(p.stdout) if p.stdout.strip() else []


def main():
    today = kst_today()
    posted, reason = "no", "오늘 발송 흔적 없음"

    # (a) 정시 워크플로: 오늘 success 또는 진행중
    for r in list_runs("discord-report-drop.yml"):
        if to_kst_date(r["createdAt"]) != today:
            continue
        if r.get("status") in ACTIVE or r.get("conclusion") == "success":
            posted = "yes"
            reason = f"정시 런 감지(status={r.get('status')}, conclusion={r.get('conclusion')})"
            break

    # (b) 백업 자신이 오늘 이미 처리했는지 (중복 방지)
    if posted == "no":
        for r in list_runs("report-drop-watchdog.yml"):
            if to_kst_date(r["createdAt"]) != today:
                continue
            if r.get("conclusion") == "success":
                posted = "yes"
                reason = "오늘 백업 감시가 이미 처리함"
                break

    print(f"[info] posted={posted} — {reason} (KST {today})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"posted={posted}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
