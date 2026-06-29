#!/usr/bin/env python3
"""drops/ 최신 주간 리포트 드롭 1편을 Discord 웹훅으로 전송.

primary(discord-report-drop) · backup(report-drop-watchdog) 워크플로 공용 모듈.
stdlib만 사용(설치 불필요). YAML 인라인 heredoc의 들여쓰기 취약성을 제거하기 위해
발송 로직을 이 파일로 분리한다.

env:
  DISCORD_REPORT_WEBHOOK_URL  전송 대상 웹훅 (필수, DRY_RUN 시 불필요)
  DRY_RUN=1                   전송 없이 찾기·정제까지만 (로컬 검증용)
종료코드: 0 성공 / 1 실패(파일 없음·빈 내용·204 외 응답)
"""
import os
import re
import sys
import glob
import json
import urllib.request
import urllib.error

DROP_GLOB = "drops/*-주간리포트드롭.md"
DISCORD_LIMIT = 1990  # 2000자 하드리밋 - 안전 마진 10자


def find_latest_drop():
    """파일명(YY.MM.DD) 기준 사전순 최신 드롭 1개."""
    files = sorted(glob.glob(DROP_GLOB))
    return files[-1] if files else None


def clean_content(path):
    """HTML 주석 제거 → 트림 → 2000자 제한."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    if len(text) > DISCORD_LIMIT:
        text = text[:DISCORD_LIMIT] + "\n…(이하 생략)"
    return text


def post_to_discord(webhook, content):
    """POST → (http_status, body). Discord 성공은 204 No Content."""
    data = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data,
        headers={
            "Content-Type": "application/json",
            # Discord Cloudflare가 기본 Python-urllib UA를 403(error 1010)으로 차단 → 명시 UA 필수 (2026-06-29)
            "User-Agent": "Mozilla/5.0 (compatible; NVL-report-drop/1.0; +https://neovibelab.com)",
        }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    path = find_latest_drop()
    if not path:
        print("::error::drops/ 폴더에 드롭 파일이 없습니다")
        return 1
    print(f"[info] 드롭 파일: {path}")

    content = clean_content(path)
    if not content:
        print("::error::드롭 파일 내용이 비어 있습니다")
        return 1
    print(f"[info] 전송 내용 길이: {len(content)}자")

    if os.environ.get("DRY_RUN") == "1":
        print("[dry-run] 전송 생략")
        return 0

    webhook = os.environ.get("DISCORD_REPORT_WEBHOOK_URL")
    if not webhook:
        print("::error::DISCORD_REPORT_WEBHOOK_URL 미설정")
        return 1

    code, body = post_to_discord(webhook, content)
    print(f"Discord webhook HTTP: {code}")
    if body:
        print(f"Discord response: {body}")
    if code != 204:
        print(f"::error::Discord webhook 실패 (HTTP {code})")
        return 1
    print("[info] Discord 전송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
