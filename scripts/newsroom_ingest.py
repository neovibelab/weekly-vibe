#!/usr/bin/env python3
"""뉴스룸 수집기 — allowlist 기업 뉴스룸/블로그의 RSS·Atom 피드에서 최신 글을 가져와
분류 후 Supabase radar_items(collector='newsroom')에 적재. GitHub Actions 일일 cron.

런타임 의존: requests, anthropic (피드 파싱은 stdlib xml.etree — 외부 feedparser 불필요).
환경변수:
  SUPABASE_URL / SUPABASE_KEY
  ANTHROPIC_API_KEY          분류용(없으면 토픽·요약 없이 적재)
  NEWSROOM_LOOKBACK_DAYS     기본 7 (뉴스룸은 매일 발행 아님 — 저빈도 소스 포착 위해 넉넉히. URL dedup이라 겹쳐도 안전)
사용: python scripts/newsroom_ingest.py [--dry-run]
"""
from __future__ import annotations

import datetime
import html
import json
import logging
import os
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWLIST_PATH = os.path.join(os.path.dirname(HERE), "sources_newsrooms.json")
TOPIC_KEYS = [
    "fan-behavior", "consumer-behavior", "ent-deals", "ip-business",
    "artist-ownership", "tech-issues", "taste-values",  # 구 gen-z-lifestyle (2026-06-17 재정의)
]
LOOKBACK_DAYS = int(os.environ.get("NEWSROOM_LOOKBACK_DAYS", "7"))
FETCH_CAP = 8  # 피드당 최대 처리 건수
DISCORD_CAP = 8  # discord 전송 webhook당 최대 (도배 방지)
# discord 전송 토픽 필터 — 엔터·문화·소비 핵심만 전송(순수 tech-issues·ent-deals 거시는 컷).
# 36氪 happy_life(생활소비) 성격 근사 — 신약·항공·로봇 등 엔터·소비와 먼 글 차단.
DISCORD_TOPICS = {"fan-behavior", "consumer-behavior", "taste-values", "ip-business", "artist-ownership"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ── 피드 fetch / 파싱 (RSS·Atom 공통) ───────────────────────────────────────────

def fetch_feed(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        })
        r.raise_for_status()
        return r.content
    except Exception as e:
        log.warning("피드 fetch 실패 %s: %s", url, e)
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(data: bytes) -> list[dict]:
    """RSS(item)·Atom(entry) 공통 파서 → {title, link, date, summary} 리스트."""
    try:
        root = ET.fromstring(data)
    except Exception as e:
        log.warning("피드 파싱 실패: %s", e)
        return []
    out: list[dict] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        d: dict = {}
        for ch in el:
            ln = _local(ch.tag)
            txt = (ch.text or "").strip()
            if ln == "title" and txt:
                d["title"] = txt
            elif ln == "link":
                href = ch.get("href")
                if href:  # Atom <link href= rel=>
                    if ch.get("rel", "alternate") == "alternate" or "link" not in d:
                        d["link"] = href
                elif txt:  # RSS <link>text</link>
                    d["link"] = txt
            elif ln in ("pubdate", "published", "updated", "date") and txt:
                d.setdefault("date", txt)
            elif ln in ("description", "summary", "content") and txt:
                d.setdefault("summary", txt)
        if d.get("title") and d.get("link"):
            out.append(d)
    return out


def parse_date(s: str):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)  # RSS RFC822
    except Exception:
        pass
    try:
        return datetime.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))  # Atom ISO8601
    except Exception:
        return None


def html_to_text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


# ── 분류 (Claude haiku) ───────────────────────────────────────────────────────

def classify(title: str, text: str, region_hint: str) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    fallback = {"topics": [], "summary_ko": "", "is_promo": False}
    if not key:
        return fallback
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        prompt = (
            "엔터·문화·소비 산업 기업 뉴스룸/블로그 글을 분류해 JSON으로만 응답.\n\n"
            f"제목: {title}\n본문 발췌: {text[:1500]}\n\n"
            "topics: 해당되는 것만 (배열 0~3개) — " + ", ".join(TOPIC_KEYS) + "\n"
            "is_promo: 단순 홍보면 true, 산업 신호면 false. "
            "true=신작·시즌 공개, 예고편·트레일러, 출시일/공개일 안내, 자사 콘텐츠·작품 마케팅, 수상 자축 등 보도자료성 홍보. "
            "false=사업 전략·투자·M&A·실적·구독자/이용 데이터·기술·정책·인사·파트너십 등 산업 신호. "
            "애매하면 false(보존 우선).\n"
            "summary_ko: 한국어 150자 이내 핵심 요약 (무엇을 다뤘는지)\n\n"
            '{"topics": [...], "is_promo": false, "summary_ko": "..."}'
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        if isinstance(data, list):  # 모델이 배열로 응답하는 엣지
            data = next((x for x in data if isinstance(x, dict)), {})
        topics = [t for t in (data.get("topics") or []) if t in TOPIC_KEYS]
        return {
            "topics": topics,
            "summary_ko": (data.get("summary_ko") or "").strip(),
            "is_promo": bool(data.get("is_promo", False)),
        }
    except Exception as e:
        log.warning("분류 실패: %s", e)
        return fallback


# ── Supabase ──────────────────────────────────────────────────────────────────

def supa_upsert(row: dict) -> int:
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"
    key = os.environ["SUPABASE_KEY"]
    h = {
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    r = requests.post(url, headers=h, json=row, timeout=15)
    return r.status_code


def recent_urls(days: int = 30) -> set[str]:
    try:
        url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"
        key = os.environ["SUPABASE_KEY"]
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
        r = requests.get(url, headers={"apikey": key, "Authorization": f"Bearer {key}"},
                         params={"select": "url", "collector": "eq.newsroom",
                                 "created_at": f"gte.{cutoff}"}, timeout=15)
        r.raise_for_status()
        return {x["url"] for x in r.json() if x.get("url")}
    except Exception:
        return set()


def send_to_discord(webhook_url: str, content: str) -> int:
    try:
        r = requests.post(webhook_url, json={"content": content[:1900]}, timeout=10)
        return r.status_code
    except Exception as e:
        log.warning("디스코드 전송 실패: %s", e)
        return 0


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> int:
    dry = "--dry-run" in sys.argv
    with open(ALLOWLIST_PATH, encoding="utf-8") as f:
        sources = json.load(f)["sources"]

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=LOOKBACK_DAYS)
    seen = recent_urls() if not dry else set()
    rows = []
    discord_queue = []  # (webhook_env, name, title, url, pub) — discord 지정 소스의 signal만

    for src in sources:
        if src.get("feed", "").startswith("_"):
            continue
        data = fetch_feed(src["feed"])
        if not data:
            continue
        kept = 0
        for it in parse_feed(data):
            if kept >= FETCH_CAP:
                break
            url = it["link"].strip()
            if not url or url in seen:
                continue
            dt = parse_date(it.get("date", ""))
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                if dt < cutoff:
                    continue  # 룩백 밖(오래된 글)
                pub = dt.astimezone(datetime.timezone.utc).isoformat()
            else:
                pub = datetime.datetime.now(datetime.timezone.utc).isoformat()
            title = it["title"][:500]
            summary_raw = html_to_text(it.get("summary", ""))[:2000]
            cls = classify(title, summary_raw, src.get("region", "global-en"))
            rows.append({
                "id": str(uuid.uuid4()),
                "title": title,
                "url": url,
                "source": src["name"],
                "category": "newsroom",
                "collector": "newsroom",
                "summary": (cls["summary_ko"] or summary_raw[:200]),
                "topics": cls["topics"],
                "tags": cls["topics"],
                "region": src.get("region", "global-en"),
                "published_date": pub,
                # promo는 status=filtered_out → 대시보드 기본 뷰에서 숨김(app.py neq.filtered_out
                # · dashboard.html inPool). filter_verdict=promo로 사유 기록, ?status=filtered_out로 토글.
                "status": "filtered_out" if cls.get("is_promo") else "pending",
                "filter_verdict": "promo" if cls.get("is_promo") else "pass",
                "total_score": 0,
            })
            seen.add(url)
            kept += 1
            log.info("[%s] %s%s | %s", src["name"], "·".join(cls["topics"]) or "-",
                     " [PROMO]" if cls.get("is_promo") else "", title[:55])
            if (src.get("discord") and not cls.get("is_promo")
                    and set(cls.get("topics", [])) & DISCORD_TOPICS):
                discord_queue.append((src["discord"], src["name"], title, url, pub))

    log.info("수집 %d건 (소스 %d개, 최근 %d일)", len(rows), len(sources), LOOKBACK_DAYS)
    if dry:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        print(f"\n[디스코드 전송 후보(signal)] {len(discord_queue)}건:")
        for env, name, title, url, pub in discord_queue[:12]:
            print(f"  -> {env} | [{name}] {title[:42]}")
        return 0
    saved = 0
    for row in rows:
        code = supa_upsert(row)
        if code in (200, 201, 204):
            saved += 1
        else:
            log.warning("Supabase 적재 실패 HTTP %d: %s", code, row["title"][:40])
    log.info("Supabase 적재 완료: %d/%d", saved, len(rows))

    # discord 전송 — discord 지정 소스의 signal만, webhook당 최신 DISCORD_CAP건(도배 방지).
    # promo·기존 적재분(seen)은 자연 제외. #vibe-china 등 지역 채널 최신성 보강.
    if discord_queue:
        from collections import defaultdict
        by_hook = defaultdict(list)
        for env, name, title, url, pub in discord_queue:
            by_hook[env].append((name, title, url, pub))
        for env, posts in by_hook.items():
            webhook = os.environ.get(env)
            if not webhook:
                log.warning("%s 미설정 — discord 전송 생략", env)
                continue
            posts.sort(key=lambda x: x[3], reverse=True)  # 최신순
            sent = 0
            for name, title, url, _ in posts[:DISCORD_CAP]:
                if send_to_discord(webhook, f"📰 **[{name}]** {title}\n{url}") in (200, 204):
                    sent += 1
            log.info("discord 전송: %s %d/%d건", env, sent, min(len(posts), DISCORD_CAP))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
