"""
Maily API에서 최신 발행 게시물 파싱 + 상세 API로 본문 추출.
인자: <api_token> <last_id_file>
"""
import sys, json, html, re, urllib.request, urllib.error

sys.stdout.reconfigure(encoding='utf-8')

EXCERPT_LIMIT = 400
SLUG          = "draft.briefing"
API_BASE      = "https://api.maily.so"

API_TOKEN    = sys.argv[1]
LAST_ID_FILE = sys.argv[2]

def api_get(path):
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {API_TOKEN}", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print(f"[error] HTTP {e.code}: {body}", file=sys.stderr)
        return None

# ── 목록 API: 최신 발행 1건 ────────────────────────────────
data = api_get(f"/api/{SLUG}/notes.json?status=published&order_by=published_at&page=1")
if not data:
    sys.exit(1)

notes = data.get('notes', [])
if not notes:
    print("new=false")
    sys.exit(0)

latest   = notes[0]
post_id  = str(latest.get('ext_id', ''))
title    = html.unescape(str(latest.get('title', '')))
subtitle = html.unescape(str(latest.get('subtitle', '')))
pub_raw  = latest.get('published_at', '')
thumb    = latest.get('thumbnail_image_cdn_url', '') or ''
link     = f"https://maily.so/{SLUG}/posts/{post_id}" if post_id else ''

# ── 이전 ID 비교 ───────────────────────────────────────────
try:
    with open(LAST_ID_FILE) as f:
        prev_id = f.read().strip()
except FileNotFoundError:
    prev_id = ""

if post_id and post_id == prev_id:
    print("new=false")
    sys.exit(0)

# ── 상세 API: 본문 추출 ────────────────────────────────────
excerpt = subtitle  # 기본값: subtitle
detail  = api_get(f"/api/{SLUG}/notes/{post_id}.json")
if detail:
    print(f"[debug] detail keys: {list(detail.keys())}", file=sys.stderr)
    # body 필드 탐색
    raw_body = ""
    for key in ('body', 'content', 'html', 'body_html', 'rendered_body'):
        val = detail.get(key, '')
        if val:
            raw_body = val
            break
    if raw_body:
        text = re.sub(r'<[^>]+>', ' ', raw_body)
        text = html.unescape(re.sub(r'\s+', ' ', text).strip())
        excerpt = (text[:EXCERPT_LIMIT].rsplit(' ', 1)[0] + '…') if len(text) > EXCERPT_LIMIT else text

# ── 발행일 포맷 ────────────────────────────────────────────
try:
    from datetime import datetime
    dt = datetime.fromisoformat(pub_raw.replace('Z', '+00:00'))
    pub_pretty = dt.strftime('%Y.%m.%d')
except Exception:
    pub_pretty = pub_raw[:10] if len(pub_raw) >= 10 else pub_raw

# ── 출력 ──────────────────────────────────────────────────
print("new=true")
print(f"post_id={post_id}")
print(f"title={title}")
print(f"link={link}")
print(f"pub_date={pub_pretty}")
print(f"excerpt={excerpt}")
print(f"thumbnail={thumb}")
