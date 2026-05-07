"""
Maily API에서 최신 발행 게시물을 가져와 새 발행 여부 판별.
인자: <api_token> <last_id_file>
"""
import sys, json, html, re, urllib.request

sys.stdout.reconfigure(encoding='utf-8')

EXCERPT_LIMIT = 400
SLUG          = "draft.briefing"
API_BASE      = "https://api.maily.so"

API_TOKEN    = sys.argv[1]
LAST_ID_FILE = sys.argv[2]

# ── API 호출: 최신 발행 1건 ────────────────────────────────
url = f"{API_BASE}/api/{SLUG}/notes.json?status=published&order_by=published_at&page=1"
req = urllib.request.Request(url, headers={
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
})
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode('utf-8'))

# ── 응답 구조 파악 (첫 키와 첫 아이템 키 로그) ────────────
print(f"[debug] top-level keys: {list(data.keys())}", file=sys.stderr)
items = None
for key in ('notes', 'posts', 'items', 'data', 'results'):
    if key in data:
        items = data[key]
        print(f"[debug] found items under '{key}', count={len(items)}", file=sys.stderr)
        break

if not items:
    # 응답 자체가 리스트일 수도 있음
    if isinstance(data, list):
        items = data
        print(f"[debug] response is a list, count={len(items)}", file=sys.stderr)
    else:
        print(f"[debug] full response: {json.dumps(data, ensure_ascii=False)[:500]}", file=sys.stderr)
        print("new=false")
        sys.exit(0)

if not items:
    print("new=false")
    sys.exit(0)

latest = items[0]
print(f"[debug] first item keys: {list(latest.keys())}", file=sys.stderr)

# ── 필드 추출 (다양한 키명 대응) ─────────────────────────
def pick(d, *keys, default=""):
    for k in keys:
        if k in d and d[k]:
            return str(d[k])
    return default

post_id   = pick(latest, 'id', 'note_id', 'post_id')
title     = html.unescape(pick(latest, 'title', 'subject'))
link      = pick(latest, 'url', 'permalink', 'link', 'post_url')
pub_date  = pick(latest, 'published_at', 'created_at', 'sent_at')
thumbnail = pick(latest, 'thumbnail_image', 'thumbnail_url', 'cover_image', 'image_url')

# 본문: HTML 태그 제거 후 plain text 추출
raw_body  = pick(latest, 'body', 'content', 'html', 'body_html', 'preview_text', 'description')
if raw_body:
    body_text = re.sub(r'<[^>]+>', ' ', raw_body)
    body_text = html.unescape(re.sub(r'\s+', ' ', body_text).strip())
    excerpt   = (body_text[:EXCERPT_LIMIT].rsplit(' ', 1)[0] + '…') if len(body_text) > EXCERPT_LIMIT else body_text
else:
    excerpt   = ""

# ── 이전 ID 비교 ───────────────────────────────────────────
try:
    with open(LAST_ID_FILE) as f:
        prev_id = f.read().strip()
except FileNotFoundError:
    prev_id = ""

if post_id and post_id == prev_id:
    print("new=false")
    sys.exit(0)

# ── 발행일 포맷 정리 ───────────────────────────────────────
try:
    from datetime import datetime, timezone
    # ISO 8601 처리
    dt = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
    pub_pretty = dt.strftime('%Y.%m.%d')
except Exception:
    pub_pretty = pub_date[:10] if len(pub_date) >= 10 else pub_date

# ── 출력 ──────────────────────────────────────────────────
print("new=true")
print(f"post_id={post_id}")
print(f"title={title}")
print(f"link={link}")
print(f"pub_date={pub_pretty}")
print(f"excerpt={excerpt}")
print(f"thumbnail={thumbnail}")
