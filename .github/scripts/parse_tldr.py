import re, html, sys

with open(sys.argv[1], encoding='utf-8') as f:
    content = f.read()

m = re.search(r'<ul class="tldr-list">(.*?)</ul>', content, re.DOTALL)
if not m:
    sys.exit(0)

for item in re.findall(r'<li>(.*?)</li>', m.group(1), re.DOTALL):
    item = re.sub(r'<span class="tldr-num">[^<]*</span>', '', item)
    item = re.sub(r'<[^>]+>', '', item)
    item = html.unescape(item)
    item = ' '.join(item.split())
    if item:
        print(item)
