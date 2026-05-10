"""
China Ent Daily Digest
----------------------
매일 00:30 UTC (09:30 KST) 실행.
중국 엔터테인먼트·테크·IP 뉴스 1~2건을 선별해 Discord China Ent 채널에 게시.

환경변수:
  ANTHROPIC_API_KEY          Claude API 키
  DISCORD_CHINA_ENT_WEBHOOK  Discord 웹훅 URL
"""

import os
import json
import logging
import datetime
from difflib import SequenceMatcher

import feedparser
import requests
from anthropic import Anthropic
from dateutil import parser as dateparser

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

RSS_SOURCES = [
    # 한국어
    ("Google News KR — 중국 엔터", "https://news.google.com/rss/search?q=중국+엔터테인먼트+음악&hl=ko&gl=KR&ceid=KR:ko"),
    ("Google News KR — 중국 IP", "https://news.google.com/rss/search?q=중국+IP+저작권+플랫폼&hl=ko&gl=KR&ceid=KR:ko"),
    # 중국어
    ("Google News CN — 娱乐科技", "https://news.google.com/rss/search?q=中国+娱乐+科技+音乐&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("Google News CN — IP版权", "https://news.google.com/rss/search?q=音乐+IP+版权+流媒体&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    # 영어
    ("Google News EN — China Entertainment", "https://news.google.com/rss/search?q=China+entertainment+music+streaming&hl=en-US&gl=US&ceid=US:en"),
    ("Google News EN — China IP Tech", "https://news.google.com/rss/search?q=China+IP+tech+entertainment+industry&hl=en-US&gl=US&ceid=US:en"),
]

# 36시간 이내 기사만 수집
HOURS_WINDOW = 36

# 관련성 점수 컷오프 (0~10)
RELEVANCE_CUTOFF = 5

# 최대 선택 기사 수
MAX_ARTICLES = 2

# 제목 유사도 임계값 (이 이상이면 중복으로 간주)
DUPLICATE_THRESHOLD = 0.80

SUMMARY_SYSTEM_PROMPT = (
    "당신은 한국 엔터테인먼트 업계 전문가입니다.\n"
    "중국 엔터테인먼트·테크·IP 뉴스를 "
    "한국 레이블, 플랫폼, 아티스트 관점에서 해석해 "
    "3~4문장으로 요약합니다.\n"
    "중국어·영어 원문이 입력되더라도 반드시 한국어로 요약합니다.\n"
    "사실 중심으로, 과장 없이 작성합니다."
)

DISCORD_MESSAGE_TEMPLATE = (
    "**{headline}**\n\n"
    "{summary}\n\n"
    "📎 {source} · [원문 읽기]({url})"
)

DISCORD_HEADER_TEMPLATE = "🇨🇳 **China Ent Daily** | {date}\n\n━━━━━━━━━━━━━━━━━━━━"


# ──────────────────────────────────────────────
# RSS 수집
# ──────────────────────────────────────────────

def _parse_entry_time(entry) -> datetime.datetime | None:
    """feedparser entry에서 발행 시각을 timezone-aware UTC datetime으로 반환."""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc)
            except Exception:
                pass
    # fallback: 문자열 파싱
    for attr in ("published", "updated"):
        s = getattr(entry, attr, None)
        if s:
            try:
                dt = dateparser.parse(s)
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt
            except Exception:
                pass
    return None


def fetch_articles() -> list[dict]:
    """모든 RSS 소스에서 최근 HOURS_WINDOW 시간 이내 기사를 수집한다."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS_WINDOW)
    articles = []

    for source_name, url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            count = 0
            for entry in feed.entries:
                pub_time = _parse_entry_time(entry)
                if pub_time is None or pub_time < cutoff:
                    continue
                articles.append({
                    "source": source_name,
                    "title": entry.get("title", "").strip(),
                    "url": entry.get("link", ""),
                    # summary가 없으면 title만으로 평가
                    "body": entry.get("summary", "") or entry.get("title", ""),
                    "published": pub_time.isoformat(),
                })
                count += 1
            log.info("[%s] %d건 수집 (최근 %dh 이내)", source_name, count, HOURS_WINDOW)
        except Exception as exc:
            # 소스 하나 실패해도 나머지 계속 처리
            log.warning("[%s] RSS 수집 실패: %s", source_name, exc)

    log.info("전체 수집 기사: %d건", len(articles))
    return articles


# ──────────────────────────────────────────────
# 중복 제거
# ──────────────────────────────────────────────

def deduplicate(articles: list[dict]) -> list[dict]:
    """제목 유사도 80% 이상인 기사는 하나만 남긴다."""
    unique = []
    for candidate in articles:
        title_c = candidate["title"].lower()
        is_dup = any(
            SequenceMatcher(None, title_c, kept["title"].lower()).ratio() >= DUPLICATE_THRESHOLD
            for kept in unique
        )
        if not is_dup:
            unique.append(candidate)
    removed = len(articles) - len(unique)
    if removed:
        log.info("중복 제거: %d건 → %d건", len(articles), len(unique))
    return unique


# ──────────────────────────────────────────────
# Claude API — 관련성 점수
# ──────────────────────────────────────────────

def score_articles(client: Anthropic, articles: list[dict]) -> list[dict]:
    """
    각 기사에 관련성 점수(0~10)를 부여한다.
    관련성 기준:
    - 중국 엔터테인먼트 산업 (음악·드라마·영화·팬덤·아이돌·플랫폼)
    - 중국 테크 기업의 콘텐츠·미디어 진출 (텐센트뮤직, 넷이즈, 바이트댄스 등)
    - 중국 IP 전략·저작권·한한령·한중 콘텐츠 협력
    - 한국 엔터테인먼트 업계 종사자에게 중국 시장 관점에서 유용한 정보
    """
    if not articles:
        return []

    # 배치 처리: 기사 목록을 JSON으로 넘겨 한 번에 점수 받기
    batch = [
        {"id": i, "title": a["title"], "body": a["body"][:500]}
        for i, a in enumerate(articles)
    ]
    prompt = (
        "아래 기사 목록을 보고 각 기사의 관련성 점수를 JSON 배열로 반환하라.\n"
        "관련성 기준:\n"
        "- 중국 엔터테인먼트 산업 (음악·드라마·영화·팬덤·아이돌·플랫폼)\n"
        "- 중국 테크 기업의 콘텐츠·미디어 진출 (텐센트뮤직, 넷이즈, 바이트댄스 등)\n"
        "- 중국 IP 전략·저작권·한한령·한중 콘텐츠 협력\n"
        "- 한국 엔터테인먼트 업계 종사자에게 중국 시장 관점에서 유용한 정보\n"
        "점수: 0(무관) ~ 10(매우 관련)\n"
        "출력 형식 (JSON만, 설명 없이):\n"
        '[{"id": 0, "score": 7}, {"id": 1, "score": 2}, ...]\n\n'
        f"기사 목록:\n{json.dumps(batch, ensure_ascii=False)}"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # JSON 블록 파싱
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        scores = json.loads(raw)
        score_map = {item["id"]: item["score"] for item in scores}
    except Exception as exc:
        log.warning("관련성 점수 파싱 실패: %s — 모든 기사 점수 0 처리", exc)
        score_map = {}

    for i, article in enumerate(articles):
        article["score"] = score_map.get(i, 0)

    articles.sort(key=lambda x: x["score"], reverse=True)
    return articles


# ──────────────────────────────────────────────
# Claude API — 한국어 요약
# ──────────────────────────────────────────────

def summarize_article(client: Anthropic, article: dict) -> str:
    """선택된 기사를 한국어 3~4문장으로 요약한다."""
    prompt = (
        f"다음 기사를 요약하라.\n\n"
        f"제목: {article['title']}\n"
        f"출처: {article['source']}\n"
        f"내용: {article['body'][:1000]}"
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        log.error("요약 생성 실패 (%s): %s", article["title"], exc)
        return "(요약 생성 실패)"


# ──────────────────────────────────────────────
# Discord 전송
# ──────────────────────────────────────────────

def build_discord_payload(selected: list[dict]) -> str:
    """Discord 메시지 본문 문자열 생성."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    parts = [DISCORD_HEADER_TEMPLATE.format(date=today)]

    for article in selected:
        block = DISCORD_MESSAGE_TEMPLATE.format(
            headline=article["title"],
            summary=article["summary"],
            source=article["source"],
            url=article["url"],
        )
        parts.append(block)

    # 기사 간 빈 줄 하나 추가
    return "\n\n".join(parts)


def send_to_discord(webhook_url: str, content: str) -> None:
    """Discord 웹훅으로 메시지 전송."""
    payload = {"content": content}
    response = requests.post(
        webhook_url,
        json=payload,
        timeout=15,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Discord 웹훅 실패 (HTTP {response.status_code}): {response.text[:200]}"
        )
    log.info("Discord 전송 완료 (HTTP %d)", response.status_code)


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    webhook_url = os.environ.get("DISCORD_CHINA_ENT_WEBHOOK")

    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
    if not webhook_url:
        raise EnvironmentError("DISCORD_CHINA_ENT_WEBHOOK 환경변수가 설정되지 않았습니다.")

    client = Anthropic(api_key=api_key)

    # 1. RSS 수집
    articles = fetch_articles()
    if not articles:
        log.info("수집된 기사 없음 — 전송 생략")
        return

    # 2. 중복 제거
    articles = deduplicate(articles)

    # 3. 관련성 점수 산출
    articles = score_articles(client, articles)

    # 4. 컷오프 적용 후 상위 1~2건 선택
    relevant = [a for a in articles if a["score"] >= RELEVANCE_CUTOFF]
    if not relevant:
        log.info("관련성 점수 %d 이상 기사 없음 — 전송 생략", RELEVANCE_CUTOFF)
        return

    selected = relevant[:MAX_ARTICLES]
    log.info("선택된 기사: %d건", len(selected))
    for a in selected:
        log.info("  [%.1f] %s (%s)", a["score"], a["title"], a["source"])

    # 5. 한국어 요약 생성
    for article in selected:
        article["summary"] = summarize_article(client, article)

    # 6. Discord 전송
    content = build_discord_payload(selected)
    send_to_discord(webhook_url, content)


if __name__ == "__main__":
    main()
