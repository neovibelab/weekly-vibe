#!/usr/bin/env python3
"""
Vibe Signal Collector v3 — 지역·언어 기반 통합 수집기
--------------------------------------------------------------
5개 지역(한국·글로벌·중국·일본·동남아)을 각 지역의 네이티브 언어로 검색.
7개 주제(팬행동·소비행동·딜·IP·오너십·테크·Z세대)는 검색 필터 겸 태깅 기준.
Z세대 주제는 엔터 밖 소비 시장 전반(패션·뷰티·F&B·여행·리테일) 체크용.
Anthropic web_search 서버 사이드 도구로 검색+분석+요약을 단일 API 호출로 처리.

사용법:
  python scripts/vibe_search.py <region>
  python scripts/vibe_search.py <region> --dry-run

  <region>: korea | global-en | china | japan | southeast-asia

환경변수:
  ANTHROPIC_API_KEY              Claude API 키
  DISCORD_<REGION>_WEBHOOK       Discord 웹훅 (지역별)
  SEEN_FILE                      채널 간 중복 제거 파일 (기본: seen-titles.txt)
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import logging
import os
import re
import sys
import time
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests
from anthropic import Anthropic

try:
    from supabase_writer import save_items as supabase_save, fetch_recent_titles
except ImportError:
    supabase_save = None
    fetch_recent_titles = None

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── 주제 라벨 (태깅·디스플레이용) ─────────────────────────

TOPIC_LABELS: dict[str, str] = {
    "fan-behavior": "팬행동",
    "consumer-behavior": "소비행동",
    "ent-deals": "딜",
    "ip-business": "IP",
    "artist-ownership": "오너십",
    "tech-issues": "테크",
    # 엔터 밖 소비 시장 체크용 — Z세대 문화·가치관·소비행태·라이프스타일
    # (패션·뷰티·F&B·여행·리테일 등, 2026-06-10 대표 지시)
    "gen-z-lifestyle": "Z세대",
}

TOPIC_KEYS = list(TOPIC_LABELS.keys())

# ── 지역 설정 ────────────────────────────────────────────

REGIONS: dict[str, dict] = {
    "korea": {
        "name": "한국",
        "emoji": "🇰🇷",
        "webhook_env": "DISCORD_KOREA_WEBHOOK",
        "language": "한국어",
        "search_instruction": (
            "**반드시 한국어로 검색**하세요. 영어 검색은 하지 마세요."
        ),
        "edge_note": (
            "대표는 한국 뉴스를 이미 잘 파악하고 있습니다. "
            "일반 헤드라인 반복이 아니라, 숫자·데이터가 담긴 기사나 "
            "여러 사건의 연결고리를 보여주는 분석 기사를 우선하세요."
        ),
        # 조선·중앙·동아·한겨레·매경·연합은 Anthropic 크롤러 차단(robots.txt)으로
        # allowed_domains에 넣을 수 없음 — 2026-06-10 프로브 실측. 변경 시 domain-probe.yml로 재검증.
        "trusted_sources": (
            "한국경제(텐아시아 포함), 한국일보, 서울신문, 경향신문·스포츠경향, "
            "서울경제, 헤럴드경제, 이데일리, 아시아경제, 파이낸셜뉴스, 뉴스1, 뉴시스, "
            "YTN, 전자신문, 디지털데일리, 미디어오늘, 빌보드코리아, 시사IN, 더밀크"
        ),
        "allowed_domains": [
            "hankyung.com", "hankookilbo.com", "seoul.co.kr", "khan.co.kr",
            "sedaily.com", "heraldcorp.com", "edaily.co.kr", "asiae.co.kr",
            "fnnews.com", "news1.kr", "newsis.com", "ytn.co.kr",
            "etnews.com", "ddaily.co.kr", "mediatoday.co.kr",
            "billboard.co.kr", "sisain.co.kr", "themilk.com",
        ],
        "search_terms": {
            "fan-behavior": ["케이팝 팬덤 소비", "콘서트 투어 매출", "위버스 팬 플랫폼"],
            "consumer-behavior": ["엔터 브랜드 콜라보", "MZ세대 문화 소비", "굿즈 시장 규모"],
            "ent-deals": ["엔터 투자 인수", "음악 레이블 M&A", "엔터 기업 실적"],
            "ip-business": ["IP 사업 확장", "캐릭터 라이선싱", "웹툰 영상화"],
            "artist-ownership": ["아티스트 독립 레이블", "음악 저작권 분쟁", "자체 기획사"],
            "tech-issues": ["AI 음악 생성 저작권", "스트리밍 정산", "음악 플랫폼 정책"],
            "gen-z-lifestyle": ["Z세대 소비 트렌드 조사", "Z세대 가치관 라이프스타일", "잘파세대 소비 행태"],
        },
    },
    "global-en": {
        "name": "글로벌(영어)",
        "emoji": "🌐",
        "webhook_env": "DISCORD_GLOBAL_EN_WEBHOOK",
        "language": "English",
        "search_instruction": (
            "**Search in English.** "
            "Focus on global entertainment and music industry trends from trade media."
        ),
        "edge_note": (
            "단순 차트 뉴스보다 산업 구조 변화를 다루는 깊은 분석 기사를 우선합니다. "
            "K-pop·한류의 글로벌 비즈니스 임팩트, 영미권 음악 산업 M&A·투자, "
            "그리고 아시아 엔터 산업에 대한 영어권 매체의 보도가 핵심입니다."
        ),
        # FT·Reuters·WSJ·NYT·Guardian·Pitchfork·Economist는 Anthropic 크롤러 차단 —
        # 2026-06-10 프로브 실측. 변경 시 domain-probe.yml로 재검증.
        "trusted_sources": (
            "Billboard, Variety, Music Business Worldwide, "
            "Hits Daily Double, TechCrunch, Bloomberg, Axios, "
            "The Hollywood Reporter, Rolling Stone, NME, "
            "Music Ally, Digital Music News, CMU, "
            "IFPI, MIDiA Research, Luminate"
        ),
        "allowed_domains": [
            "billboard.com", "variety.com", "musicbusinessworldwide.com",
            "hitsdailydouble.com", "techcrunch.com", "bloomberg.com",
            "axios.com", "hollywoodreporter.com", "rollingstone.com",
            "nme.com", "musically.com", "digitalmusicnews.com",
            "completemusicupdate.com", "ifpi.org", "midiaresearch.com",
            "luminatedata.com",
            # 주의: IP홀더 뉴스룸 도메인(sony.com 등)은 여기 넣지 말 것 — Anthropic
            # 크롤러 차단 도메인이 allowed_domains에 하나만 끼어도 web_search가
            # 요청 전체를 400 거부해 글로벌 수집이 통째로 죽는다 (2026-06-16~17 사고).
            # 추가하려면 반드시 probe_domains.py로 사전 검증, 통과분만.
        ],
        "search_terms": {
            "fan-behavior": ["K-pop fandom economy", "concert touring revenue 2026", "fan platform engagement"],
            "consumer-behavior": ["entertainment brand collaboration", "Gen Z cultural consumption", "music merch market"],
            "ent-deals": ["music industry M&A 2026", "entertainment investment deal", "music catalog acquisition"],
            "ip-business": ["music IP licensing deal", "entertainment franchise expansion", "cross-media IP"],
            "artist-ownership": ["artist-owned label", "master recording ownership", "creator economy music"],
            "tech-issues": ["AI music copyright", "streaming platform policy change", "music tech startup funding"],
            "gen-z-lifestyle": ["Gen Z consumer trends report", "Gen Z values lifestyle survey", "Gen Z spending habits 2026"],
        },
    },
    "china": {
        "name": "중국",
        "emoji": "🇨🇳",
        "webhook_env": "DISCORD_CHINA_WEBHOOK",
        "language": "中文(简体)",
        "search_instruction": (
            "**必须用简体中文搜索。** 不要用英文搜索。"
        ),
        "edge_note": (
            "영어로 번역되지 않는 중국 엔터 시장의 1차 소스가 핵심 가치입니다. "
            "广电总局 규제 변화, 腾讯音乐·网易云 플랫폼 전략, "
            "아이돌 시장(选秀·饭圈) 동향, 음악 저작권 거래에 주목하세요."
        ),
        "trusted_sources": (
            "36氪, 虎嗅, 界面新闻, 第一财经, 财新, "
            "南方周末, 澎湃新闻, 新浪娱乐, "
            "腾讯娱乐, 每日经济新闻"
        ),
        "allowed_domains": [
            "36kr.com", "huxiu.com", "jiemian.com", "yicai.com",
            "caixin.com", "infzm.com", "thepaper.cn", "sina.com.cn",
            "qq.com", "nbd.com.cn",
        ],
        "search_terms": {
            "fan-behavior": ["粉丝经济 趋势", "演唱会市场 规模", "饭圈消费"],
            "consumer-behavior": ["文娱消费 趋势", "品牌跨界 合作", "Z世代 消费 文化"],
            "ent-deals": ["娱乐公司 投资 并购", "音乐版权 交易", "影视 资本 运作"],
            "ip-business": ["IP授权 衍生品", "动漫 游戏 联动", "文娱IP 商业化"],
            "artist-ownership": ["艺人 独立 厂牌", "音乐人 版权 归属", "艺人 工作室"],
            "tech-issues": ["AI音乐 版权", "流媒体 平台 竞争", "音乐科技 创业"],
            "gen-z-lifestyle": ["Z世代 消费 趋势 报告", "00后 价值观 生活方式", "年轻人 消费 行为 变化"],
        },
    },
    "japan": {
        "name": "일본",
        "emoji": "🇯🇵",
        "webhook_env": "DISCORD_JAPAN_WEBHOOK",
        "language": "日本語",
        "search_instruction": (
            "**必ず日本語で検索してください。** 英語で検索しないでください。"
        ),
        "edge_note": (
            "일본 음악 시장의 독특한 구조(피지컬 강세, 팬클럽 모델, IP 다각화)에 주목하세요. "
            "영어권에서 잘 보도되지 않는 일본 시장 내부 동향이 핵심 가치입니다. "
            "K-pop의 일본 시장 전략, J-pop·보카로이드·VTuber 동향도 포함합니다."
        ),
        # 朝日·毎日는 Anthropic 크롤러 차단 — 2026-06-10 프로브 실측.
        "trusted_sources": (
            "日経エンタテインメント!, 音楽ナタリー, ORICON NEWS, "
            "Billboard JAPAN, リアルサウンド, BARKS, "
            "日本経済新聞, 東洋経済, ITmedia, MANTANWEB"
        ),
        "allowed_domains": [
            "nikkei.com", "natalie.mu", "oricon.co.jp", "billboard-japan.com",
            "realsound.jp", "barks.jp", "toyokeizai.net", "itmedia.co.jp",
            "mantan-web.jp",
        ],
        "search_terms": {
            "fan-behavior": ["推し活 消費 トレンド", "コンサート ライブ 市場", "ファンクラブ 会員数"],
            "consumer-behavior": ["エンタメ ブランド コラボ", "Z世代 文化消費", "グッズ市場 規模"],
            "ent-deals": ["音楽 レーベル 買収", "エンタメ 投資", "芸能事務所 資本"],
            "ip-business": ["IP ライセンス ビジネス", "アニメ ゲーム 連動", "キャラクター 商品化"],
            "artist-ownership": ["アーティスト 独立 レーベル", "音楽 著作権 問題", "クリエイター エコノミー"],
            "tech-issues": ["AI 音楽 著作権", "サブスク ストリーミング", "音楽テック スタートアップ"],
            "gen-z-lifestyle": ["Z世代 消費 トレンド 調査", "Z世代 価値観 ライフスタイル", "若者 消費行動 変化"],
        },
    },
    "southeast-asia": {
        "name": "동남아",
        "emoji": "🌏",
        "webhook_env": "DISCORD_SOUTHEAST_ASIA_WEBHOOK",
        "language": "English (+ local)",
        "search_instruction": (
            "**Search in English**, targeting Southeast Asian markets: "
            "Philippines, Indonesia, Thailand, Vietnam, Malaysia, Singapore."
        ),
        "edge_note": (
            "동남아는 K-pop·한류의 핵심 성장 시장입니다. "
            "개별 국가 뉴스보다 ASEAN 단위 트렌드, "
            "현지 아티스트(SB19, BINI, 4th Impact 등)의 부상, "
            "한류와 현지 문화의 접점 사례를 우선합니다."
        ),
        # Straits Times·CNA는 Anthropic 크롤러 차단 — 2026-06-10 프로브 실측.
        "trusted_sources": (
            "Rappler, Bangkok Post, Kompas, "
            "Nikkei Asia, South China Morning Post, "
            "Philippine Daily Inquirer, VnExpress International, "
            "The Jakarta Post"
        ),
        "allowed_domains": [
            "rappler.com", "bangkokpost.com", "kompas.com",
            "nikkei.com", "scmp.com", "inquirer.net",
            "vnexpress.net", "thejakartapost.com",
        ],
        "search_terms": {
            "fan-behavior": ["K-pop fandom Southeast Asia", "SB19 BINI fan community", "concert market ASEAN"],
            "consumer-behavior": ["entertainment consumption Southeast Asia", "Gen Z cultural trends ASEAN", "Hallyu brand impact"],
            "ent-deals": ["entertainment investment Southeast Asia", "music label ASEAN expansion", "K-pop agency partnership Asia"],
            "ip-business": ["anime manga licensing Southeast Asia", "entertainment IP ASEAN", "webtoon adaptation Asia"],
            "artist-ownership": ["independent artist Southeast Asia", "P-pop industry Philippines", "local music industry ASEAN"],
            "tech-issues": ["music streaming Southeast Asia", "TikTok music ASEAN", "digital entertainment platform Asia"],
            "gen-z-lifestyle": ["Gen Z consumer trends Southeast Asia", "Gen Z lifestyle values ASEAN", "youth spending behavior Southeast Asia"],
        },
    },
}

MAX_CANDIDATES = 5
DUPLICATE_THRESHOLD = 0.75
MIN_TOTAL_SCORE = 3
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "48"))
MAX_TOKENS = int(os.environ.get("VS_MAX_TOKENS", "4096"))  # 백필 등 후보 많을 때 상향
URL_CHECK_TIMEOUT = 8
SCORE_KEYS = ("newsletter_fit", "carousel_fit", "reliability")
HANGUL_RE = re.compile(r"[가-힣]")
KST = datetime.timezone(datetime.timedelta(hours=9))

# 출처 차단 도메인 — 코드 검증에서 제외 (화이트리스트와 별개의 방어선).
# 나무위키: 위키 특성상 1차 출처 아님 (2026-06-10 대표 지시)
BLOCKED_DOMAINS = ("namu.wiki",)

# 발행일 미상 기사 게재 허용 여부 (기본 제외 — 2026-06-10 대표 지시).
# 화이트리스트 매체가 검색에서 안 잡혀 0건이 반복되면 "1"로 임시 완화.
ALLOW_UNDATED = os.environ.get("ALLOW_UNDATED", "0") == "1"

# ── 프롬프트 ──────────────────────────────────────────────


def build_search_prompt(region: dict, today: datetime.date, cutoff: datetime.date) -> str:
    topic_sections = []
    for i, (key, terms) in enumerate(region["search_terms"].items(), 1):
        label = TOPIC_LABELS[key]
        terms_str = ", ".join(f'"{t}"' for t in terms)
        topic_sections.append(f"{i}. **{label}** — 검색어 예: {terms_str}")

    topics_block = "\n".join(topic_sections)
    valid_keys = ", ".join(TOPIC_KEYS)

    return (
        "당신은 엔터테인먼트·음악 산업과 Z세대 소비 시장의 Vibe 신호 수집기입니다.\n\n"
        f"## 수집 지역: {region['name']} ({region['language']})\n\n"
        f"## 검색 지시\n\n"
        f"{region['search_instruction']}\n"
        f"오늘은 {today.isoformat()} (KST)입니다.\n"
        f"최근 {MAX_AGE_HOURS}시간 이내({cutoff.isoformat()} ~ {today.isoformat()} 발행)의 "
        "뉴스·기사·보도만 웹 검색으로 찾으세요.\n"
        "검색 결과의 page_age 등 메타데이터로 발행일을 판단하세요. "
        f"{cutoff.isoformat()}보다 확실히 오래된 기사는 제외하세요. "
        "최종 후보로 선택할 기사인데 메타데이터로 발행일이 확인되지 않으면, "
        "**버리지 말고 반드시 web_fetch로 그 기사 페이지를 열어** 본문의 발행일을 확인하세요 "
        "(최종 후보가 아닌 기사는 열지 마세요). "
        "fetch로도 확인되지 않으면 published_date를 null로 두고 후보에 포함하세요 — "
        "발행일 불명 기사의 제외 여부는 시스템이 판단합니다. "
        "당신이 발행일 불명을 이유로 후보를 0건으로 만들지 마세요.\n"
        "뉴스레터와 캐러셀 소재로 활용할 수 있는 사례를 선별합니다.\n\n"
        "다음 7개 주제 영역을 커버하도록 **최소 5회** 다양한 검색어로 검색하세요.\n"
        "한 번의 검색으로 모든 주제를 다루려 하지 말고, 주제별로 나눠서 검색하세요.\n"
        "**Z세대(gen-z-lifestyle) 주제는 별도로 최소 1회 검색**하세요 — 이 주제는 "
        "엔터테인먼트에 국한하지 않습니다. 패션·뷰티·F&B·여행·리테일·테크 소비 등 "
        "소비 시장 전반에서 Z세대의 문화·가치관·소비행태·라이프스타일 신호를 수집합니다.\n\n"
        f"{topics_block}\n\n"
        "## 검색 대상 매체 (화이트리스트)\n"
        "검색은 다음 매체로 제한됩니다 — 주요 일간지·주간지·매거진·전문지 위주:\n"
        f"{region['trusted_sources']}\n"
        "보도자료 재가공·어그리게이터·위키·커뮤니티는 출처로 쓰지 마세요.\n\n"
        f"## 차별화 포인트\n{region['edge_note']}\n\n"
        "## 공통 원칙\n"
        "- 구체적 수치·데이터·사례가 포함된 기사 우선\n"
        "- 여러 사건의 연결고리를 보여주는 분석 기사 우선 (단순 보도보다)\n"
        "- 하나의 기사가 여러 주제에 걸칠 수 있음 — topics에 복수 태깅 가능\n"
        "- 요약(summary)은 **반드시 한국어**로 작성 (원문 언어와 무관)\n"
        "- 제목(title)은 **한국어로 번역**하세요. 원문 언어와 무관하게 반드시 한국어 제목으로.\n"
        "- published_date는 기사 발행일(YYYY-MM-DD). page_age 또는 web_fetch로 확인된 날짜만 적고, "
        "추정하지 마세요. 그래도 확인 불가하면 null로 두되 기사 자체는 포함하세요.\n"
        "- 나무위키 등 위키 문서·커뮤니티 게시글은 출처(url)로 사용하지 마세요. "
        "언론 보도·공식 발표를 출처로 하세요.\n\n"
        "## 선별 기준 (각 0~2점)\n"
        "1. **소재적합**(newsletter_fit): 뉴스레터 칼럼 소재로서 해석 가능한 구체적 사례·데이터가 있는가\n"
        "   (0=일반 뉴스, 1=관점 가능, 2=풍부한 사례+데이터)\n"
        "2. **캐러셀적합**(carousel_fit): 태도→증거→함의→질문 서사 아크를 만들 수 있는가\n"
        "   (0=아크 불가, 1=단일 포인트, 2=완전한 아크 가능)\n"
        "3. **출처신뢰**(reliability): 출처가 확인 가능하고 1차 자료에 근거하는가\n"
        "   (0=출처 불분명, 1=2차 보도, 2=1차 자료/공식 발표)\n\n"
        "## 출력\n\n"
        f"total_score(3개 합산) {MIN_TOTAL_SCORE}점 이상인 후보를 최소 1개, 최대 {MAX_CANDIDATES}개 선택하세요.\n"
        "좋은 후보가 1~2개뿐이면 그만큼만 출력하세요. 개수를 채우려고 기준 미달 기사를 포함하지 마세요.\n"
        "JSON 배열만 출력하고 다른 텍스트는 추가하지 마세요.\n\n"
        "```json\n"
        "[\n"
        "  {\n"
        '    "title": "기사 제목 (한국어로 번역)",\n'
        '    "url": "출처 URL",\n'
        '    "source": "매체명",\n'
        '    "published_date": "YYYY-MM-DD (기사 발행일, 확인 불가 시 null)",\n'
        f'    "topics": ["해당 주제 키 — 유효값: {valid_keys}"],\n'
        '    "summary": "200자 이내 한국어 요약. 원문 언어와 무관하게 반드시 한국어로.",\n'
        '    "newsletter_fit": 0,\n'
        '    "carousel_fit": 0,\n'
        '    "reliability": 0,\n'
        '    "total_score": 0\n'
        "  }\n"
        "]\n"
        "```\n\n"
        "기준을 충족하는 후보가 하나도 없으면 빈 배열 `[]`만 출력하세요. 억지로 1개를 만들지 마세요.\n"
        "배열 앞뒤에 보고·해설·마크다운 텍스트를 덧붙이지 마세요."
    )


# ── JSON 파싱 (견고) ──────────────────────────────────────


def _parse_json_robust(raw: str) -> list[dict]:
    """JSON 배열 파싱. 실패 시 수리 → 개별 객체 추출 폴백."""
    # 1차: 원본 그대로
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("JSON 디코드 실패 (1차): %s", exc)

    # 2차: 간단한 수리
    repaired = re.sub(r",\s*([}\]])", r"\1", raw)       # trailing comma
    repaired = re.sub(r"[\x00-\x1f]", " ", repaired)    # control chars
    repaired = repaired.replace("\\'", "'")
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        log.warning("JSON 수리 실패 (2차)")

    # 3차: 개별 JSON 객체를 하나씩 추출
    results = []
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                fragment = raw[start : i + 1]
                try:
                    obj = json.loads(fragment)
                    results.append(obj)
                except json.JSONDecodeError:
                    # 개별 객체도 수리 시도
                    frag2 = re.sub(r",\s*}", "}", fragment)
                    frag2 = re.sub(r"[\x00-\x1f]", " ", frag2)
                    try:
                        obj = json.loads(frag2)
                        results.append(obj)
                    except json.JSONDecodeError:
                        log.warning("개별 객체 파싱 실패: %s", fragment[:120])
                start = None
    if results:
        log.info("개별 객체 추출 성공: %d건", len(results))
    else:
        log.warning("모든 파싱 실패, 원문 500자: %s", raw[:500])
    return results


# ── 검색 ──────────────────────────────────────────────────


def search_and_analyze(
    client: Anthropic, region: dict, today: datetime.date, cutoff: datetime.date
) -> list[dict]:
    prompt = build_search_prompt(region, today, cutoff)
    messages: list[dict] = [{"role": "user", "content": prompt}]
    # 20250305/20250910 고정: 2026 버전(dynamic filtering)은 코드 실행
    # 컨테이너를 돌려 단순 큐레이션에 과부하 — 세그먼트 28분 실측 (2026-06-10).
    # allowed_domains 화이트리스트로 검색·fetch를 신뢰 매체로 제한 (대표 지시).
    # allowed/blocked는 동시 사용 불가 — 차단 도메인은 코드 검증에서 처리.
    # web_fetch: 검색 메타데이터에 발행일이 없는 최종 후보의 기사 페이지를
    # 직접 열어 발행일을 확인 (화이트리스트 매체 기사도 page_age 누락이 잦음).
    tools = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 8,
            "allowed_domains": region["allowed_domains"],
        },
        {
            "type": "web_fetch_20250910",
            "name": "web_fetch",
            "max_uses": 5,
            "allowed_domains": region["allowed_domains"],
            "max_content_tokens": 10000,
        },
    ]

    # 스트리밍 필수: 서버사이드 검색 루프가 길어지면 비스트리밍은 10분
    # HTTP 타임아웃 → SDK 재시도로 검색 비용만 중복 과금된다 (2026-06-10 실측).
    response = None
    extra: dict = {}
    for _ in range(3):  # pause_turn(서버 루프 한도) 연속 재개 최대 2회
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=MAX_TOKENS,
            tools=tools,
            messages=messages,
            **extra,
        ) as stream:
            response = stream.get_final_message()

        log.info("stop_reason=%s", response.stop_reason)
        if response.stop_reason != "pause_turn":
            break
        messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": response.content},
        ]
        # 코드 실행 동반 응답은 같은 컨테이너로 재개해야 함 (없으면 400)
        if getattr(response, "container", None):
            extra["container"] = response.container.id

    if response.stop_reason == "max_tokens":
        log.warning("응답이 max_tokens로 잘림 — 일부 결과만 사용")

    queries: list[str] = []
    fetches: list[str] = []
    for block in response.content:
        if getattr(block, "type", "") != "server_tool_use":
            continue
        name = getattr(block, "name", "")
        inp = getattr(block, "input", {}) or {}
        if name == "web_search":
            queries.append(inp.get("query", ""))
        elif name == "web_fetch":
            fetches.append(inp.get("url", ""))
    if queries:
        log.info(
            "검색 %d회: %s", len(queries), " | ".join(q[:40] for q in queries if q)
        )
    log.info(
        "fetch %d회%s", len(fetches),
        ": " + " | ".join(u[:60] for u in fetches) if fetches else "",
    )

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text or ""

    if not text.strip():
        log.warning("텍스트 응답 없음")
        return []

    cleaned = text.strip()
    if "```" in cleaned:
        cleaned = re.sub(r"```\w*\n?", "", cleaned)

    match = re.search(r"\[[\s\S]*\]", cleaned)
    if not match:
        log.warning("JSON 파싱 실패: %s", text[:300])
        return []

    raw_json = match.group()
    candidates = _parse_json_robust(raw_json)

    for c in candidates:
        if isinstance(c, dict):
            topics = c.get("topics", [])
            if isinstance(topics, str):
                c["topics"] = [topics]
            c["topics"] = [t for t in c.get("topics", []) if t in TOPIC_LABELS]

    result = [c for c in candidates if isinstance(c, dict)]
    if not result:
        log.warning("후보 0건 — 모델 응답 앞 2000자: %s", text[:2000])
    return result


# ── 품질 게이트 ───────────────────────────────────────────


def _host_matches(url: str, domains) -> bool:
    host = (urlparse(url).netloc or "").split(":")[0].lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _parse_date(value) -> datetime.date | None:
    if not value or not isinstance(value, str):
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# URL 경로의 날짜 패턴 — 구분자형(/2026/06/10/, 2026-06-10) 우선, 연속 8자리 폴백
URL_DATE_SEP_RE = re.compile(r"(20[12]\d)[/\-.]([01]?\d)[/\-.]([0-3]?\d)")
URL_DATE_RAW_RE = re.compile(r"(20[12]\d)([01]\d)([0-3]\d)")


def _date_from_url(url: str) -> datetime.date | None:
    """기사 URL에 박힌 발행일 추출 (한국 언론 URL 관행). 모델이 발행일을
    못 채웠을 때의 코드 레벨 폴백 — 유효하지 않은 날짜는 무시."""
    for pattern in (URL_DATE_SEP_RE, URL_DATE_RAW_RE):
        for m in pattern.finditer(url):
            try:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            if d >= datetime.date(2024, 1, 1):
                return d
    return None


def validate_candidates(
    candidates: list[dict],
    cutoff: datetime.date,
    today: datetime.date,
    allowed_domains=(),
) -> tuple[list[dict], dict]:
    """형식·출처·점수·발행일 검증. 프롬프트 지시를 코드 레벨에서 재강제한다.

    발행일 미상은 기본 제외(신뢰성 — 2026-06-10 대표 지시). 화이트리스트
    매체는 날짜 메타데이터가 대체로 깔끔하지만, 0건이 반복되면
    ALLOW_UNDATED=1로 임시 완화 가능(플래그 게재)."""
    valid: list[dict] = []
    drops = {
        "format": 0, "score": 0, "stale": 0,
        "future": 0, "blocked": 0, "no_date": 0,
    }

    for c in candidates:
        title = (c.get("title") or "").strip()
        url = (c.get("url") or "").strip()
        summary = (c.get("summary") or "").strip()

        if not title or not url.startswith("http") or not summary:
            drops["format"] += 1
            log.info("제외(필수 필드 누락): %s", (title or url)[:80])
            continue
        if _host_matches(url, BLOCKED_DOMAINS):
            drops["blocked"] += 1
            log.info("제외(차단 도메인): %s | %s", title[:60], url)
            continue
        if allowed_domains and not _host_matches(url, allowed_domains):
            drops["blocked"] += 1
            log.info("제외(화이트리스트 외 출처): %s | %s", title[:60], url)
            continue
        if not HANGUL_RE.search(summary):
            drops["format"] += 1
            log.info("제외(요약 한국어 아님): %s", title[:80])
            continue

        total = sum(int(c.get(k) or 0) for k in SCORE_KEYS)
        if total != c.get("total_score"):
            log.info("점수 재계산: %s → %d | %s", c.get("total_score"), total, title[:60])
            c["total_score"] = total
        if total < MIN_TOTAL_SCORE:
            drops["score"] += 1
            log.info("제외(점수 %d < %d): %s", total, MIN_TOTAL_SCORE, title[:80])
            continue

        pub = _parse_date(c.get("published_date"))
        if pub is None:
            pub = _date_from_url(url)
            if pub is not None:
                log.info("발행일 URL 추출(%s): %s", pub, title[:60])
        if pub is not None:
            if pub > today + datetime.timedelta(days=1):
                drops["future"] += 1
                log.info("제외(미래 발행일 %s — 할루시네이션 의심): %s", pub, title[:80])
                continue
            if pub < cutoff:
                drops["stale"] += 1
                log.info("제외(발행일 %s < 컷오프 %s): %s", pub, cutoff, title[:80])
                continue
            c["published_date"] = pub.isoformat()
        elif ALLOW_UNDATED:
            c["published_date"] = None
            log.info("발행일 미상 — 플래그로 게재 유지 (ALLOW_UNDATED): %s", title[:80])
        else:
            drops["no_date"] += 1
            log.info("제외(발행일 불명): %s", title[:80])
            continue

        c["title"], c["url"], c["summary"] = title, url, summary
        valid.append(c)

    return valid, drops


def check_url_alive(url: str) -> bool:
    """URL 생존 확인. 할루시네이션 링크(없는 도메인·404) 차단이 목적.
    봇 차단(403 등)·서버 오류·타임아웃은 실재 URL일 수 있어 통과시킨다."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    try:
        resp = requests.head(
            url, headers=headers, timeout=URL_CHECK_TIMEOUT, allow_redirects=True
        )
        if resp.status_code in (404, 405, 410):
            resp = requests.get(
                url, headers=headers, timeout=URL_CHECK_TIMEOUT,
                allow_redirects=True, stream=True,
            )
            resp.close()
        return resp.status_code not in (404, 410)
    except requests.Timeout:
        return True
    except requests.RequestException:
        return False


def select_candidates(candidates: list[dict]) -> tuple[list[dict], int, int]:
    """점수순 정렬(동점이면 발행일 확인분 우선) 후 배치 내 중복과
    죽은 링크를 걸러 상위 MAX_CANDIDATES개 선별.

    배치 내 중복: 모델 JSON이 깨져 개별 객체 폴백이 돌면 같은 기사가
    2벌씩 추출될 수 있다 (2026-06-10 실측) — URL·제목 유사도로 차단."""
    candidates.sort(
        key=lambda c: (
            c.get("total_score", 0),
            c.get("reliability", 0),
            c.get("published_date") is not None,
        ),
        reverse=True,
    )
    selected: list[dict] = []
    sel_urls: set[str] = set()
    sel_titles: list[str] = []
    dead_links = 0
    batch_dups = 0
    for c in candidates:
        if len(selected) >= MAX_CANDIDATES:
            break
        url_key = c["url"].rstrip("/").lower()
        if url_key in sel_urls or is_cross_dup(c["title"], sel_titles):
            batch_dups += 1
            log.info("제외(배치 내 중복): %s", c["title"][:60])
            continue
        if not check_url_alive(c["url"]):
            dead_links += 1
            log.info("제외(링크 불량): %s | %s", c["title"][:60], c["url"])
            continue
        selected.append(c)
        sel_urls.add(url_key)
        sel_titles.append(c["title"])
    return selected, dead_links, batch_dups


def write_step_summary(region_name: str, stats: str) -> None:
    """GitHub Actions 실행 페이지에 지역별 수집 통계 노출."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- **{region_name}**: {stats}\n")
    except OSError:
        pass


# ── 중복 제거 ─────────────────────────────────────────────


def load_seen_titles(seen_file: str) -> list[str]:
    if not os.path.exists(seen_file):
        return []
    with open(seen_file, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def is_cross_dup(title: str, seen_titles: list[str]) -> bool:
    t = title.lower()
    return any(
        SequenceMatcher(None, t, s.lower()).ratio() >= DUPLICATE_THRESHOLD
        for s in seen_titles
    )


# ── Discord ───────────────────────────────────────────────


def _topic_tags(c: dict) -> str:
    topics = c.get("topics", [])
    if not topics:
        return "—"
    return "·".join(TOPIC_LABELS.get(t, t) for t in topics)


def _score_indicators(c: dict) -> list[str]:
    indicators: list[str] = []
    if c.get("newsletter_fit", 0) > 0:
        indicators.append("소재적합")
    if c.get("carousel_fit", 0) > 0:
        indicators.append("캐러셀적합")
    if c.get("reliability", 0) > 0:
        indicators.append("출처신뢰")
    return indicators


def build_discord_message(c: dict) -> str:
    score = c.get("total_score", 0)
    badge = "🟢" if score >= 5 else "🟡"
    tags = _topic_tags(c)

    title = c["title"][:100]
    url = c.get("url", "")
    title_part = f"[**{title}**]({url})" if url else f"**{title}**"
    summary = (c.get("summary", "") or "").strip()[:500]
    source = c.get("source", "")

    msg = f"{badge} {title_part} `{tags}`"
    pub = c.get("published_date") or "발행일 미상"
    meta = " · ".join(x for x in (source, pub) if x)
    if meta:
        msg += f"\n📰 {meta}"
    if summary:
        msg += f"\n> {summary}"
    return msg[:1900]


def send_to_discord(webhook_url: str, content: str) -> None:
    payload = {"content": content[:2000], "flags": 4}
    resp = requests.post(webhook_url, json=payload, timeout=15)
    if resp.status_code not in (200, 204):
        raise RuntimeError(
            f"Discord 웹훅 실패 (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    log.info("Discord 전송 완료 (HTTP %d)", resp.status_code)


# ── 메인 ──────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Vibe Signal Collector v3 — 지역·언어 기반")
    parser.add_argument("region", choices=list(REGIONS.keys()), help="수집 지역")
    parser.add_argument("--dry-run", action="store_true", help="Discord 전송 안 함")
    args = parser.parse_args()

    region = REGIONS[args.region]
    region_name = region["name"]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    webhook_url = os.environ.get(region["webhook_env"])
    seen_file = os.environ.get("SEEN_FILE", "seen-titles.txt")

    if not api_key:
        log.error("ANTHROPIC_API_KEY 환경변수 미설정")
        return 1
    if not webhook_url and not args.dry_run:
        log.warning("%s 환경변수 미설정 — 전송 생략", region["webhook_env"])
        return 0

    client = Anthropic(api_key=api_key)
    now_kst = datetime.datetime.now(KST)
    today_date = now_kst.date()
    cutoff_date = (now_kst - datetime.timedelta(hours=MAX_AGE_HOURS)).date()
    today = today_date.isoformat()

    # 1. 웹 검색 + 분석
    log.info(
        "[%s] 웹 검색 시작 (%s) | 발행일 컷오프: %s",
        region_name, region["language"], cutoff_date,
    )
    try:
        candidates = search_and_analyze(client, region, today_date, cutoff_date)
    except Exception as exc:
        log.error("[%s] 검색 실패: %s", region_name, exc)
        write_step_summary(region_name, f"⚠️ 검색 실패: {exc}")
        # exit 1 → 워크플로 outcome=failure → notify_region_failure.py 메일 경보.
        # 후보 0건(정상)은 아래에서 return 0 — 실패와 0건을 종료코드로 구분한다.
        return 1

    collected = len(candidates)
    if not candidates:
        log.info("[%s] 후보 없음 — 전송 생략", region_name)
        write_step_summary(region_name, "후보 0건")
        return 0

    log.info("[%s] 후보 %d건 수집", region_name, collected)

    # 2. 품질 게이트 (형식·출처 화이트리스트·점수·발행일)
    candidates, drops = validate_candidates(
        candidates, cutoff_date, today_date, region["allowed_domains"]
    )

    # 3. 채널 간 중복 제거 (Supabase + 로컬 파일 병행)
    seen_titles = load_seen_titles(seen_file)
    if fetch_recent_titles:
        db_titles = fetch_recent_titles(7)
        if db_titles:
            seen_titles = list(set(seen_titles + db_titles))
            log.info("[%s] Supabase 제목 %d건 로드 (중복 제거용)", region_name, len(db_titles))
    before_dup = len(candidates)
    candidates = [c for c in candidates if not is_cross_dup(c["title"], seen_titles)]
    dup_cnt = before_dup - len(candidates)

    # 4. 점수순 정렬 → 배치 내 중복·죽은 링크 걸러 상위 N개 선별
    selected, dead_links, batch_dups = select_candidates(candidates)

    date_unknown = sum(1 for c in selected if not c.get("published_date"))
    stats = (
        f"수집 {collected} → 게재 {len(selected)}"
        f" (제외: 형식 {drops['format']} · 점수 {drops['score']}"
        f" · 기한경과 {drops['stale']} · 미래일자 {drops['future']}"
        f" · 발행일불명 {drops['no_date']} · 출처차단 {drops['blocked']}"
        f" · 중복 {dup_cnt + batch_dups} · 링크불량 {dead_links})"
    )
    if date_unknown:
        stats += f" · 발행일 미상 {date_unknown}건 포함"
    log.info("[%s] %s", region_name, stats)
    write_step_summary(region_name, stats)

    if not selected:
        log.info("[%s] 품질 게이트 통과 후보 없음 — 전송 생략", region_name)
        return 0

    # 5. 선택 로그
    for c in selected:
        indicators = _score_indicators(c)
        log.info(
            "선택: [%d지표] %s | %s",
            c.get("total_score", 0),
            c["title"][:60],
            c.get("url", ""),
        )
        log.info(
            "선택메타: %s",
            json.dumps(
                {
                    "summary": (c.get("summary") or "")[:200],
                    "indicators": indicators,
                    "topics": c.get("topics", []),
                },
                ensure_ascii=False,
            ),
        )

    if args.dry_run:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        return 0

    # 6. Discord 전송
    header = (
        f"{region['emoji']} **{region_name} Vibe | {today}**\n"
        f"-# {stats}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    send_to_discord(webhook_url, header)
    for c in selected:
        time.sleep(2)
        send_to_discord(webhook_url, build_discord_message(c))

    # 7. Supabase 저장
    if supabase_save:
        try:
            n = supabase_save(selected, args.region)
            log.info("[%s] Supabase 저장: %d건", region_name, n)
        except Exception as exc:
            log.warning("[%s] Supabase 저장 실패 (Discord 전송은 완료): %s", region_name, exc)

    # 8. seen-titles 갱신 (로컬 fallback 유지)
    with open(seen_file, "a", encoding="utf-8") as f:
        for c in selected:
            f.write(c["title"] + "\n")
    log.info(
        "[%s] seen-titles 갱신: %d건 추가",
        region_name,
        len(selected),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
