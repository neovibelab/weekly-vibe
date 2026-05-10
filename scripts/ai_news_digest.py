"""
AI Music & Entertainment Daily Digest
--------------------------------------
매일 2회 실행:
  AM (09:00 KST / UTC 00:00): 한국 신뢰 매체 — AI 음악·자본·엔터·라이프스타일
  PM (15:00 KST / UTC 06:00): 영문 신뢰 매체 — AI 음악·자본·엔터·라이프스타일

UTC 시각으로 AM/PM 세션을 자동 판별한다.

환경변수:
  ANTHROPIC_API_KEY        Claude API 키
  DISCORD_AI_TALK_WEBHOOK  Discord 웹훅 URL
"""

import os
import re
import html
import json
import logging
import datetime
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

# ──────────────────────────────────────────────
# AM: 한국 신뢰 매체 (09:00 KST)
# AI 엔터·비즈니스·AX(조직문화) 전반. 음악보다 넓은 범위.
# ──────────────────────────────────────────────
AM_SOURCES = [
    ("한국경제",  "https://news.google.com/rss/search?q=AI+엔터테인먼트+비즈니스+when:2d+site:hankyung.com&hl=ko&gl=KR&ceid=KR:ko"),
    ("매일경제",  "https://news.google.com/rss/search?q=AI+비즈니스+콘텐츠+산업+when:2d+site:mk.co.kr&hl=ko&gl=KR&ceid=KR:ko"),
    ("전자신문",  "https://news.google.com/rss/search?q=AI+기업+혁신+AX+도입+when:2d+site:etnews.com&hl=ko&gl=KR&ceid=KR:ko"),
    ("ZDNet KR", "https://news.google.com/rss/search?q=AI+비즈니스+조직+AX+when:2d+site:zdnet.co.kr&hl=ko&gl=KR&ceid=KR:ko"),
    ("연합뉴스",  "https://news.google.com/rss/search?q=AI+엔터+비즈니스+산업+when:2d+site:yna.co.kr&hl=ko&gl=KR&ceid=KR:ko"),
]

# ──────────────────────────────────────────────
# PM: 영문 신뢰 매체 (15:00 KST)
# MBW·TechCrunch는 직접 RSS (AI 비즈니스 특화, 광범위 음악 뉴스 적음).
# Variety·Billboard 등은 직접 RSS 대신 AI 타겟 쿼리 사용 (초상권 등 무관 기사 차단).
# ──────────────────────────────────────────────
PM_SOURCES = [
    ("Music Business Worldwide", "https://www.musicbusinessworldwide.com/feed/"),
    ("TechCrunch Music",         "https://techcrunch.com/tag/music/feed/"),
    ("Variety — AI",             "https://news.google.com/rss/search?q=AI+music+generative+royalty+when:2d+site:variety.com&hl=en-US&gl=US&ceid=US:en"),
    ("Billboard — AI",           "https://news.google.com/rss/search?q=AI+music+label+streaming+copyright+when:2d+site:billboard.com&hl=en-US&gl=US&ceid=US:en"),
    ("The Verge — AI Music",     "https://news.google.com/rss/search?q=AI+music+entertainment+when:2d+site:theverge.com&hl=en-US&gl=US&ceid=US:en"),
    ("Reuters — AI Music",       "https://news.google.com/rss/search?q=AI+music+entertainment+industry+when:2d+site:reuters.com&hl=en-US&gl=US&ceid=US:en"),
]

# 48시간 이내 기사만 수집
HOURS_WINDOW = 48

# 관련성 점수 컷오프 (0~10)
# 4 이상 — AI가 부수적 언급에 그치는 기사 차단하되 너무 엄격하지 않게
RELEVANCE_CUTOFF = 4

# 최대 선택 기사 수 (1건)
MAX_ARTICLES = 1

# 제목 유사도 임계값
DUPLICATE_THRESHOLD = 0.80

SUMMARY_SYSTEM_PROMPT = (
    "당신은 한국 엔터테인먼트 업계 전문가입니다.\n"
    "AI와 음악·엔터테인먼트 산업의 교차점에서 발생하는 뉴스를 "
    "한국 레이블·플랫폼·아티스트 관점에서 두 문단으로 작성합니다.\n"
    "첫 번째 문단은 기사의 핵심 사실과 맥락을 4~5문장으로 서술합니다.\n"
    "두 번째 문단은 이 뉴스가 한국 엔터테인먼트 업계에 미치는 의미와 시사점을 4~5문장으로 분석합니다.\n"
    "문단 레이블이나 제목은 쓰지 않습니다. 자연스러운 산문으로 작성합니다.\n"
    "영문 원문이 입력되더라도 반드시 한국어로 작성합니다.\n"
    "사실 중심으로, 과장 없이 작성합니다."
)

# AM용 — AI 엔터·비즈니스·AX 전반
AM_SCORE_PROMPT_PREFIX = (
    "아래 기사 목록을 보고 각 기사의 관련성 점수를 JSON 배열로 반환하라.\n"
    "주제: AI × 엔터테인먼트·비즈니스·조직문화(AX). 카테고리: 음악 / 자본 / 엔터테인먼트 / 라이프스타일.\n"
    "관련성 기준 (중요도 순):\n"
    "1. AI × 엔터테인먼트 (음악·영상·게임·팬덤·플랫폼에 AI 적용)\n"
    "2. AI × 비즈니스 (기업 AI 도입, 수익 모델, 투자·M&A)\n"
    "3. AI × 조직문화·AX (기업 AI 전환, 직무 변화, 조직 혁신)\n"
    "4. AI × 라이프스타일 (소비자 행동, 스트리밍, 추천)\n"
    "한국 엔터테인먼트 업계 종사자에게 실질적으로 유용한 정보 우선.\n"
    "score는 0(무관)~10(매우 관련). id는 기사 번호.\n"
    "출력 형식: JSON 배열만, 설명 없이.\n\n"
    "기사 목록:\n"
)

# PM용 — AI × 음악·엔터 특화
PM_SCORE_PROMPT_PREFIX = (
    "아래 기사 목록을 보고 각 기사의 관련성 점수를 JSON 배열로 반환하라.\n"
    "주제: AI × 음악·엔터테인먼트. 카테고리: 음악 / 자본 / 엔터테인먼트 / 라이프스타일.\n"
    "핵심 원칙: AI가 기사의 주요 주제여야 한다. AI가 단순 언급·배경으로만 등장하면 0~2점.\n"
    "관련성 기준 (중요도 순):\n"
    "1. AI가 음악 산업에 미치는 영향 (생성형 AI, 저작권, 로열티, 레이블 전략)\n"
    "2. 엔터테인먼트 × AI 자본 동향 (투자·M&A·펀딩·플랫폼 비즈니스)\n"
    "3. AI × 엔터테인먼트 (영상·게임·팬덤·아이돌 AI 활용)\n"
    "4. AI × 라이프스타일 (소비자 행동 변화, 스트리밍, 추천 알고리즘)\n"
    "예시 — 낮은 점수(1~3): 초상권 소송, 아티스트 계약 분쟁 등 AI와 무관한 엔터 뉴스.\n"
    "예시 — 높은 점수(7~10): AI 음악 생성 플랫폼 출시, AI 저작권 판례, 레이블 AI 전략 발표.\n"
    "score는 0(무관)~10(매우 관련). id는 기사 번호.\n"
    "출력 형식: JSON 배열만, 설명 없이.\n\n"
    "기사 목록:\n"
)

def _get_score_prompt_prefix() -> str:
    return AM_SCORE_PROMPT_PREFIX if _get_session() == "AM" else PM_SCORE_PROMPT_PREFIX

BATCH_SIZE = 20

# PM 키워드 프리필터 (음악·엔터 특화)
_PM_KEYWORDS = [
    "AI", "artificial intelligence", "generative", "machine learning",
    "인공지능", "AI 음악", "생성형",
    "Suno", "Udio", "Soundraw", "AIVA", "Boomy",
    "copyright", "royalty", "licensing", "deepfake",
    "algorithm", "streaming", "recommendation",
]

# AM 키워드 프리필터 (AI 전반 — 엔터·비즈니스·AX)
_AM_KEYWORDS = [
    "AI", "인공지능", "생성형", "generative", "machine learning",
    "ChatGPT", "GPT", "LLM", "클로드", "Gemini",
    "AX", "AI 전환", "AI 도입", "AI 혁신",
    "엔터", "콘텐츠", "플랫폼", "비즈니스", "산업",
]


# ──────────────────────────────────────────────
# 세션 판별
# ──────────────────────────────────────────────

def _get_session() -> str:
    override = os.environ.get("SESSION", "").upper()
    if override in ("AM", "PM"):
        return override
    return "AM" if datetime.datetime.utcnow().hour < 6 else "PM"

def _get_sources() -> list[tuple[str, str]]:
    return AM_SOURCES if _get_session() == "AM" else PM_SOURCES

def _get_header(date: str) -> str:
    if _get_session() == "AM":
        return f"🇰🇷 **AI Music 오전 | {date}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    return f"🎵 **AI Music 오후 | {date}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

DISCORD_ARTICLE_TEMPLATE = "**{headline}**\n*{source}*\n\n{summary}\n\n🔗 [원문 보기]({url})"


# ──────────────────────────────────────────────
# RSS 수집
# ──────────────────────────────────────────────

def _parse_entry_time(entry) -> datetime.datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc)
            except Exception:
                pass
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
    raw_title = entry.get("title", "").strip()
    real_source = getattr(getattr(entry, "source", None), "title", None)
    if not real_source and " - " in raw_title:
        parts = raw_title.rsplit(" - ", 1)
        if len(parts) == 2 and len(parts[1]) < 60:
            real_source = parts[1].strip()
            raw_title = parts[0].strip()
    return real_source or fallback, raw_title


def fetch_articles() -> list[dict]:
    session = _get_session()
    sources = _get_sources()
    log.info("세션: %s (%d개 소스)", session, len(sources))
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS_WINDOW)
    articles = []

    for source_name, url in sources:
        try:
            feed = feedparser.parse(url)
            count = 0
            for entry in feed.entries:
                pub_time = _parse_entry_time(entry)
                if pub_time is None or pub_time < cutoff:
                    continue
                real_source, clean_title = _extract_real_source(entry, source_name)
                raw_body = entry.get("summary", "") or ""
                clean_body = re.sub(r"<[^>]+>", " ", raw_body)
                clean_body = html.unescape(clean_body)
                clean_body = re.sub(r"\s+", " ", clean_body).strip()
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

def _keyword_prefilter(articles: list[dict]) -> list[dict]:
    keywords = _AM_KEYWORDS if _get_session() == "AM" else _PM_KEYWORDS
    filtered = []
    for a in articles:
        text = (a["title"] + " " + a["body"]).lower()
        if any(kw.lower() in text for kw in keywords):
            filtered.append(a)
    log.info("키워드 프리필터 (%s): %d건 → %d건", _get_session(), len(articles), len(filtered))
    return filtered


def score_articles(client: Anthropic, articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    articles = _keyword_prefilter(articles)
    if not articles:
        log.info("프리필터 통과 기사 없음")
        return []

    score_map: dict[int, float] = {}
    for batch_start in range(0, len(articles), BATCH_SIZE):
        batch_items = articles[batch_start:batch_start + BATCH_SIZE]
        batch = [
            {"id": batch_start + i, "title": a["title"], "body": a["body"][:300]}
            for i, a in enumerate(batch_items)
        ]
        prompt = _get_score_prompt_prefix() + json.dumps(batch, ensure_ascii=False)
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
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        resp.raise_for_status()
        final_url = resp.url
        log.info("fetch 최종 URL: %s…", final_url[:80])

        if "news.google.com" in final_url:
            _EXCLUDE = r"(?:news\.google|lh\d+\.googleusercontent|googleapis|gstatic|google\.com)"
            match = re.search(
                rf'href="(https?://(?!{_EXCLUDE})[a-zA-Z0-9][^"{{}}\\s]{{10,}})"',
                resp.text
            )
            if match:
                real_url = match.group(1)
                log.info("실제 기사 URL 재시도: %s…", real_url[:80])
                resp = requests.get(real_url, headers=headers, timeout=10, allow_redirects=True)
                resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            log.warning("비-HTML 응답 (%s) — 스킵", content_type[:50])
            return ""

        extractor = _TextExtractor()
        extractor.feed(resp.text)
        text = " ".join(extractor.texts)
        text = html.unescape(text)
        log.info("fetch 본문 추출: %d자", len(text))
        return text[:max_chars]
    except Exception as exc:
        log.warning("기사 본문 fetch 실패 (%s…): %s", url[:50], exc)
        return ""


# ──────────────────────────────────────────────
# Claude API — 한국어 요약
# ──────────────────────────────────────────────

def summarize_article(client: Anthropic, article: dict) -> str:
    body = article["body"]
    log.info("요약 시작 — 본문 %d자: %s…", len(body), body[:60])
    if len(body) < 150 or body.strip() == article["title"].strip():
        log.info("본문 부족 — URL fetch 시도: %s…", article["url"][:60])
        fetched = _fetch_article_body(article["url"])
        if fetched:
            log.info("fetch 성공 — %d자 획득", len(fetched))
            body = fetched

    has_body = len(body) > len(article["title"]) + 20
    if has_body:
        prompt = (
            f"다음 기사를 두 문단으로 작성하라. 문단 레이블이나 제목은 쓰지 말고 자연스러운 산문으로 작성한다.\n"
            f"첫 번째 문단: 기사의 핵심 사실과 맥락을 4~5문장으로 서술.\n"
            f"두 번째 문단: 이 뉴스가 한국 레이블·플랫폼·아티스트에게 미치는 의미와 시사점을 4~5문장으로 분석.\n\n"
            f"제목: {article['title']}\n"
            f"출처: {article['source']}\n"
            f"내용: {body[:1200]}"
        )
    else:
        prompt = (
            f"다음 기사 제목을 바탕으로 두 문단을 작성하라. 문단 레이블이나 제목은 쓰지 말고 자연스러운 산문으로 작성한다.\n"
            f"첫 번째 문단: 제목으로 유추할 수 있는 핵심 사실과 배경을 4~5문장으로 서술.\n"
            f"두 번째 문단: 한국 레이블·플랫폼·아티스트에게 미치는 의미와 시사점을 4~5문장으로 분석.\n"
            f"본문이 없더라도 반드시 두 문단을 모두 작성해야 한다. '요약할 수 없습니다' 같은 응답은 절대 금지.\n\n"
            f"제목: {article['title']}\n"
            f"출처: {article['source']}"
        )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=600,
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
    today = datetime.date.today().strftime("%Y-%m-%d")
    header = _get_header(today)
    blocks = [
        DISCORD_ARTICLE_TEMPLATE.format(
            headline=a["title"],
            source=a["source"],
            summary=a["summary"],
            url=a["url"],
        )
        for a in selected
    ]
    return header + "\n\n" + "\n\n".join(blocks)


def send_to_discord(webhook_url: str, content: str) -> None:
    payload = {"content": content, "flags": 4}  # SUPPRESS_EMBEDS
    response = requests.post(webhook_url, json=payload, timeout=15)
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

    articles = fetch_articles()
    if not articles:
        log.info("수집된 기사 없음 — 전송 생략")
        return

    articles = deduplicate(articles)
    articles = score_articles(client, articles)

    relevant = [a for a in articles if a["score"] >= RELEVANCE_CUTOFF]
    if not relevant:
        log.info("관련성 점수 %d 이상 기사 없음 — 전송 생략", RELEVANCE_CUTOFF)
        return

    # fetch 성공 기사 먼저, 실패 시 제목 기반 폴백
    selected = []
    fallback_candidates = []

    for candidate in relevant:
        body = candidate["body"]
        needs_fetch = len(body) < 150 or body.strip() == candidate["title"].strip()
        if needs_fetch:
            fetched = _fetch_article_body(candidate["url"])
            if fetched:
                candidate["body"] = fetched
            else:
                log.info("fetch 실패 — 폴백 대기: %s…", candidate["title"][:50])
                fallback_candidates.append(candidate)
                continue

        candidate["summary"] = summarize_article(client, candidate)
        selected.append(candidate)
        log.info("선택: [%.1f] %s (%s)", candidate["score"], candidate["title"], candidate["source"])
        if len(selected) >= MAX_ARTICLES:
            break

    if len(selected) < MAX_ARTICLES and fallback_candidates:
        for candidate in fallback_candidates[:MAX_ARTICLES - len(selected)]:
            candidate["summary"] = summarize_article(client, candidate)
            selected.append(candidate)
            log.info("폴백 선택: [%.1f] %s (%s)", candidate["score"], candidate["title"], candidate["source"])

    if not selected:
        log.info("요약 가능한 기사 없음 — 전송 생략")
        return

    content = build_discord_payload(selected)
    send_to_discord(webhook_url, content)


if __name__ == "__main__":
    main()
