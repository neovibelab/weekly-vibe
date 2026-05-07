"""
Maily RSS 피드에서 최신 항목 파싱 + 포스트 페이지에서 요약·썸네일 추출.
인자: <feed_url> <last_guid_file>
"""
import sys, re, html, urllib.request
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding='utf-8')

EXCERPT_LIMIT = 400

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

# ── 포스트 페이지 파싱: OG + 본문 텍스트 ───────────────────
LOGIN_MARKERS = ('로그인이 필요해요', '이메일 인증', '이메일로 전송')

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.og = {}
        self._in_body = False
        self._in_p = False
        self._skip = False
        self.paragraphs = []
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self._in_body = True
        if tag == 'meta':
            a = dict(attrs)
            prop = a.get('property', a.get('name', ''))
            if prop in ('og:description', 'og:image') and 'content' in a:
                self.og[prop] = a['content']
        if self._in_body and tag == 'p' and not self._skip:
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == 'p' and self._in_p:
            self._in_p = False
            text = ' '.join(self._buf).strip()
            if text:
                # 로그인 안내문이면 이후 수집 중단
                if any(m in text for m in LOGIN_MARKERS):
                    self._skip = True
                else:
                    self.paragraphs.append(text)
            self._buf = []

    def handle_data(self, data):
        if self._in_p and not self._skip:
            t = data.strip()
            if t:
                self._buf.append(t)

excerpt   = ""
thumbnail = ""
try:
    # Discordbot UA → Maily SSR 트리거
    for ua in [
        "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
        "Mozilla/5.0 (compatible; Twitterbot/1.0)",
        "Mozilla/5.0",
    ]:
        req = urllib.request.Request(link, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=15) as r:
            page = r.read(32768).decode('utf-8', errors='replace')

        parser = PageParser()
        parser.feed(page)

        og_desc   = html.unescape(parser.og.get('og:description', ''))
        thumbnail = parser.og.get('og:image', '')
        body_text = ' '.join(parser.paragraphs)
        body_text = html.unescape(re.sub(r'\s+', ' ', body_text).strip())

        # 본문이 og:description과 다른 실제 내용이면 사용
        if body_text and body_text != og_desc and len(body_text) > len(og_desc):
            raw = body_text
        else:
            raw = og_desc

        if raw:
            excerpt = (raw[:EXCERPT_LIMIT].rsplit(' ', 1)[0] + '…') if len(raw) > EXCERPT_LIMIT else raw
            break

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
