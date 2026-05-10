"""
China Ent Daily Digest
----------------------
매일 2회 실행:
  AM (09:00 KST / UTC 00:00): 한국 신뢰 매체 — 경제·테크·시장 관점의 중국 엔터 이슈
  PM (15:00 KST / UTC 06:00): 중국 현지 신뢰 매체 — 경제·테크·시장 관점의 중국 엔터 이슈

UTC 시각으로 AM/PM 세션을 자동 판별한다.

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

# ──────────────────────────────────────────────
# AM: 한국 신뢰 매체 (09:00 KST)
# 중국 엔터 × 케이팝·한국 엔터 교집합 집중.
# ──────────────────────────────────────────────
AM_SOURCES = [
    # 한중 엔터 교집합 — site: 없이 한국어 전체에서 수집 후 채점으로 필터
    ("Google News KR — 한중 케이팝", "https://news.google.com/rss/search?q=중국+케이팝+한류+엔터테인먼트+when:2d&hl=ko&gl=KR&ceid=KR:ko"),
    ("Google News KR — 한한령",      "https://news.google.com/rss/search?q=한한령+중국+한국+콘텐츠+엔터+when:2d&hl=ko&gl=KR&ceid=KR:ko"),
    ("Google News KR — 중국 K팝",    "https://news.google.com/rss/search?q=중국+K팝+아이돌+한국+when:2d&hl=ko&gl=KR&ceid=KR:ko"),
    # 신뢰 매체 site: 필터 (보조)
    ("연합뉴스",  "https://news.google.com/rss/search?q=중국+한류+케이팝+when:2d+site:yna.co.kr&hl=ko&gl=KR&ceid=KR:ko"),
    ("매일경제",  "https://news.google.com/rss/search?q=중국+한국+엔터+when:2d+site:mk.co.kr&hl=ko&gl=KR&ceid=KR:ko"),
]

# ──────────────────────────────────────────────
# PM: 중국 현지 + 홍콩 신뢰 매체 (15:00 KST)
# 재경·테크 전문지 직접 RSS. 본문 제공 가능한 소스 우선.
# ──────────────────────────────────────────────
PM_SOURCES = [
    # 영어 — 직접 RSS (본문 포함)
    ("SCMP",          "https://www.scmp.com/rss/91/feed"),           # China Business
    ("Reuters China", "https://feeds.reuters.com/reuters/CNtopNews"), # China Top News
    # 중국어 — 직접 RSS (본문 포함)
    ("36氪",          "https://36kr.com/feed"),
    ("虎嗅",          "https://www.huxiu.com/rss/0.xml"),
    # Google News — 중국어 (fallback, 본문 없어도 제목 기반 요약)
    ("界面新闻",      "https://news.google.com/rss/search?q=中国+娱乐+市场+资本+when:2d+site:jiemian.com&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("第一财经",      "https://news.google.com/rss/search?q=中国+娱乐+科技+市场+when:2d+site:yicai.com&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
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
    "중국 엔터테인먼트 시장의 경제·테크·시장 동향을 "
    "한국 레이블·플랫폼·아티스트 관점에서 해석해 3~4문장으로 요약합니다.\n"
    "중국어·영어 원문이 입력되더라도 반드시 한국어로 요약합니다.\n"
    "사실 중심으로, 과장 없이 작성합니다."
)

# AM/PM 세션 판별 (UTC 기준)
def _get_session() -> str:
    override = os.environ.get("SESSION", "").upper()
    if override in ("AM", "PM"):
        return override
    return "AM" if datetime.datetime.utcnow().hour < 6 else "PM"

def _get_sources() -> list[tuple[str, str]]:
    return AM_SOURCES if _get_session() == "AM" else PM_SOURCES

def _get_header(date: str) -> str:
    if _get_session() == "AM":
        return f"🇰🇷 **China Ent 오전 | {date}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    return f"🇨🇳 **China Ent 오후 | {date}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

DISCORD_ARTICLE_TEMPLATE = "**{headline}**\n*{source}*\n\n{summary}\n\n🔗 [원문 보기]({url})"


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
    """세션(AM/PM)에 맞는 소스에서 최근 HOURS_WINDOW 시간 이내 기사를 수집한다."""
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

# AM용 — 중국 엔터 × 케이팝·한국 엔터 교집합
AM_SCORE_PROMPT_PREFIX = (
    "아래 기사 목록을 보고 각 기사의 관련성 점수를 JSON 배열로 반환하라.\n"
    "주제: 중국 엔터테인먼트 × 케이팝·한국 엔터 교집합.\n"
    "관련성 기준 (중요도 순):\n"
    "1. 중국에서의 케이팝·한류 동향 (팬덤, 스트리밍, 공연, 콘텐츠 소비)\n"
    "2. 한한령·중국 한국 엔터 규제·정책 변화\n"
    "3. 중국 시장 진출 한국 아티스트·레이블·플랫폼 소식\n"
    "4. 중국 Z세대의 케이팝 소비 트렌드\n"
    "한국 엔터테인먼트 업계 종사자에게 실질적으로 유용한 정보 우선.\n"
    'score는 0(무관)~10(매우 관련). id는 기사 번호.\n'
    "출력 형식: JSON 배열만, 설명 없이.\n\n"
    "기사 목록:\n"
)

# PM용 — 중국 엔터 시장 경제·테크·시장 전반
PM_SCORE_PROMPT_PREFIX = (
    "아래 기사 목록을 보고 각 기사의 관련성 점수를 JSON 배열로 반환하라.\n"
    "주제: 중국 엔터테인먼트 시장·산업. 카테고리: 경제 / 테크 / 시장.\n"
    "관련성 기준 (중요도 순):\n"
    "1. 중국 엔터 시장 경제 이슈 (투자·M&A·수익·펀딩·플랫폼 비즈니스)\n"
    "2. 중국 엔터 테크 동향 (AI 음악·숏폼·스트리밍·플랫폼 기술)\n"
    "3. 중국 엔터 시장 구조 변화 (규제·정책·소비 트렌드·한류 동향)\n"
    "한국 엔터테인먼트 업계 종사자에게 실질적으로 유용한 정보 우선.\n"
    'score는 0(무관)~10(매우 관련). id는 기사 번호.\n'
    "출력 형식: JSON 배열만, 설명 없이.\n\n"
    "기사 목록:\n"
)

def _get_score_prompt_prefix() -> str:
    return AM_SCORE_PROMPT_PREFIX if _get_session() == "AM" else PM_SCORE_PROMPT_PREFIX

BATCH_SIZE = 20


def score_articles(client: Anthropic, articles: list[dict]) -> list[dict]:
    """각 기사에 관련성 점수(0~10)를 부여한다. 20건씩 배치 처리."""
    if not articles:
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
    header = _get_header(today)

    blocks = []
    for article in selected:
        block = DISCORD_ARTICLE_TEMPLATE.format(
            headline=article["title"],
            source=article["source"],
            summary=article["summary"],
            url=article["url"],
        )
        blocks.append(block)

    return header + "\n\n" + "\n\n".join(blocks)


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

    # 5. 후보 순서대로 시도 — fetch 실패 시 제목 기반 요약으로 폴백
    selected = []
    fetch_failed_candidates = []  # fetch 실패 기사는 별도 보관

    for candidate in relevant:
        body = candidate["body"]
        needs_fetch = len(body) < 150 or body.strip() == candidate["title"].strip()
        if needs_fetch:
            log.info("본문 부족 — URL fetch 시도: %s…", candidate["url"][:60])
            fetched = _fetch_article_body(candidate["url"])
            if fetched:
                candidate["body"] = fetched
            else:
                log.info("fetch 실패 — 제목 기반 폴백 대기: %s…", candidate["title"][:50])
                fetch_failed_candidates.append(candidate)
                continue  # fetch 성공 기사 먼저 소진

        candidate["summary"] = summarize_article(client, candidate)
        selected.append(candidate)
        log.info("선택: [%.1f] %s (%s)", candidate["score"], candidate["title"], candidate["source"])
        if len(selected) >= MAX_ARTICLES:
            break

    # fetch 성공 기사로 MAX_ARTICLES 미달 시 — 제목 기반 폴백
    if len(selected) < MAX_ARTICLES and fetch_failed_candidates:
        for candidate in fetch_failed_candidates[:MAX_ARTICLES - len(selected)]:
            candidate["summary"] = summarize_article(client, candidate)
            selected.append(candidate)
            log.info("폴백 선택: [%.1f] %s (%s)", candidate["score"], candidate["title"], candidate["source"])

    if not selected:
        log.info("요약 가능한 기사 없음 — 전송 생략")
        return

    # 6. Discord 전송
    content = build_discord_payload(selected)
    send_to_discord(webhook_url, content)


if __name__ == "__main__":
    main()
