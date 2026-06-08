#!/usr/bin/env python3
"""
Vibe Signal Collector v2 — Anthropic web_search 기반 통합 수집기
--------------------------------------------------------------
뉴스레터·캐러셀 소재 탐색을 위한 웹 검색 기반 Vibe 후보 수집.
Anthropic web_search 서버 사이드 도구로 검색+분석+요약을 단일 API 호출로 처리.

사용법:
  python scripts/vibe_search.py <topic>
  python scripts/vibe_search.py <topic> --dry-run

  <topic>: fan-behavior | consumer-behavior | ent-deals |
           ip-business | artist-ownership | tech-issues

환경변수:
  ANTHROPIC_API_KEY              Claude API 키
  DISCORD_<TOPIC>_WEBHOOK        Discord 웹훅 (주제별)
  SEEN_FILE                      채널 간 중복 제거 파일 (기본: seen-titles.txt)
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import logging
import os
import re
import sys
import time
from difflib import SequenceMatcher

import requests
from anthropic import Anthropic

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── 주제 설정 ─────────────────────────────────────────────

TOPICS: dict[str, dict] = {
    "fan-behavior": {
        "name": "팬 행동",
        "emoji": "👥",
        "webhook_env": "DISCORD_FAN_BEHAVIOR_WEBHOOK",
        "industry_priority": "음악 > 엔터 > 브랜드",
        "geo_priority": "신흥시장(동남아, 남미, 중동) > 중국, 미국, 일본 > 한국",
        "description": (
            "팬덤의 행동 변화·소비 패턴·참여 방식·조직화·경제적 영향력 관련 사례. "
            "팬 주도 캠페인, 팬덤 경제, 콘서트·굿즈·스트리밍 소비 행태, "
            "팬 커뮤니티의 시장 영향력."
        ),
    },
    "consumer-behavior": {
        "name": "소비자 행동",
        "emoji": "🛍️",
        "webhook_env": "DISCORD_CONSUMER_BEHAVIOR_WEBHOOK",
        "industry_priority": "라이프스타일·브랜드",
        "geo_priority": "신흥시장(동남아, 남미, 중동) > 중국, 미국, 일본 > 한국",
        "description": (
            "문화 소비와 라이프스타일 트렌드. 브랜드×문화 협업, "
            "세대별·지역별 소비 패턴, 문화 소비 데이터·리포트, "
            "리테일·F&B·패션과 엔터의 교차점."
        ),
    },
    "ent-deals": {
        "name": "엔터업계 딜",
        "emoji": "💰",
        "webhook_env": "DISCORD_ENT_DEALS_WEBHOOK",
        "industry_priority": "음악 > 엔터 > 브랜드",
        "geo_priority": "글로벌 (지역 무관)",
        "description": (
            "엔터테인먼트 산업의 투자·인수합병·파트너십·라이선싱 딜. "
            "음악 레이블·퍼블리싱·스트리밍 플랫폼 M&A, "
            "사모펀드·벤처의 엔터 투자, 카탈로그 거래, 주요 계약."
        ),
    },
    "ip-business": {
        "name": "IP 비즈니스 사례",
        "emoji": "🎯",
        "webhook_env": "DISCORD_IP_BUSINESS_WEBHOOK",
        "industry_priority": "음악 > 엔터 > 브랜드",
        "geo_priority": "중국 + 미국 + 일본 우선",
        "description": (
            "IP(지식재산) 활용 비즈니스 사례. 음악 IP의 다각화(영화·게임·테마파크), "
            "캐릭터·웹툰·애니메이션 IP 확장, 크로스미디어 전략, "
            "IP 가치평가·수익화 모델."
        ),
    },
    "artist-ownership": {
        "name": "아티스트 오너십",
        "emoji": "🎤",
        "webhook_env": "DISCORD_ARTIST_OWNERSHIP_WEBHOOK",
        "industry_priority": "음악 > 엔터·문화",
        "geo_priority": "중국 + 미국 + 일본 우선",
        "description": (
            "아티스트가 비즈니스의 오너십을 확보한 사례. "
            "자체 레이블·매니지먼트 설립, 마스터 소유권 회수, "
            "아티스트 주도 투자·브랜드·테크 벤처, 크리에이터 이코노미."
        ),
    },
    "tech-issues": {
        "name": "테크 이슈",
        "emoji": "⚡",
        "webhook_env": "DISCORD_TECH_ISSUES_WEBHOOK",
        "industry_priority": "음악 > 엔터",
        "geo_priority": "중국 + 미국 + 일본 우선",
        "description": (
            "음악·엔터 산업에 영향을 미치는 기술 이슈. "
            "AI 음악 생성·저작권, 스트리밍 기술·알고리즘, "
            "블록체인, 가상 아티스트, 플랫폼 정책 변화, 음악 테크 스타트업."
        ),
    },
}

MAX_CANDIDATES = 5
DUPLICATE_THRESHOLD = 0.75

# ── 프롬프트 ──────────────────────────────────────────────


def build_search_prompt(topic: dict) -> str:
    return (
        "당신은 엔터테인먼트·음악 산업 전문 Vibe 신호 수집기입니다.\n\n"
        f"## 수집 주제\n{topic['name']}: {topic['description']}\n\n"
        f"## 우선순위\n- 산업: {topic['industry_priority']}\n"
        f"- 지역: {topic['geo_priority']}\n\n"
        "## 지시\n\n"
        "최근 48시간 이내의 뉴스·기사·보도를 웹 검색으로 찾으세요.\n"
        "뉴스레터와 인스타그램 캐러셀 소재로 활용할 수 있는 사례를 선별합니다.\n\n"
        "검색 시 다음을 고려하세요:\n"
        "- 검색은 영어로. 요약은 한국어로.\n"
        "- 산업 우선순위에 따라 음악 산업 관련 사례를 먼저 검색\n"
        "- 지역 우선순위에 따라 해당 지역 사례를 우선 탐색\n"
        "- 신뢰할 수 있는 매체 우선 (Billboard, Variety, Music Business Worldwide, "
        "TechCrunch, Financial Times, Bloomberg, 업계 전문지 등)\n"
        "- 구체적 수치·데이터·사례가 포함된 기사 우선\n"
        "- 다양한 검색어로 최소 3회 검색하여 폭을 넓히세요\n\n"
        "## 선별 기준 (각 0~2점)\n"
        "1. 소재적합: 뉴스레터 칼럼 소재로서 해석 가능한 구체적 사례·데이터가 있는가\n"
        "   (0=일반 뉴스, 1=관점 가능, 2=풍부한 사례+데이터)\n"
        "2. 캐러셀적합: 태도→증거→함의→질문 서사 아크를 만들 수 있는가\n"
        "   (0=아크 불가, 1=단일 포인트, 2=완전한 아크 가능)\n"
        "3. 출처신뢰: 출처가 확인 가능하고 1차 자료에 근거하는가\n"
        "   (0=출처 불분명, 1=2차 보도, 2=1차 자료/공식 발표)\n\n"
        "## 출력\n\n"
        f"검색 결과 중 total_score(3개 합산)가 3점 이상인 후보를 최대 {MAX_CANDIDATES}개 선택하세요.\n"
        "JSON 배열만 출력하고 다른 텍스트는 추가하지 마세요.\n\n"
        "```json\n"
        "[\n"
        '  {\n'
        '    "title": "기사 제목 (원문 그대로)",\n'
        '    "url": "출처 URL",\n'
        '    "source": "매체명",\n'
        '    "summary": "200자 이내 한국어 요약. 레이블 없이 이어서.",\n'
        '    "newsletter_fit": 0,\n'
        '    "carousel_fit": 0,\n'
        '    "reliability": 0,\n'
        '    "total_score": 0\n'
        "  }\n"
        "]\n"
        "```\n\n"
        "후보가 없으면 빈 배열 `[]`을 출력하세요."
    )


# ── 검색 ──────────────────────────────────────────────────


def search_and_analyze(client: Anthropic, topic: dict) -> list[dict]:
    """Anthropic web_search로 검색+분석+요약을 단일 API 호출로 처리."""
    prompt = build_search_prompt(topic)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        tools=[{"type": "web_search_20260209", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    if response.stop_reason == "max_tokens":
        log.warning("응답이 max_tokens로 잘림 — 일부 결과만 사용")

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    if not text.strip():
        log.warning("텍스트 응답 없음")
        return []

    cleaned = text.strip()
    if "```" in cleaned:
        cleaned = re.sub(r"```\w*\n?", "", cleaned)

    match = re.search(r"\[[\s\S]*\]", cleaned)
    if not match:
        log.warning("JSON 파싱 실패: %s", text[:300])
        return []

    try:
        candidates = json.loads(match.group())
    except json.JSONDecodeError as exc:
        log.warning("JSON 디코드 실패: %s", exc)
        return []

    return [c for c in candidates if isinstance(c, dict)]


# ── 중복 제거 ─────────────────────────────────────────────


def load_seen_titles(seen_file: str) -> list[str]:
    if not os.path.exists(seen_file):
        return []
    with open(seen_file, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def is_cross_dup(title: str, seen_titles: list[str]) -> bool:
    t = title.lower()
    return any(
        SequenceMatcher(None, t, s.lower()).ratio() >= DUPLICATE_THRESHOLD
        for s in seen_titles
    )


# ── Discord ───────────────────────────────────────────────


def _get_indicators(c: dict) -> list[str]:
    indicators: list[str] = []
    if c.get("newsletter_fit", 0) > 0:
        indicators.append("소재적합")
    if c.get("carousel_fit", 0) > 0:
        indicators.append("캐러셀적합")
    if c.get("reliability", 0) > 0:
        indicators.append("출처신뢰")
    return indicators


def build_discord_message(c: dict) -> str:
    score = c.get("total_score", 0)
    badge = "🟢" if score >= 5 else "🟡"
    indicators = _get_indicators(c)
    indicator_str = "·".join(indicators) if indicators else "—"

    title = c["title"][:100]
    url = c.get("url", "")
    title_part = f"[**{title}**]({url})" if url else f"**{title}**"
    summary = (c.get("summary", "") or "").strip()[:500]

    msg = f"{badge} {title_part} `{indicator_str}`"
    if summary:
        msg += f"\n> {summary}"
    return msg[:1900]


def send_to_discord(webhook_url: str, content: str) -> None:
    payload = {"content": content[:2000], "flags": 4}
    resp = requests.post(webhook_url, json=payload, timeout=15)
    if resp.status_code not in (200, 204):
        raise RuntimeError(
            f"Discord 웹훅 실패 (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    log.info("Discord 전송 완료 (HTTP %d)", resp.status_code)


# ── 메인 ──────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Vibe Signal Collector v2")
    parser.add_argument("topic", choices=list(TOPICS.keys()), help="수집 주제")
    parser.add_argument("--dry-run", action="store_true", help="Discord 전송 안 함")
    args = parser.parse_args()

    topic = TOPICS[args.topic]
    topic_name = topic["name"]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    webhook_url = os.environ.get(topic["webhook_env"])
    seen_file = os.environ.get("SEEN_FILE", "seen-titles.txt")

    if not api_key:
        log.error("ANTHROPIC_API_KEY 환경변수 미설정")
        return 1
    if not webhook_url and not args.dry_run:
        log.warning("%s 환경변수 미설정 — 전송 생략", topic["webhook_env"])
        return 0

    client = Anthropic(api_key=api_key)
    today = datetime.date.today().strftime("%Y-%m-%d")

    # 1. 웹 검색 + 분석
    log.info("[%s] 웹 검색 시작", topic_name)
    try:
        candidates = search_and_analyze(client, topic)
    except Exception as exc:
        log.error("[%s] 검색 실패: %s", topic_name, exc)
        return 0

    if not candidates:
        log.info("[%s] 후보 없음 — 전송 생략", topic_name)
        return 0

    log.info("[%s] 후보 %d건 수집", topic_name, len(candidates))

    # 2. 채널 간 중복 제거
    seen_titles = load_seen_titles(seen_file)
    candidates = [c for c in candidates if not is_cross_dup(c["title"], seen_titles)]
    if not candidates:
        log.info("[%s] 중복 제거 후 후보 없음", topic_name)
        return 0

    selected = candidates[:MAX_CANDIDATES]

    # 3. 선택 로그 (evening_digest.py 호환 형식)
    for c in selected:
        indicators = _get_indicators(c)
        log.info(
            "선택: [%d지표] %s | %s",
            c.get("total_score", 0),
            c["title"][:60],
            c.get("url", ""),
        )
        log.info(
            "선택메타: %s",
            json.dumps(
                {
                    "summary": (c.get("summary") or "")[:200],
                    "indicators": indicators,
                },
                ensure_ascii=False,
            ),
        )

    if args.dry_run:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        return 0

    # 4. Discord 전송
    header = (
        f"{topic['emoji']} **{topic_name} Vibe | {today}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    send_to_discord(webhook_url, header)
    for c in selected:
        time.sleep(30)
        send_to_discord(webhook_url, build_discord_message(c))

    # 5. seen-titles 갱신
    with open(seen_file, "a", encoding="utf-8") as f:
        for c in selected:
            f.write(c["title"] + "\n")
    log.info(
        "[%s] seen-titles 갱신: %d건 추가",
        topic_name,
        len(selected),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
