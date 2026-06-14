#!/usr/bin/env python3
"""뉴스레터 수집기 — allowlist 발신자의 새 뉴스레터를 Gmail API(OAuth)로 가져와
분류 후 Supabase radar_items(collector='newsletter')에 적재. GitHub Actions 일일 cron.

런타임 의존: requests, anthropic (google 라이브러리 불필요 — OAuth 토큰 갱신·Gmail REST 직접 호출).
환경변수:
  GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN   (gmail.readonly OAuth)
  SUPABASE_URL / SUPABASE_KEY
  ANTHROPIC_API_KEY            (분류용; 없으면 토픽·요약 없이 적재)
  NL_LOOKBACK_DAYS             (기본 2 — 매일 실행 + URL upsert 중복제거라 겹쳐도 안전)
사용: python scripts/newsletter_ingest.py [--dry-run]
"""
from __future__ import annotations

import base64
import datetime
import html
import json
import logging
import os
import re
import sys
import uuid
from email.utils import parsedate_to_datetime

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWLIST_PATH = os.path.join(os.path.dirname(HERE), "sources_newsletters.json")
TOPIC_KEYS = [
    "fan-behavior", "consumer-behavior", "ent-deals", "ip-business",
    "artist-ownership", "tech-issues", "gen-z-lifestyle",
]
REGIONS = ["korea", "global-en", "china", "japan", "southeast-asia"]
LOOKBACK_DAYS = int(os.environ.get("NL_LOOKBACK_DAYS", "2"))
SKIP_LINK = ("unsubscribe", "stop-email", "mailto:", "/profile", "preferences",
             "list-manage.com/unsubscribe", "/cs", "수신거부")
ASSET_HOST = ("googleapis.com", "gstatic.com", "w3.org", "schema.org", "googletagmanager",
              "doubleclick", "google-analytics", "/wp-content/", "cdn-cgi", "stibee.com/v2/open")
ASSET_EXT = (".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ico")


# ── Gmail (OAuth refresh token → REST) ────────────────────────────────────────

def gmail_token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["GMAIL_CLIENT_ID"],
        "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
        "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def gmail_list(token: str, query: str, limit: int = 8) -> list[str]:
    r = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": query, "maxResults": limit}, timeout=20,
    )
    r.raise_for_status()
    return [m["id"] for m in r.json().get("messages", [])]


def gmail_get(token: str, mid: str) -> dict:
    r = requests.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
        headers={"Authorization": f"Bearer {token}"},
        params={"format": "full"}, timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * ((4 - len(data) % 4) % 4)).decode("utf-8", "replace")


def extract_bodies(payload: dict) -> tuple[str, str]:
    """MIME 트리를 순회해 (html, plaintext) 결합 반환."""
    htmls, texts = [], []

    def walk(p):
        mt = p.get("mimeType", "")
        data = (p.get("body") or {}).get("data")
        if data:
            try:
                dec = _b64(data)
                if mt == "text/html":
                    htmls.append(dec)
                elif mt == "text/plain":
                    texts.append(dec)
            except Exception:
                pass
        for sub in (p.get("parts") or []):
            walk(sub)

    walk(payload)
    return "\n".join(htmls), "\n".join(texts)


def html_to_text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def _final_url(u: str) -> str:
    """추적 리다이렉트면 경로 세그먼트(뒤→앞)의 base64에서 원본 URL을 디코드해 복원."""
    base = u.split("?")[0]
    for seg in reversed(base.rstrip("/").split("/")):
        for cand in (seg, seg[1:] if len(seg) > 1 else ""):  # 일부 추적은 1바이트 프리픽스
            if len(cand) >= 24 and re.fullmatch(r"[A-Za-z0-9_\-]+=*", cand):
                try:
                    dec = _b64(cand)
                    if dec.startswith("http"):
                        return dec.split("?")[0]  # 디코드 성공 → utm 정리
                except Exception:
                    pass
    return u  # 디코드 실패(추적 URL) → 쿼리 보존(리다이렉트에 필요)


def canonical_url(html_body: str, prefer_domain: str = "") -> str:
    """콘텐츠 링크 중 대표 URL. 자산(폰트·css·이미지)·추적·수신거부 제외, 발신 도메인 우선."""
    cands = []
    for m in re.finditer(r'href="(https?://[^"]+)"', html_body):
        u = m.group(1)
        lo = u.lower()
        if any(s in lo for s in SKIP_LINK) or any(a in lo for a in ASSET_HOST):
            continue
        if any(lo.split("?")[0].endswith(e) for e in ASSET_EXT):
            continue
        cands.append(_final_url(u))
    if not cands:
        return ""
    if prefer_domain:
        for c in cands:
            if prefer_domain in c:
                return c
    return cands[0]


# ── 분류 (Claude haiku) ───────────────────────────────────────────────────────

def classify(subject: str, text: str, region_hint: str) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    fallback = {"topics": [], "region": region_hint, "summary_ko": ""}
    if not key:
        return fallback
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        prompt = (
            "엔터·문화·소비 산업 뉴스레터 항목을 분류해 JSON으로만 응답.\n\n"
            f"제목: {subject}\n본문 발췌: {text[:1500]}\n\n"
            "topics: 해당되는 것만 (배열 0~3개) — " + ", ".join(TOPIC_KEYS) + "\n"
            "region: 하나 — korea, global-en, china, japan, southeast-asia. "
            f"이 뉴스레터의 기본 시장은 '{region_hint}'. 본문이 명백히 다른 단일 지역만 다룰 때만 바꾸고, 아니면 {region_hint} 유지.\n"
            "summary_ko: 한국어 150자 이내 핵심 요약 (무엇을 다뤘는지)\n\n"
            '{"topics": [...], "region": "...", "summary_ko": "..."}'
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
        topics = [t for t in (data.get("topics") or []) if t in TOPIC_KEYS]
        region = data.get("region") if data.get("region") in REGIONS else region_hint
        return {"topics": topics, "region": region, "summary_ko": (data.get("summary_ko") or "").strip()}
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


def recent_urls(days: int = 14) -> set[str]:
    """최근 적재된 newsletter URL (배치 내 중복 회피용; URL unique라 upsert도 방어)."""
    try:
        url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/radar_items"
        key = os.environ["SUPABASE_KEY"]
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
        r = requests.get(url, headers={"apikey": key, "Authorization": f"Bearer {key}"},
                         params={"select": "url", "collector": "eq.newsletter",
                                 "created_at": f"gte.{cutoff}"}, timeout=15)
        r.raise_for_status()
        return {x["url"] for x in r.json() if x.get("url")}
    except Exception:
        return set()


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> int:
    dry = "--dry-run" in sys.argv
    with open(ALLOWLIST_PATH, encoding="utf-8") as f:
        sources = json.load(f)["sources"]

    if not os.environ.get("GMAIL_REFRESH_TOKEN"):
        log.error("GMAIL_REFRESH_TOKEN 미설정 — OAuth 셋업 필요 (gmail_oauth_setup.py)")
        return 1

    token = gmail_token()
    seen = recent_urls()
    saved = 0
    rows = []

    for src in sources:
        if src.get("from", "").startswith("_"):
            continue
        try:
            ids = gmail_list(token, f'from:({src["from"]}) newer_than:{LOOKBACK_DAYS}d category:{{primary promotions social updates}}')
        except Exception as e:
            log.warning("[%s] 조회 실패: %s", src["name"], e)
            continue
        for mid in ids:
            try:
                msg = gmail_get(token, mid)
            except Exception as e:
                log.warning("[%s] %s 본문 실패: %s", src["name"], mid, e)
                continue
            subject = _header(msg, "Subject").strip()
            html_b, text_b = extract_bodies(msg["payload"])
            body_text = (text_b.strip() or html_to_text(html_b))[:2000]
            url = canonical_url(html_b) or f"https://mail.google.com/mail/u/0/#all/{mid}"
            if url in seen:
                continue
            try:
                pub = parsedate_to_datetime(_header(msg, "Date")).astimezone(datetime.timezone.utc).isoformat()
            except Exception:
                pub = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cls = classify(subject, body_text, src.get("region", "global-en"))
            row = {
                "id": str(uuid.uuid4()),
                "title": subject[:500],
                "url": url,
                "source": src["name"],
                "category": "newsletter",
                "collector": "newsletter",
                "summary": (cls["summary_ko"] or body_text[:200]),
                "topics": cls["topics"],
                "tags": cls["topics"],
                "region": src.get("region", "global-en"),
                "published_date": pub,
                "status": "pending",
                "filter_verdict": "pass",
                "total_score": 0,
            }
            rows.append(row)
            seen.add(url)
            log.info("[%s] %s | %s | %s", src["name"], cls["region"],
                     "·".join(cls["topics"]) or "-", subject[:50])

    log.info("수집 %d건 (allowlist %d발신자, 최근 %d일)", len(rows), len(sources), LOOKBACK_DAYS)
    if dry:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        code = supa_upsert(row)
        if code in (200, 201, 204):
            saved += 1
        else:
            log.warning("Supabase 적재 실패 HTTP %d: %s", code, row["title"][:40])
    log.info("Supabase 적재 완료: %d/%d", saved, len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
