"""
Z-Gen Lifestyle — Vibe Signal Collector
-----------------------------------------
도시·소비·서브컬처 소스에서 Z세대 Vibe 후보를 수집해 #auto-candidates로 전달.
소스: RSS(도시 매체·글로벌 스트리트·아시아 도시 피드) + IMAP(tmifmdj 구독 메일)
스코어링: Vibe & Signal 밀도 5지표

환경변수:
  ANTHROPIC_API_KEY                  Claude API 키
  DISCORD_AUTO_CANDIDATES_WEBHOOK    Discord #auto-candidates 웹훅
  GMAIL_USER                         tmifmdj@gmail.com
  GMAIL_APP_PASS                     Gmail 앱 비밀번호
"""

import os
import re
import html
import email
import imaplib
import json
import logging
import datetime
from difflib import SequenceMatcher
from email.header import decode_header
from html.parser import HTMLParser

import feedparser
import requests
from anthropic import Anthropic
from dateutil import parser as dateparser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Vibe RSS 소스 — 도시·소비·서브컬처·아시아 피드
# ──────────────────────────────────────────────

RSS_SOURCES = [
    # 글로벌 스트리트·라이프스타일
    ("Highsnobiety",        "https://www.highsnobiety.com/feed/"),
    ("Hypebeast",           "https://hypebeast.com/feed"),
    ("Dazed",               "https://www.dazeddigital.com/rss"),
    ("i-D",                 "https://i-d.co/feed/"),
    ("Business of Fashion", "https://www.businessoffashion.com/arc/outboundfeeds/rss/"),
    ("Cool Hunting",        "https://www.coolhunting.com/feed/"),
    ("Aftermath",           "https://aftermath.site/rss"),
    # 비서구권 도시·소비
    ("Rest of World",       "https://restofworld.org/feed/"),
    # 아시아 도시 (도시별 피드 선택 구독)
    ("Time Out Tokyo",      "https://www.timeout.com/tokyo/feed.rss"),
    ("Time Out Bangkok",    "https://www.timeout.com/bangkok/feed.rss"),
    ("Time Out Singapore",  "https://www.timeout.com/singapore/feed.rss"),
    ("Time Out Seoul",      "https://www.timeout.com/seoul/feed.rss"),
    ("Coconuts Bangkok",    "https://coconuts.co/bangkok/feed/"),
    ("Coconuts Jakarta",    "https://coconuts.co/jakarta/feed/"),
    ("Coconuts Manila",     "https://coconuts.co/manila/feed/"),
    ("NYLON Singapore",     "https://www.nylon.com.sg/feed/"),
    ("Metropolis Japan",    "https://metropolisjapan.com/feed/"),
    ("Hypebeast Japan",     "https://hypebeast.com/jp/feed"),
    ("Hypebeast Korea",     "https://www.hypebeast.kr/feed"),
    ("VnExpress Life",      "https://e.vnexpress.net/rss/life.rss"),
    # Reddit 도시·라이프스타일
    ("r/streetwear",        "https://www.reddit.com/r/streetwear/.rss"),
    ("r/seoullife",         "https://www.reddit.com/r/seoullife/.rss"),
    ("r/tokyo",             "https://www.reddit.com/r/Tokyo/.rss"),
    ("r/bangkok",           "https://www.reddit.com/r/bangkok/.rss"),
]

HOURS_WINDOW = 48
MAX_CANDIDATES = 5
INDICATOR_CUTOFF = 2
INDICATOR_HIGHLIGHT = 3
DUPLICATE_THRESHOLD = 0.80

# ──────────────────────────────────────────────
# 5지표 스코어링 프롬프트
# ──────────────────────────────────────────────

VIBE_SCORE_PROMPT = (
    "아래 신호(기사/콘텐츠) 목록을 보고 각각에 대해 Vibe & Signal 밀도 5지표를 채점하라.\n\n"
    "5지표 (각 0~2):\n"
    "① 언급빈도: 커뮤니티·SNS·전문 매체·평론 레이어에서 같은 신호가 반복 등장하는가\n"
    "   (0=단발 또는 첫 등장, 1=일부 레이어에서 언급, 2=여러 레이어에서 반복)\n"
    "② 도시분포: 특정 도시·지역을 명시하는 신호인가\n"
    "   (0=지리 없음, 1=단일 도시/지역 명시, 2=복수 도시·지역에 걸친 신호)\n"
    "③ 교차정체성: 팬덤·세대·서브컬처·라이프스타일 등 정체성 레이어가 몇 개 겹치는가\n"
    "   (0=없음, 1=하나의 정체성 레이어, 2=둘 이상 교차)\n"
    "④ 매개자다양성: 팬·평론가·아티스트·플랫폼·산업 관계자 중 몇 레이어가 함께 관여하는가\n"
    "   (0=없음, 1=하나의 매개자 레이어, 2=둘 이상)\n"
    "⑤ 지속기간: 단발 화제인가 누적·반복 관찰되는가\n"
    "   (0=단발 이벤트, 1=단기 트렌드, 2=누적 관찰 중인 흐름)\n\n"
    "출력: JSON 배열만. 각 항목: id, indicators(작동한 지표명 배열), count(개수).\n"
    "예: [{\"id\":0,\"indicators\":[\"도시분포\",\"교차정체성\"],\"count\":2}]\n\n"
    "신호 목록:\n"
)

SUMMARY_SYSTEM_PROMPT = (
    "당신은 도시·소비·Z세대 문화 Vibe 신호 분석가입니다.\n"
    "주어진 신호를 **한 문장(40자 이내)**으로 기술합니다.\n"
    "어떤 도시 결·소비 균열·교차정체성이 감지되는가를 한 줄로. 도시명 필수.\n"
    "한국어. 일반론 금지."
)

BATCH_SIZE = 15


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


def _city_from_source(source_name: str) -> str:
    mapping = {
        "Time Out Tokyo": "도쿄",
        "Time Out Bangkok": "방콕",
        "Time Out Singapore": "싱가포르",
        "Time Out Seoul": "서울",
        "Coconuts Bangkok": "방콕",
        "Coconuts Jakarta": "자카르타",
        "Coconuts Manila": "마닐라",
        "NYLON Singapore": "싱가포르",
        "Metropolis Japan": "도쿄",
        "Hypebeast Japan": "도쿄",
        "Hypebeast Korea": "서울",
        "VnExpress Life": "호치민/하노이",
        "r/seoullife": "서울",
        "r/tokyo": "도쿄",
        "r/bangkok": "방콕",
    }
    return mapping.get(source_name, "")


def fetch_rss_articles() -> list[dict]:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS_WINDOW)
    articles = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VibeBot/1.0)"}

    for source_name, url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url, request_headers=headers)
            count = 0
            city = _city_from_source(source_name)
            for entry in feed.entries:
                pub_time = _parse_entry_time(entry)
                if pub_time is None or pub_time < cutoff:
                    continue
                title = entry.get("title", "").strip()
                raw_body = entry.get("summary", "") or ""
                body = re.sub(r"<[^>]+>", " ", raw_body)
                body = html.unescape(re.sub(r"\s+", " ", body)).strip()
                # 도시 이름을 body 앞에 힌트로 추가 (스코어링 시 ②도시분포 판단 보조)
                if city and city not in title:
                    body = f"[{city}] {body}"
                articles.append({
                    "source": source_name,
                    "title": title,
                    "url": entry.get("link", ""),
                    "body": body or title,
                    "published": pub_time.isoformat(),
                    "channel": "vibe/z-lifestyle",
                    "city": city,
                })
                count += 1
            if count:
                log.info("[RSS] %s: %d건", source_name, count)
        except Exception as exc:
            log.warning("[RSS] %s 실패: %s", source_name, exc)

    log.info("RSS 전체 수집: %d건", len(articles))
    return articles


# ──────────────────────────────────────────────
# IMAP 수집
# ──────────────────────────────────────────────

def _decode_header_value(value: str) -> str:
    parts = decode_header(value)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def _extract_email_text(msg) -> str:
    text_plain = []
    text_html = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    text_plain.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
            elif ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    text_html.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            decoded = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            if msg.get_content_type() == "text/plain":
                text_plain.append(decoded)
            else:
                text_html.append(decoded)

    if text_plain:
        return " ".join(text_plain)[:800]
    raw = " ".join(text_html)
    cleaned = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(re.sub(r"\s+", " ", cleaned)).strip()[:800]


def fetch_email_articles() -> list[dict]:
    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_pass = os.environ.get("GMAIL_APP_PASS", "")
    if not gmail_user or not gmail_pass:
        log.info("GMAIL 환경변수 없음 — 이메일 수집 스킵")
        return []

    articles = []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_user, gmail_pass)
        mail.select("inbox")

        since_date = (datetime.date.today() - datetime.timedelta(days=2)).strftime("%d-%b-%Y")
        _, data = mail.search(None, "SINCE", since_date)
        msg_ids = data[0].split() if data[0] else []
        log.info("[IMAP] 대상 메일: %d건", len(msg_ids))

        for msg_id in msg_ids[-50:]:
            try:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                subject = _decode_header_value(msg.get("Subject", ""))
                sender = _decode_header_value(msg.get("From", ""))
                body = _extract_email_text(msg)
                if not subject or not body or len(body) < 50:
                    continue
                articles.append({
                    "source": f"📧 {sender[:60]}",
                    "title": subject[:200],
                    "url": "",
                    "body": body,
                    "published": "",
                    "channel": "vibe/z-lifestyle",
                    "city": "",
                })
            except Exception as e:
                log.warning("[IMAP] 메일 파싱 실패: %s", e)

        mail.logout()
        log.info("[IMAP] 수집 완료: %d건", len(articles))
    except Exception as exc:
        log.warning("[IMAP] 연결 실패: %s", exc)

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
    if len(articles) > len(unique):
        log.info("중복 제거: %d건 → %d건", len(articles), len(unique))
    return unique


# ──────────────────────────────────────────────
# 5지표 스코어링
# ──────────────────────────────────────────────

def score_vibe(client: Anthropic, articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    scored = []
    for batch_start in range(0, len(articles), BATCH_SIZE):
        batch_items = articles[batch_start:batch_start + BATCH_SIZE]
        batch = [
            {"id": i, "title": a["title"], "body": a["body"][:300]}
            for i, a in enumerate(batch_items)
        ]
        prompt = VIBE_SCORE_PROMPT + json.dumps(batch, ensure_ascii=False)
        try:
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```\w*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
            results = json.loads(raw)
            for item in results:
                idx = item["id"]
                if idx < len(batch_items):
                    a = batch_items[idx].copy()
                    a["indicators"] = item.get("indicators", [])
                    a["indicator_count"] = item.get("count", 0)
                    scored.append(a)
        except Exception as exc:
            log.warning("5지표 스코어링 실패 (batch %d~): %s", batch_start, exc)
            for a in batch_items:
                a["indicators"] = []
                a["indicator_count"] = 0
                scored.append(a)

    for a in scored:
        city_tag = f" [{a.get('city','')}]" if a.get("city") else ""
        log.info("  [%d지표] %s%s | %s",
                 a["indicator_count"],
                 "·".join(a["indicators"]),
                 city_tag,
                 a["title"][:60])

    scored.sort(key=lambda x: x["indicator_count"], reverse=True)
    return scored


# ──────────────────────────────────────────────
# 요약
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


def _fetch_article_body(url: str, max_chars: int = 1200) -> str:
    if not url:
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        resp.raise_for_status()
        extractor = _TextExtractor()
        extractor.feed(resp.text)
        text = html.unescape(" ".join(extractor.texts))
        return text[:max_chars]
    except Exception:
        return ""


def summarize_article(client: Anthropic, article: dict) -> str:
    body = article["body"]
    if len(body) < 150 and article.get("url"):
        fetched = _fetch_article_body(article["url"])
        if fetched:
            body = fetched

    city_hint = f"[도시: {article['city']}] " if article.get("city") else ""
    prompt = (
        f"제목: {article['title']}\n"
        f"출처: {article['source']} {city_hint}\n"
        f"본문: {body[:1000]}"
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        log.error("요약 생성 실패: %s", exc)
        return article["title"]


# ──────────────────────────────────────────────
# Discord #auto-candidates 카드
# ──────────────────────────────────────────────

def build_discord_messages(candidates: list[dict], header: str) -> list[str]:
    lines = [header, ""]
    for a in candidates:
        count = a["indicator_count"]
        if count < INDICATOR_CUTOFF:
            continue
        badge = "🔴" if count >= INDICATOR_HIGHLIGHT else "🟡"
        indicators = "·".join(a["indicators"][:3]) if a["indicators"] else "—"
        city = a.get("city", "")
        city_tag = f" `{city}`" if city else ""
        title = a["title"][:80]
        url = a.get("url", "")
        title_part = f"[**{title}**]({url})" if url else f"**{title}**"
        summary = (a.get("summary", "") or "").strip()[:80]
        lines.append(f"{badge}{city_tag} {title_part} `{indicators}`")
        if summary:
            lines.append(f"> {summary}")
        lines.append("")
    content = "\n".join(lines).strip()
    if len(content) <= len(header) + 5:
        return []
    return [content[:1900]]


def send_to_discord(webhook_url: str, content: str) -> None:
    payload = {"content": content[:2000], "flags": 4}
    response = requests.post(webhook_url, json=payload, timeout=15)
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord 웹훅 실패 (HTTP {response.status_code}): {response.text[:200]}")
    log.info("Discord 전송 완료 (HTTP %d)", response.status_code)


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    webhook_url = os.environ.get("DISCORD_AUTO_CANDIDATES_WEBHOOK")

    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
    if not webhook_url:
        raise EnvironmentError(
            "DISCORD_AUTO_CANDIDATES_WEBHOOK 환경변수가 설정되지 않았습니다.\n"
            "Discord에 #auto-candidates 채널과 웹훅을 생성하고 GitHub Secret에 등록하세요."
        )

    client = Anthropic(api_key=api_key)

    rss_articles = fetch_rss_articles()
    email_articles = fetch_email_articles()
    all_articles = rss_articles + email_articles

    if not all_articles:
        log.info("수집된 신호 없음 — 전송 생략")
        return

    articles = deduplicate(all_articles)
    articles = score_vibe(client, articles)

    candidates = [a for a in articles if a["indicator_count"] >= INDICATOR_CUTOFF]
    if not candidates:
        log.info("5지표 %d개 이상 신호 없음 — 전송 생략", INDICATOR_CUTOFF)
        return

    selected = candidates[:MAX_CANDIDATES]
    for a in selected:
        a["summary"] = summarize_article(client, a)
        log.info("선택: [%d지표] %s", a["indicator_count"], a["title"][:60])

    today = datetime.date.today().strftime("%Y-%m-%d")
    header = f"🌏 **Z-Gen & City Vibe | {today}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    messages = build_discord_messages(selected, header)
    if len(messages) <= 1:
        log.info("Discord 카드 빌드 결과 없음 — 전송 생략")
        return

    for msg in messages:
        send_to_discord(webhook_url, msg)


if __name__ == "__main__":
    main()
