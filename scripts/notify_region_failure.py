#!/usr/bin/env python3
"""지역 수집 스텝이 검색 실패(API/코드 에러)로 끝나면 **워크플로를 실패시킨다**(메일 경보는 2026-09-14 폐기).

ai-news-daily.yml 전용. 각 지역 스텝의 outcome을 OUTCOME_<REGION> env로 받아
'failure'인 지역만 모아 `::error::` + exit 1로 잡을 빨갛게 만든다.

왜 필요한가: 지역 스텝이 continue-on-error라 한 지역이 죽어도 잡 전체는
success로 떠서 gh run·디스코드에 티가 안 난다. 글로벌이 sony.com 400으로
이틀 침묵 실패한 사고(2026-06-15~16)가 며칠 뒤에야 발견됐다. 잡 실패가
침묵 실패에 대한 유일한 능동 경보다.

0건과 실패의 구분: 정상적인 '후보 0건'은 vibe_search가 exit 0 → outcome
success → 여기 안 잡힌다. web_search API 호출 자체가 실패한 경우만
vibe_search가 exit 1 → outcome failure → 알림. 정상 0건엔 잡이 성공으로 끝난다.

실패 지역이 있을 때만 exit 1. 없으면 0.

env:
  OUTCOME_<REGION>             각 지역 스텝 outcome (success/failure/skipped/'')
  RUN_URL                      (선택) 해당 GitHub Actions 실행 URL
  DRY_RUN=1                    발송 없이 본문만 출력
"""
import os
import sys

# Windows 콘솔(cp949)에서 이모지·em-dash 출력 시 UnicodeEncodeError 방지.
# GitHub Actions(Linux)는 UTF-8 기본이라 무영향 — 로컬 DRY_RUN 테스트 호환용.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# env 키 → 한국어 지역명 (워크플로 알림 스텝의 OUTCOME_* 와 일치시킬 것)
REGIONS = [
    ("OUTCOME_KOREA", "한국"),
    ("OUTCOME_GLOBAL", "영어권 검색"),  # 검색 프로파일 이름 (2026-09-10)
    ("OUTCOME_CHINA", "중국"),
    ("OUTCOME_JAPAN", "일본"),
    ("OUTCOME_SEA", "동남아"),
]


def failed_regions() -> list[str]:
    """outcome이 'failure'인 지역명 목록. skipped/success/미설정은 무시."""
    return [name for env, name in REGIONS if os.environ.get(env) == "failure"]


def read_failure_reasons() -> list[tuple[str, str, str]]:
    """vibe_search가 남긴 실패 사유 마커 파일(같은 잡 내 작성) → (지역, 분류, 메시지) 목록.
    분류 'credit'이 하나라도 있으면 메일에 크레딧 충전 안내를 명시한다."""
    path = os.environ.get("FAILURE_REASON_FILE", "region-failures.txt")
    out: list[tuple[str, str, str]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2:
                    out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    except OSError:
        pass
    return out


def build_message(failed: list[str], reasons: list[tuple[str, str, str]] | None = None) -> tuple[str, str]:
    joined = ", ".join(failed)
    reasons = reasons or []
    is_credit = any(cat == "credit" for _, cat, _ in reasons)
    subject = f"[NVL] ⚠️ Vibe 수집 실패 — {joined}"
    if is_credit:
        subject += " (크레딧 잔액 부족)"
    lines = [
        "오늘 Vibe 후보 수집에서 다음 지역이 검색 실패(API/코드 에러)로 끝났습니다:",
        f"  → {joined}",
        "",
        "이는 '후보 0건'(정상)이 아니라 web_search API 호출 자체가 실패한 경우입니다.",
        "해당 지역 Discord 채널은 오늘 비어 있습니다.",
    ]
    if is_credit:
        lines += [
            "",
            "■ 확인된 원인: Anthropic API 크레딧 잔액 부족 (400 invalid_request_error)",
            "  → https://console.anthropic.com 의 Plans & Billing에서 크레딧을 충전하세요.",
            "  충전하면 다음 예약 수집부터 자동 정상화됩니다.",
            "  ※ 뉴스룸·뉴스레터 기사 미번역도 같은 원인 — 충전 후",
            "    scripts/backfill_translate.py 로 누적분 일괄 재번역.",
        ]
    else:
        lines += [
            "",
            "점검:",
            "  - allowed_domains에 Anthropic 크롤러 차단 도메인이 끼었는지 (400 거부)",
            "  - ANTHROPIC_API_KEY 유효성·사용 한도·크레딧 잔액",
            "  - GitHub Actions 로그의 '[지역] 검색 실패:' 라인",
        ]
    run_url = os.environ.get("RUN_URL", "").strip()
    if run_url:
        lines += ["", f"실행 로그: {run_url}"]
    return subject, "\n".join(lines)


def main() -> int:
    """실패 지역이 있으면 **워크플로를 실패시킨다**(메일은 2026-09-14 폐기).

    대표 지시 - 「[NVL] 경보 메일이 불필요하다」. 다만 **침묵 실패는 그대로 두면 안 된다** -
    지역 스텝이 continue-on-error라 한 지역이 죽어도 잡 전체가 success로 뜨고,
    2026-06-15~16에 글로벌이 이틀 침묵 실패한 것이 며칠 뒤에야 발견됐다.

    그래서 알림 경로를 **메일에서 GitHub Actions 실패로 바꿨다** - `::error::` + exit 1이면
    잡이 빨갛게 뜨고 GitHub가 자기 알림을 보낸다. **추가 시크릿·채널이 필요 없다.**
    `report-drop-watchdog.yml`의 「결과 판정」 스텝이 이미 쓰는 방식이다.

    **되돌림** - 잡 실패가 눈에 안 띄어 침묵 실패가 또 늦게 발견되면, 메일이 아니라
    **Discord 경보 전용 웹훅**을 세운다(이미 매일 쓰는 채널이라 눈에 띈다).
    """
    failed = failed_regions()
    if not failed:
        print("[info] 실패 지역 없음")
        return 0

    _, body = build_message(failed, read_failure_reasons())
    print(body)
    joined = ", ".join(failed)
    print(f"::error title=Vibe 수집 실패::{joined} - 검색 실패로 끝났다. 로그 확인 요망")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
