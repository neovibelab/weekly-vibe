"""
Maily RSS 피드에서 최신 항목 파싱 + 포스트 페이지 OG 태그에서 요약·썸네일 추출.
인자: <feed_url> <last_guid_file>
"""
import sys, re, html, urllib.request
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding='utf-8')

FEED_URL       = sys.argv[1]
LAST_GUID_FILE = sys.argv[2]

# ── RSS 피드 fetch ──────────────────────────────────────────
with urllib.request.urlopen(FEED_URL, timeout=15) as r:
    content = r.read().decode('utf-8')

m = re.search(
    r'<item>\s*<title>(.*?)</title>.*?<link>(.*?)</link>.*?'
    r'<guid[^>]*>(.*?)</guid>.*?<pubDate>(.*?)</pubDate>',
    content, re.DOTALL
)
if not m:
    print("new=false")
    sys.exit(0)

title    = html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
link     = m.group(2).strip()
guid     = m.group(3).strip()
pub_date = m.group(4).strip()

# ── 이전 guid 비교 ─────────────────────────────────────────
try:
    with open(LAST_GUID_FILE) as f:
        prev_guid = f.read().strip()
except FileNotFoundError:
    prev_guid = ""

if guid == prev_guid:
    print("new=false")
    sys.exit(0)

# ── OG 태그에서 요약·썸네일 추출 (로그인 불필요) ─────────────
class OGParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.og = {}

    def handle_starttag(self, tag, attrs):
        if tag != 'meta':
            return
        attrs = dict(attrs)
        prop = attrs.get('property', attrs.get('name', ''))
        if prop in ('og:description', 'og:image') and 'content' in attrs:
            self.og[prop] = attrs['content']

excerpt   = ""
thumbnail = ""
try:
    req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        # <head>만 읽으면 충분 — 최대 8KB
        page = r.read(8192).decode('utf-8', errors='replace')

    parser = OGParser()
    parser.feed(page)
    excerpt   = html.unescape(parser.og.get('og:description', ''))
    thumbnail = parser.og.get('og:image', '')
except Exception:
    pass

# ── 출력 ───────────────────────────────────────────────────
print("new=true")
print(f"title={title}")
print(f"link={link}")
print(f"guid={guid}")
print(f"pub_date={pub_date}")
print(f"excerpt={excerpt}")
print(f"thumbnail={thumbnail}")
