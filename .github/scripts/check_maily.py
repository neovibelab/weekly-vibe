"""
Maily RSS 피드에서 최신 항목을 파싱해 새 발행 여부를 판별.
새 항목 있으면 포스트 페이지에서 본문 요약도 추출.
인자: <feed_url> <last_guid_file>
"""
import sys, re, html, urllib.request
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding='utf-8')

EXCERPT_LIMIT = 280  # Discord embed description 글자 수 제한

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

# ── 본문 요약 추출 ─────────────────────────────────────────
class TextExtractor(HTMLParser):
    """<p> 태그 내 텍스트만 수집"""
    def __init__(self):
        super().__init__()
        self.texts = []
        self._in_p = False

    def handle_starttag(self, tag, attrs):
        if tag == 'p':
            self._in_p = True

    def handle_endtag(self, tag):
        if tag == 'p':
            self._in_p = False

    def handle_data(self, data):
        if self._in_p:
            text = data.strip()
            if text:
                self.texts.append(text)

excerpt = ""
try:
    req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        page = r.read().decode('utf-8', errors='replace')

    parser = TextExtractor()
    parser.feed(page)
    full_text = " ".join(parser.texts)
    full_text = html.unescape(full_text)
    full_text = re.sub(r'\s+', ' ', full_text).strip()

    if len(full_text) > EXCERPT_LIMIT:
        excerpt = full_text[:EXCERPT_LIMIT].rsplit(' ', 1)[0] + "…"
    else:
        excerpt = full_text
except Exception:
    excerpt = ""

# ── 출력 ───────────────────────────────────────────────────
print("new=true")
print(f"title={title}")
print(f"link={link}")
print(f"guid={guid}")
print(f"pub_date={pub_date}")
print(f"excerpt={excerpt}")
