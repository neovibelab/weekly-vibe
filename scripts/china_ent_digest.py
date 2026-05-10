"""
China Ent Daily Digest
----------------------
매일 09:00 KST / 15:00 KST 2회 실행.
중국 엔터 뉴스 1건을 선별해 Discord China Ent 채널에 게시.

주요 주제: 중국의 한국 엔터 이슈 / 자본 투자 / 정책 규제 / Z세대 소비 트렌드

환경변수:
  ANTHROPIC_API_KEY          Claude API 키
  DISCORD_CHINA_ENT_WEBHOOK  Discord 웹훅 URL
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

RSS_SOURCES = [
    # 한국어 — 중국 한류·투자·규제
    ("Google News KR — 중국 한류", "https://news.google.com/rss/search?q=중국+한류+K팝+엔터테인먼트+when:2d&hl=ko&gl=KR&ceid=KR:ko"),
    ("Google News KR — 한한령 규제", "https://news.google.com/rss/search?q=한한령+중국+한국+콘텐츠+when:2d&hl=ko&gl=KR&ceid=KR:ko"),
    ("Google News KR — 중국 엔터 투자", "https://news.google.com/rss/search?q=중국+엔터+투자+자본+when:2d&hl=ko&gl=KR&ceid=KR:ko"),
    # 중국어 — 韩流·자본·Z세대
    ("Google News CN — 韩流韩剧", "https://news.google.com/rss/search?q=韩流+韩剧+韩国+娱乐+when:2d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("Google News CN — Z世代消费", "https://news.google.com/rss/search?q=Z世代+娱乐+消费+追星+when:2d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("Google News CN — 监管投资", "https://news.google.com/rss/search?q=娱乐+监管+政策+资本+投资+when:2d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    # 영어 — Hallyu·China policy
    ("Google News EN — China Hallyu", "https://news.google.com/rss/search?q=China+Korea+Hallyu+Kpop+entertainment+when:2d&hl=en-US&gl=US&ceid=US:en"),
    ("Google News EN — China GenZ", "https://news.google.com/rss/search?q=China+Gen+Z+entertainment+consumption+idol+when:2d&hl=en-US&gl=US&ceid=US:en"),
]

# 48시간 이내 기사만 수집
HOURS_WINDOW = 48

# 관련성 점수 컷오프 (0~10)
RELEVANCE_CUTOFF = 5

# 최대 선택 기사 수 (1건)
MAX_ARTICLES = 1

# 제목 유사도 임계값 (이 이상이면 중복으로 간주)
DUPLICATE_THRESHOLD = 0.80

SUMMARY_SYSTEM_PROMPT = (
    "당신은 한국 엔터테인먼트 업계 전문가입니다.\n"
    "중국의 한국 엔터 이슈, 자본 투자, 정책 규제, Z세대 소비 트렌드 뉴스를 "
    "한국 레이블·플랫폼·아티스트 관점에서 해석해 3~4문장으로 요약합니다.\n"
    "중국어·영어 원문이 입력되더라도 반드시 한국어로 요약합니다.\n"
    "사실 중심으로, 과장 없이 작성합니다."
)

DISCORD_HEADER_TEMPLATE = "🇨🇳 **China Ent Daily | {date}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
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
    """실제 출처명과 정제된 제목을 반환한다."""
    raw_title = entry.get("title", "").strip()

    real_source = getattr(getattr(entry, "source", None), "title", None)

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
                # HTML 태그 제거 + 엔티티 디코딩
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
    """각 기사에 관련성 점수(0~10)를 부여한다."""
    if not articles:
        return []

    batch = [
        {"id": i, "title": a["title"], "body": a["body"][:500]}
        for i, a in enumerate(articles)
    ]
    prompt = (
        "아래 기사 목록을 보고 각 기사의 관련성 점수를 JSON 배열로 반환하라.\n"
        "관련성 기준 (중요도 순):\n"
        "1. 중국에서의 한국 엔터 이슈 (K-팝·K-드라마·한류 관련 중국 동향, 한한령 변화)\n"
        "2. 중국 엔터산업 자본 투자 (M&A, 펀딩, 플랫폼 투자)\n"
        "3. 중국 엔터 정책·규제 (콘텐츠 심의, 플랫폼 규제, 저작권)\n"
        "4. 중국 Z세대 소비 트렌드 (팬덤 소비, 아이돌, 숏폼, 스트리밍)\n"
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
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        resp.raise_for_status()
        final_url = resp.url
        log.info("fetch 최종 URL: %s…", final_url[:80])

        # Google News 페이지에 머무른 경우 — 실제 기사 URL 재시도
        if "news.google.com" in final_url:
            import re as _re
            # CDN·이미지·Google 내부 URL 제외하고 실제 기사 링크만 탐색
            _EXCLUDE = r"(?:news\.google|lh\d+\.googleusercontent|googleapis|gstatic|google\.com)"
            match = _re.search(
                rf'href="(https?://(?!{_EXCLUDE})[a-zA-Z0-9][^"{{}}\\s]{{10,}})"',
                resp.text
            )
            if match:
                real_url = match.group(1)
                log.info("실제 기사 URL 재시도: %s…", real_url[:80])
                resp = requests.get(real_url, headers=headers, timeout=10, allow_redirects=True)
                resp.raise_for_status()

        # HTML이 아닌 응답(이미지 등)은 스킵
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
    """선택된 기사를 한국어 3~4문장으로 요약한다."""
    body = article["body"]
    log.info("요약 시작 — 본문 %d자: %s…", len(body), body[:60])
    if len(body) < 150 or body.strip() == article["title"].strip():
        log.info("본문 부족 — URL fetch 시도: %s…", article["url"][:60])
        fetched = _fetch_article_body(article["url"])
        if fetched:
            log.info("fetch 성공 — %d자 획득", len(fetched))
            body = fetched
        else:
            log.warning("fetch 실패 — 제목만으로 요약")

    has_body = len(body) > len(article["title"]) + 20
    if has_body:
        prompt = (
            f"다음 기사를 한국 엔터테인먼트 업계 관점에서 3~4문장으로 요약하라.\n\n"
            f"제목: {article['title']}\n"
            f"출처: {article['source']}\n"
            f"내용: {body[:1200]}"
        )
    else:
        prompt = (
            f"다음 기사 제목을 바탕으로 한국 엔터테인먼트 업계 종사자에게 유용한 맥락과 의미를 3~4문장으로 작성하라.\n"
            f"본문이 없더라도 반드시 내용을 작성해야 한다. '요약할 수 없습니다' 같은 응답은 절대 금지.\n\n"
            f"제목: {article['title']}\n"
            f"출처: {article['source']}"
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

    # 5. 후보 순서대로 시도 — fetch 실패 시 다음 기사로
    selected = []
    for candidate in relevant:
        body = candidate["body"]
        needs_fetch = len(body) < 150 or body.strip() == candidate["title"].strip()
        if needs_fetch:
            log.info("본문 부족 — URL fetch 시도: %s…", candidate["url"][:60])
            fetched = _fetch_article_body(candidate["url"])
            if not fetched:
                log.info("fetch 실패 — 다음 기사로 건너뜀: %s…", candidate["title"][:50])
                continue
            candidate["body"] = fetched

        candidate["summary"] = summarize_article(client, candidate)
        selected.append(candidate)
        log.info("선택: [%.1f] %s (%s)", candidate["score"], candidate["title"], candidate["source"])
        if len(selected) >= MAX_ARTICLES:
            break

    if not selected:
        log.info("요약 가능한 기사 없음 — 전송 생략")
        return

    # 6. Discord 전송
    content = build_discord_payload(selected)
    send_to_discord(webhook_url, content)


if __name__ == "__main__":
    main()
