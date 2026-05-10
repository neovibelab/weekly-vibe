"""
AI Music Daily Digest
---------------------
매일 00:00 UTC (09:00 KST) 실행.
AI 음악 산업 관련 뉴스 1~2건을 선별해 Discord #ai_talk 채널에 게시.

환경변수:
  ANTHROPIC_API_KEY        Claude API 키
  DISCORD_AI_TALK_WEBHOOK  Discord 웹훅 URL
"""

import os
import re
import json
import logging
import datetime
import urllib.request
from difflib import SequenceMatcher
from html.parser import HTMLParser

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
    ("Music Business Worldwide", "https://www.musicbusinessworldwide.com/feed/"),
    ("Variety", "https://variety.com/v/music/feed/"),
    ("TechCrunch", "https://techcrunch.com/tag/music/feed/"),
    ("Google News — AI Music", "https://news.google.com/rss/search?q=AI+music+label+streaming+when:2d&hl=en-US&gl=US&ceid=US:en"),
    ("Google News — Generative Music", "https://news.google.com/rss/search?q=generative+AI+music+industry+when:2d&hl=en-US&gl=US&ceid=US:en"),
    ("Google News — Music AI Copyright", "https://news.google.com/rss/search?q=music+AI+copyright+royalty+when:2d&hl=en-US&gl=US&ceid=US:en"),
]

# 48시간 이내 기사만 수집 (주말·휴일 공백 대응)
HOURS_WINDOW = 48

# 관련성 점수 컷오프 (0~10)
RELEVANCE_CUTOFF = 3

# 최대 선택 기사 수
MAX_ARTICLES = 2

# 제목 유사도 임계값 (이 이상이면 중복으로 간주)
DUPLICATE_THRESHOLD = 0.80

SUMMARY_SYSTEM_PROMPT = (
    "당신은 한국 엔터테인먼트 업계 전문가입니다.\n"
    "AI와 음악 산업의 교차점에서 발생하는 뉴스를 "
    "한국 레이블, 플랫폼, 아티스트 관점에서 해석해 "
    "3~4문장으로 요약합니다.\n"
    "사실 중심으로, 과장 없이 작성합니다."
)

DISCORD_HEADER_TEMPLATE = "🎵 **AI Music Daily | {date}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
DISCORD_ARTICLE_TEMPLATE = "**{num}. {headline}**\n*{source}*\n\n{summary}\n\n🔗 [원문 보기]({url})"
DISCORD_SEPARATOR = "\n─────────────────────────────────\n"


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


def _extract_real_source(entry, fallback: str) -> tuple[str, str]:
    """실제 출처명과 정제된 제목을 반환한다.

    Google News RSS는 제목 끝에 ' - 출처명'을 붙이고
    entry.source.title에 출처명을 제공한다.
    """
    raw_title = entry.get("title", "").strip()

    # 1순위: feedparser가 파싱한 entry.source.title
    real_source = getattr(getattr(entry, "source", None), "title", None)

    # 2순위: 제목 끝 ' - 출처명' 패턴
    if not real_source and " - " in raw_title:
        parts = raw_title.rsplit(" - ", 1)
        if len(parts) == 2 and len(parts[1]) < 60:
            real_source = parts[1].strip()
            raw_title = parts[0].strip()

    return real_source or fallback, raw_title


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
                real_source, clean_title = _extract_real_source(entry, source_name)
                raw_body = entry.get("summary", "") or ""
                clean_body = re.sub(r"<[^>]+>", " ", raw_body).strip()
                clean_body = re.sub(r"\s+", " ", clean_body)
                articles.append({
                    "source": real_source,
                    "title": clean_title,
                    "url": entry.get("link", ""),
                    "body": clean_body or clean_title,
                    "published": pub_time.isoformat(),
                })
                count += 1
            log.info("[%s] %d건 수집 (최근 %dh 이내)", source_name, count, HOURS_WINDOW)
        except Exception as exc:
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

_AI_KEYWORDS = [
    "AI", "artificial intelligence", "generative", "machine learning",
    "음악 AI", "AI 음악", "인공지능", "생성형",
    "Suno", "Udio", "Soundraw", "AIVA", "Boomy",
    "copyright", "royalty", "licensing", "deepfake",
]

def _keyword_prefilter(articles: list[dict]) -> list[dict]:
    """AI 관련 키워드가 제목 또는 본문에 포함된 기사만 통과."""
    filtered = []
    for a in articles:
        text = (a["title"] + " " + a["body"]).lower()
        if any(kw.lower() in text for kw in _AI_KEYWORDS):
            filtered.append(a)
    log.info("키워드 프리필터: %d건 → %d건", len(articles), len(filtered))
    return filtered


def score_articles(client: Anthropic, articles: list[dict]) -> list[dict]:
    """
    각 기사에 관련성 점수(0~10)를 부여한다.
    관련성 기준: AI가 음악 산업에 미치는 영향
    (저작권, 스트리밍, 레이블, 아티스트 도구, 규제 등)
    """
    if not articles:
        return []

    # 키워드 프리필터 — Claude에 넘기기 전 AI 무관 기사 제거
    articles = _keyword_prefilter(articles)
    if not articles:
        log.info("프리필터 통과 기사 없음")
        return []

    # 배치 크기 제한 (max_tokens 512로 감당 가능한 수)
    BATCH_SIZE = 20
    score_map: dict[int, float] = {}

    for batch_start in range(0, len(articles), BATCH_SIZE):
        batch_articles = articles[batch_start:batch_start + BATCH_SIZE]
        batch = [
            {"id": batch_start + i, "title": a["title"], "body": a["body"][:300]}
            for i, a in enumerate(batch_articles)
        ]
        prompt = (
            "아래 기사 목록을 보고 각 기사의 관련성 점수를 JSON 배열로 반환하라.\n"
            "관련성 기준:\n"
            "- AI가 음악 산업(저작권, 스트리밍, 레이블, 아티스트 도구, 규제)에 미치는 영향\n"
            "- 한국 엔터테인먼트 업계 종사자에게 실질적으로 유용한 정보\n"
            "점수: 0(무관) ~ 10(매우 관련)\n"
            "출력 형식 (JSON만, 설명 없이):\n"
            '[{"id": 0, "score": 7}, {"id": 1, "score": 2}, ...]\n\n'
            f"기사 목록:\n{json.dumps(batch, ensure_ascii=False)}"
        )
        try:
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            scores = json.loads(raw)
            for item in scores:
                score_map[item["id"]] = item["score"]
        except Exception as exc:
            log.warning("관련성 점수 파싱 실패 (batch %d~): %s", batch_start, exc)

    for i, article in enumerate(articles):
        article["score"] = score_map.get(i, 0)
        log.info("  점수 %.1f | %s", article["score"], article["title"][:60])

    articles.sort(key=lambda x: x["score"], reverse=True)
    return articles


# ──────────────────────────────────────────────
# 기사 본문 fetch
# ──────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """HTML에서 본문 텍스트만 추출."""
    def __init__(self):
        super().__init__()
        self._skip = 0
        self._skip_tags = {"script", "style", "nav", "header", "footer", "aside"}
        self.texts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._skip_tags and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            t = data.strip()
            if len(t) > 20:
                self.texts.append(t)


def _fetch_article_body(url: str, max_chars: int = 1500) -> str:
    """URL에서 기사 본문 텍스트를 추출한다. 실패 시 빈 문자열 반환."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NVLBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        extractor = _TextExtractor()
        extractor.feed(html)
        return " ".join(extractor.texts)[:max_chars]
    except Exception as exc:
        log.warning("기사 본문 fetch 실패 (%s…): %s", url[:50], exc)
        return ""


# ──────────────────────────────────────────────
# Claude API — 한국어 요약
# ──────────────────────────────────────────────

def summarize_article(client: Anthropic, article: dict) -> str:
    """선택된 기사를 한국어 3~4문장으로 요약한다."""
    body = article["body"]
    # RSS 본문이 제목과 같거나 너무 짧으면 URL에서 직접 fetch
    if len(body) < 150 or body.strip() == article["title"].strip():
        log.info("본문 부족 — URL fetch 시도: %s…", article["url"][:60])
        fetched = _fetch_article_body(article["url"])
        if fetched:
            body = fetched

    prompt = (
        f"다음 기사를 요약하라.\n\n"
        f"제목: {article['title']}\n"
        f"출처: {article['source']}\n"
        f"내용: {body[:1200]}"
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
    header = DISCORD_HEADER_TEMPLATE.format(date=today)

    blocks = []
    for i, article in enumerate(selected, 1):
        block = DISCORD_ARTICLE_TEMPLATE.format(
            num=i,
            headline=article["title"],
            source=article["source"],
            summary=article["summary"],
            url=article["url"],
        )
        blocks.append(block)

    return header + "\n\n" + DISCORD_SEPARATOR.join(blocks)


def send_to_discord(webhook_url: str, content: str) -> None:
    """Discord 웹훅으로 메시지 전송."""
    payload = {"content": content, "flags": 4}  # 4 = SUPPRESS_EMBEDS
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
    webhook_url = os.environ.get("DISCORD_AI_TALK_WEBHOOK")

    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
    if not webhook_url:
        raise EnvironmentError("DISCORD_AI_TALK_WEBHOOK 환경변수가 설정되지 않았습니다.")

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
