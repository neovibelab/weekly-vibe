#!/usr/bin/env python3
"""뉴스레터 수집기 — allowlist 발신자의 새 뉴스레터를 Gmail(IMAP 앱비밀번호)로 가져와
분류 후 Supabase radar_items(collector='newsletter')에 적재. GitHub Actions 일일 cron.

런타임 의존: requests, anthropic (Gmail은 stdlib imaplib — OAuth/검증 불필요).
환경변수:
  GMAIL_USER / GMAIL_APP_PASS   Gmail 주소 + 앱비밀번호(IMAP)
  SUPABASE_URL / SUPABASE_KEY
  ANTHROPIC_API_KEY             분류용(없으면 토픽·요약 없이 적재)
  NL_LOOKBACK_DAYS              기본 2 (매일 실행 + URL upsert 중복제거라 겹쳐도 안전)
사용: python scripts/newsletter_ingest.py [--dry-run]
"""
from __future__ import annotations

import base64
import datetime
import email
import html
import imaplib
import json
import logging
import os
import re
import sys
import uuid
from email.header import decode_header
from email.utils import parsedate_to_datetime
from urllib.parse import quote

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
FETCH_CAP = 6  # 발신자당 최대 처리 건수
SKIP_LINK = ("unsubscribe", "stop-email", "mailto:", "/profile", "preferences",
             "list-manage.com/unsubscribe", "/cs", "수신거부")
ASSET_HOST = ("googleapis.com", "gstatic.com", "w3.org", "schema.org", "googletagmanager",
              "doubleclick", "google-analytics", "/wp-content/", "cdn-cgi", "stibee.com/v2/open")
ASSET_EXT = (".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ico")


# ── Gmail (IMAP 앱비밀번호) ────────────────────────────────────────────────────

def imap_connect() -> imaplib.IMAP4_SSL:
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASS"].replace(" ", ""))
    M.select("INBOX")
    return M


def imap_search(M, sender: str, since: datetime.date) -> list[bytes]:
    crit = f'(FROM "{sender}" SINCE "{since.strftime("%d-%b-%Y")}")'
    typ, data = M.search(None, crit)
    return data[0].split() if typ == "OK" and data and data[0] else []


def imap_message(M, num: bytes):
    typ, data = M.fetch(num, "(RFC822)")
    if typ != "OK" or not data or not isinstance(data[0], tuple):
        return None
    return email.message_from_bytes(data[0][1])


def decode_hdr(s: str) -> str:
    if not s:
        return ""
    out = []
    for txt, enc in decode_header(s):
        out.append(txt.decode(enc or "utf-8", "replace") if isinstance(txt, bytes) else txt)
    return "".join(out)


def extract_bodies(msg) -> tuple[str, str]:
    """이메일 MIME에서 (html, plaintext) 결합 반환."""
    htmls, texts = [], []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ct = part.get_content_type()
        if ct not in ("text/html", "text/plain"):
            continue
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        dec = payload.decode(part.get_content_charset() or "utf-8", "replace")
        (htmls if ct == "text/html" else texts).append(dec)
    return "\n".join(htmls), "\n".join(texts)


# ── 텍스트 / URL ──────────────────────────────────────────────────────────────

def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * ((4 - len(data) % 4) % 4)).decode("utf-8", "replace")


def html_to_text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def _final_url(u: str) -> str:
    """추적 리다이렉트면 경로 세그먼트(뒤→앞)의 base64에서 원본 URL을 디코드해 복원."""
    base = u.split("?")[0]
    for seg in reversed(base.rstrip("/").split("/")):
        for cand in (seg, seg[1:] if len(seg) > 1 else ""):
            if len(cand) >= 24 and re.fullmatch(r"[A-Za-z0-9_\-]+=*", cand):
                try:
                    dec = _b64(cand)
                    if dec.startswith("http"):
                        return dec.split("?")[0]  # 디코드 성공 → utm 정리
                except Exception:
                    pass
    return u  # 디코드 실패(추적 URL) → 쿼리 보존(리다이렉트에 필요)


def canonical_url(html_body: str) -> str:
    """콘텐츠 링크 중 대표 URL. 자산(폰트·css·이미지)·추적·수신거부 제외."""
    for m in re.finditer(r'href="(https?://[^"]+)"', html_body):
        u = m.group(1)
        lo = u.lower()
        if any(s in lo for s in SKIP_LINK) or any(a in lo for a in ASSET_HOST):
            continue
        if any(lo.split("?")[0].endswith(e) for e in ASSET_EXT):
            continue
        return _final_url(u)
    return ""


# ── 분류 (Claude haiku) ───────────────────────────────────────────────────────

def classify(subject: str, text: str, region_hint: str) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    fallback = {"topics": [], "summary_ko": ""}
    if not key:
        return fallback
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        prompt = (
            "엔터·문화·소비 산업 뉴스레터 항목을 분류해 JSON으로만 응답.\n\n"
            f"제목: {subject}\n본문 발췌: {text[:1500]}\n\n"
            "topics: 해당되는 것만 (배열 0~3개) — " + ", ".join(TOPIC_KEYS) + "\n"
            "summary_ko: 한국어 150자 이내 핵심 요약 (무엇을 다뤘는지)\n\n"
            '{"topics": [...], "summary_ko": "..."}'
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
        return {"topics": topics, "summary_ko": (data.get("summary_ko") or "").strip()}
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

    if not os.environ.get("GMAIL_APP_PASS") or not os.environ.get("GMAIL_USER"):
        log.error("GMAIL_USER/GMAIL_APP_PASS 미설정 — IMAP 앱비밀번호 시크릿 필요")
        return 1

    M = imap_connect()
    since = datetime.date.today() - datetime.timedelta(days=LOOKBACK_DAYS)
    seen = recent_urls()
    rows = []

    for src in sources:
        if src.get("from", "").startswith("_"):
            continue
        try:
            nums = imap_search(M, src["from"], since)
        except Exception as e:
            log.warning("[%s] 검색 실패: %s", src["name"], e)
            continue
        for num in nums[-FETCH_CAP:]:
            try:
                msg = imap_message(M, num)
            except Exception as e:
                log.warning("[%s] fetch 실패: %s", src["name"], e)
                continue
            if not msg:
                continue
            subject = decode_hdr(msg.get("Subject", "")).strip()
            html_b, text_b = extract_bodies(msg)
            body_text = (text_b.strip() or html_to_text(html_b))[:2000]
            msgid = (msg.get("Message-ID") or "").strip().strip("<>")
            url = canonical_url(html_b) or (
                f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{quote(msgid)}"
                if msgid else f"newsletter:{src['name']}:{num.decode()}")
            if url in seen:
                continue
            try:
                pub = parsedate_to_datetime(msg.get("Date")).astimezone(datetime.timezone.utc).isoformat()
            except Exception:
                pub = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cls = classify(subject, body_text, src.get("region", "global-en"))
            rows.append({
                "id": str(uuid.uuid4()),
                "title": subject[:500] or "(제목 없음)",
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
            })
            seen.add(url)
            log.info("[%s] %s | %s", src["name"], "·".join(cls["topics"]) or "-", subject[:55])

    try:
        M.logout()
    except Exception:
        pass

    log.info("수집 %d건 (allowlist %d발신자, 최근 %d일)", len(rows), len(sources), LOOKBACK_DAYS)
    if dry:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    saved = 0
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
