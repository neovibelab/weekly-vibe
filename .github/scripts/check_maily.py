"""
Maily RSS 피드에서 최신 항목을 파싱해 새 발행 여부를 판별.
인자: <feed_url> <last_guid_file>
새 항목 있으면 GITHUB_OUTPUT 형식으로 출력, 없으면 new=false 출력.
"""
import sys, re, html, urllib.request
sys.stdout.reconfigure(encoding='utf-8')

FEED_URL      = sys.argv[1]
LAST_GUID_FILE = sys.argv[2]

# RSS 피드 fetch
with urllib.request.urlopen(FEED_URL, timeout=15) as r:
    content = r.read().decode('utf-8')

# 최신 item 파싱
m = re.search(
    r'<item>\s*<title>(.*?)</title>.*?<link>(.*?)</link>.*?'
    r'<guid[^>]*>(.*?)</guid>.*?<pubDate>(.*?)</pubDate>',
    content, re.DOTALL
)
if not m:
    print("new=false")
    sys.exit(0)

title   = html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
link    = m.group(2).strip()
guid    = m.group(3).strip()
pub_date = m.group(4).strip()

# 이전 guid 비교
try:
    with open(LAST_GUID_FILE) as f:
        prev_guid = f.read().strip()
except FileNotFoundError:
    prev_guid = ""

if guid == prev_guid:
    print("new=false")
    sys.exit(0)

# 새 항목 발견 — GitHub Actions multiline output 형식
print("new=true")
print(f"title={title}")
print(f"link={link}")
print(f"guid={guid}")
print(f"pub_date={pub_date}")
