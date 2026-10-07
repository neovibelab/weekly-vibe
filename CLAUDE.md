# weekly-vibe - 일일 수집 엔진 (vibe_search v3)

> **역할**: 엔터·문화 산업 뉴스의 일일 자동 수집 엔진. 5개 검색 프로파일을 현지 발행 리듬에 맞춰 3개 시간대(오전 한국·일본 / 오후 중국·동남아 / 저녁 영어권)로 수집해 Discord 5개 채널에 알리고 Supabase `radar_items`에 적재한다. **저장되는 지역 값은 12종이고 검색 프로파일과 다른 층이다**(§1-4).
>
> **주간 브리핑(NEWSPAPER HTML) 발행은 폐기**(2026-06-09). 과거 발행물(`NEWSPAPER_*.html` · `SPECIAL_*.html` · `index.html` · `preview/`)은 역사 아카이브로만 보존하고 신규 생성하지 않는다. 제작 스킬(`.claude/skills/weekly-vibe/`)은 삭제됐다. 구 제작 가이드는 이 파일의 git 히스토리(2026-06-10 이전)에 있다.
>
> **`weeklybriefing.vercel.app` 공개 배포는 삭제**(2026-07-28 대표 결정). 삭제 대상은 Vercel 프로젝트 하나뿐이고 이 수집 엔진은 계속 돈다. (경위 → DR)
>
> **정본 관계**: 운영 워크플로(무엇을·언제·왜)는 [`OPERATING-MODEL.md`](../OPERATING-MODEL.md) §2 · 아키텍처·deprecated는 루트 [`CLAUDE.md`](../CLAUDE.md) §5 · 결정 경위·실측·사고 이력은 [`DECISION-RATIONALE.md`](DECISION-RATIONALE.md)(이하 DR). 이 파일은 매 턴 상주하므로 길이가 곧 비용이다. 여기에는 규칙만 적고, 규칙을 고치려 할 때는 DR을 먼저 읽는다.

---

## 1. 수집 파이프라인

```
격일 3개 시간대 KST (GitHub Actions ai-news-daily.yml - cron 3개 */2. vibe_search만 격일, newsletter·newsroom은 매일)
  오전 07시 한국·일본 / 오후 14시 중국·동남아 / 저녁 21시 글로벌(영어)
    → scripts/vibe_search.py (Claude Sonnet web_search, 해당 시간대 지역 순차)
    → 품질 게이트 (validate_candidates → URL 생존 확인)
    → Discord 5개 지역 채널 알림 + Supabase radar_items upsert
    → 대시보드 큐레이션: https://nvl-vibe-radar.vercel.app/
```

| 지역 | 언어 | Discord 채널 | Secret |
|------|------|-------------|--------|
| 한국 | 한국어 | `#korea_vibe` | `DISCORD_KOREA_WEBHOOK` |
| 영어권 검색 | 영어 | `#global_vibe` | `DISCORD_GLOBAL_EN_WEBHOOK` |
| 중국 | 중국어 | `#vibe-china` | `DISCORD_CHINA_WEBHOOK` |
| 일본 | 일본어 | `#vibe-japan` | `DISCORD_JAPAN_WEBHOOK` |
| 동남아 | 영어+현지 | `#asia_vibe` | `DISCORD_SOUTHEAST_ASIA_WEBHOOK` |

- **엔진**: `scripts/vibe_search.py` - Claude Sonnet `web_search` 서버사이드 도구(스트리밍 호출). 지역당 1~5건, 기준 충족 후보 없으면 그날은 생략. 단일 워크플로에서 각 step `if`가 `github.event.schedule`·수동 region input으로 분기하고, skip 지역은 outcome=skipped라 실패 경보에 걸리지 않는다. (시간대 분산·격일 전환 경위 → DR)
- **검색 예산** (2026-09-18 조임): `web_search` 5회 · `web_fetch` 3회 · fetch 본문 3,000토큰. 환경변수 `VS_SEARCH_USES`·`VS_FETCH_USES`·`VS_FETCH_TOKENS`로 조정한다. **서버사이드 검색은 검색할 때마다 앞선 결과를 다시 읽어 입력이 누적으로 자란다** - 검색 n회면 대략 `nB + (n-1)n/2 x S`. 9월 실측 = 검색 288회에 입력 1,431만 토큰(검색 1회당 49,700), 월 비용의 32%. 모델은 허용 8회 중 6회를 썼다. **되돌림 조건** - 지역당 후보가 2주 연속 평균 1건 아래면 `VS_SEARCH_USES=6`으로 되돌린다.
- **나이컷**: 한·글·일 120h, 중·동남아 168h(`MAX_AGE_HOURS`). robots.txt로 막힌 일간지(조선·중앙·FT·Reuters 등)는 뉴스레터 구독으로 흡수한다 - 발신자만 `sources_newsletters.json`에 추가.
- **적재**: `scripts/supabase_writer.py` - REST API upsert → `radar_items`. env `SUPABASE_URL`·`SUPABASE_KEY`(GitHub Secrets). nvl-vibe-radar 자체 수집기는 폐기됐다. 풀을 채우는 수집기는 vibe_search(웹)·newsletter_ingest(§1-1)·newsroom_ingest(§1-2)·interview_ingest(§1-3)·gnews_ingest(구글 뉴스 RSS) 다섯이고, radar는 조회·큐레이션 대시보드(collector/region 필터)다.
- **풀 유지보수**: `scripts/pool_maintenance.py`. 매일 실행, `--apply` 없으면 미리보기만.
  - **상한** - collector별 pending 상한(gnews 140 · feed 30 · interview 200 · 그 외 50). 자르는 순서는 **시제 우선**(바이브 > 시그널 > 미판정 > 배경), 같은 층에서 최신순. created_at만 보면 5일 된 바이브가 오늘 들어온 단신에 밀린다.
  - **시제 시효** - 바이브 14일 · 시그널 7일 · 뉴스 3일. **미판정은 시효 없음**, 인터뷰는 collector 통째로 면제.
  - **픽 시효** - 20일(`picked_expiry_targets`, 인터뷰 픽 면제). 픽 버튼은 2026-09-10에 없앴고 남은 2건을 이 규칙이 정리한다.
  - **면제** - 살아있는 타깃(`aims` open·drafting)의 근거 신호는 상한·시제 시효에서 뺀다. 타깃을 세워뒀는데 근거가 사라지면 2 팩트시트가 빈손으로 시작한다.
  - **묶음 로직은 2026-09-10 제거** - 정리 ③(묶음 시의성 시효)·픽 시효의 보호 묶음 면제·상한 면제의 묶음 멤버. 묶음이 09-02 폐기라 셋 다 매 런 `clusters`·`cluster_items`를 조회하고 빈 목록을 받아 왔다.
  - Supabase 전 행 조회는 반드시 `_fetch_paged`(Range 헤더 + id 정렬 순회) - PostgREST는 `limit`과 무관하게 1,000행에서 자른다. (경위 → DR)
- **중복 제거**: `seen-titles.txt` + Supabase URL 중복 체크.
- **질의별 수확 보고** (2026-10-08 신설): gnews 적재 시 `tags`에 `q:<질의이름>`을 남기고 `scripts/query_yield.py`가 질의별 **수확 -> 통과 -> 좌표 -> 타깃** 깔때기를 집계한다. **판단은 좌표 열로 한다** - 수확 100건에 좌표 0이면 유행어를 긁고 있다는 뜻이다(오대산 문화축전이 「AI 음악」에 걸린 양상). 수확 20건 이상에 좌표 0이면 「죽었다」, 30건 이상에 5% 미만이면 「묽다」로 지목한다. **질의를 고치려면 이걸 먼저 돌린다.** 기준선 = 2026-10-08 전체 좌표율 41.2%(4,605건). `q:` 태그 이전 적재분은 「(태그 없음)」으로 묶인다. **되돌림** - 6개월 돌려 지목된 질의가 하나도 없으면 보고를 접는다(질의 집합이 안 썩는다는 뜻).
- **행위자 축** (2026-10-08 신설): 사건은 추상 주제어가 아니라 **당사자명과 업계 관용어**로 보도된다. 다만 **회사명을 그대로 넣으면 터진다** - 룩백 3일 신규가 티빙 86 · JYP 85 · YG 82 · SM 66 · 하이브 57건이고 9개를 넣으면 한국 수집이 63 -> 516건(+719%)이다(피드가 컴백·직캠·특징주로 찬다). 그래서 둘로 나눴다.
  - **질의로 넣은 것** = 관용어 2종(`엔터 지분 투자`·`기획사 계약` - 행위자를 열거하지 않고 같은 일을 한다. 신세계의 워너브라더스 1.4조 지분투자를 잡았다) + 법인형 행위자 3종(`카카오엔터테인먼트`·`CJ ENM`·`네이버웹툰` - 한계 수확 40·22·4건으로 작고 활동·주가 잡음이 적다). 합계 **69건/룩백 = 23건/일**, 전체 gnews 140건/일 대비 +16%. 풀 상한은 시제 우선으로 자르니 이 단신들이 먼저 밀린다.
  - **행위자 블록은 `QUERIES` 맨 끝에 둔다.** 앞 질의가 URL을 선점하므로 그 줄들의 「신규」가 곧 한계 기여다. **순서를 바꾸면 그 측정이 깨진다.**
  - **넣지 않은 행위자는 `scripts/actor_watch.py`가 본다.** 수집하지 않고 「구글 뉴스에 있는데 우리 풀에 한 번도 안 들어온 것」만 제목으로 보고한다(RSS만·LLM 없음·적재 없음). 신뢰 문제의 본질은 빠진 것 자체가 아니라 **빠진 걸 모르는 상태**라서다. 사건어로 추려 보여주고 **겹침(자카드)으로 사건을 접는다** - 국감 하나가 43개 매체로 보도되면 43건이 아니라 1사건이다. **이 수치로 비율을 계산하지 않는다**(사람이 제목을 보는 전제). 2026-10-08 첫 실행에서 문체위·과방위 국감(JYP 패노메논 상표권 · 티빙 해킹 보상 · YG 저작권 무혐의)이 통째로 사각지대였다.
  - 「멜론」은 행위자로 쓰지 않는다 - 고창멜론연합회 농업마이스터가 섞인다(동음이의어 실측).
  - **되돌림** - `query_yield.py`에서 4주 뒤 행위자 3종의 좌표율이 기준선(41.2%) 절반 아래면 뺀다. `actor_watch`가 6주 연속 사각지대 0이면 접는다.
- **검수 적재** (2026-10-08 신설): `scripts/radar_audit.py`가 위 측정기들을 불러 **다섯 숫자를 `radar-audit-log.md`에 한 줄**로 쌓는다(질의·수집/일·좌표율·지목·못본사건·발행일치). 같은 날 행은 덮어쓴다. **측정을 새로 만들지 않고 있는 측정기를 불러 쓴다** - `scripts/../../scripts/diff-harvest.py`와 같은 방침이고, 그쪽 머리말이 「측정기는 있었고 빠진 것은 적재뿐」이라는 기록이다.
  - **쌍으로 읽는다.** 「못본사건」만 보면 틀린다 - 질의를 늘리면 사각지대는 줄고 수집량이 같이 오른다. **못본사건이 내려가면서 수집/일이 안 오르는 것**이 개선이다.
  - **좌표율은 순환한다.** 우리 판정자(haiku)의 의견이라 질의가 그쪽으로 수렴할 수 있다. 끊는 외부 기준이 **발행일치**(발행본 인용 URL ∩ 레이더 보유)다. 둘이 반대로 가면 판정자를 의심한다. 라이브 풀의 41%가 구글 뉴스 리다이렉트라 발행일치의 상한이 낮은 것을 알고 본다.
  - 기준선 2026-10-08 = 질의 40 · 수집 112건/일 · 좌표율 36.0% · 못본사건 102 · 발행일치 14/445(3.1%).
  - **되돌림** - 8주 쌓아 다섯 숫자가 모두 평평하면(추세 없음) 로그를 접고 수동 점검으로 돌린다.
- **태깅**: 7렌즈 멀티태깅(`fan-behavior` `consumer-behavior` `ent-deals` `ip-business` `artist-ownership` `tech-issues` `taste-values`, `topics` 배열). **`cross-industry` 태그는 만들지 않는다**(대표 결정) - 레퍼런스는 일부 신호의 속성이 아니라 전 콘텐츠의 해석 렌즈다. 타 업종 이전 원리 판정은 대시보드 보조·추천 프롬프트(nvl-vibe-radar `REF_FRAME`)가 한다. `taste-values` = 세대를 가로지르는 취향·가치 신호(지속가능·로컬·디깅·리바이벌·취향 공동체, 엔터 밖 패션·뷰티·F&B·여행·리테일 포함). 구 `gen-z-lifestyle`(Z세대 인구통계 축)의 재정의. **키 동기화 필수** - 같은 풀(`radar_items.topics`)을 쓰는 `newsletter_ingest.py`·`newsroom_ingest.py`의 `TOPIC_KEYS`, `nvl-vibe-radar`(`app.py` VALID_TOPICS·`dashboard.html` 필터/TOPICS/CROSS_CUL)도 함께 바꾼다. 대시보드는 과거 `gen-z-lifestyle`을 alias로 호환(마이그레이션 불필요). (경위 → DR)
- **출력 언어**: 모든 외국어 기사 제목은 한국어 번역. **LLM 응답 JSON 파싱은 `scripts/llm_json.py` 하나로 모았다**(2026-09-10) - `parse_obj`(코드펜스 제거 → 첫 균형 블록 → 키별 정규식 3층, 실패 시 예외) · `parse_list`(원본 → 수리 → 개별 객체 추출 3단, 구 `_parse_json_robust`). **`nvl-vibe-radar/llm_json.py`와 쌍둥이다** - 두 저장소는 서로 import할 수 없으니 한쪽을 고치면 반드시 다른 쪽도 고친다.
- **개별 테스트**: `ai-news-daily.yml`의 `workflow_dispatch` region input(all/korea/global-en/china/japan/southeast-asia). 이건 **검색 프로파일**이지 저장 지역이 아니다(아래 §1-4).
- **실패 경보**: 지역 스텝이 검색 실패(web_search API·코드 에러)로 끝나면 `scripts/notify_region_failure.py`가 **잡을 실패시킨다**(`::error::` + exit 1). **메일 경보는 2026-09-14 대표 지시로 폐기** - 「[NVL] 경보 메일이 불필요하다」. GitHub Actions가 자기 알림을 보내므로 추가 채널·시크릿이 필요 없다. **0건(정상)은 exit 0이라 안 잡힌다** - 침묵 실패만 본다. **되돌림** - 잡 실패가 눈에 안 띄어 침묵 실패가 또 늦게 발견되면 Discord 경보 전용 웹훅을 세운다.

**§1-1~1-3 공통**: Discord 미포스팅·대시보드 전용, `total_score=0`(사전 큐레이션 소스), 대시보드에 출처 배지. 시크릿은 `SUPABASE_*` + ANTHROPIC `ANTHROPIC_API_KEY_WEEKLY_BRIEFING` 재사용(피드는 무인증이라 신규 시크릿 없음). 피드 URL은 **실제 fetch로 유효 XML을 검증한 뒤 등재**(죽은 피드, 헤더만 주고 본문이 빈 깡통[예: Sanrio] 주의), `_` 접두 = 비활성. 소스 추가·제거는 각 JSON만 편집.

## 1-1. 뉴스레터 수집기 (collector='newsletter')

vibe_search와 같은 풀(`radar_items`)을 공유하는 두 번째 수집기. 대표의 뉴스레터·AI서비스 전용 계정(tmifmdj@gmail.com)으로 구독하는 정예 뉴스레터를 소스 풀에 합친다.

- **엔진**: `scripts/newsletter_ingest.py` - Gmail IMAP 앱비밀번호(stdlib `imaplib`, OAuth·검증 불필요) → allowlist 발신자의 최근 메일 → 본문 추출·추적URL 복원(base64 경로 디코드) → Claude haiku 분류(7렌즈 topics + 한국어 요약) → upsert. 지역은 발신자별 고정 힌트(본문 분류 비의존).
- **allowlist**: `sources_newsletters.json` - 발신자 47곳 = 엔터·문화·소비 직결 35 + `broad` 12(일반·테크·비즈 종합 매체). `broad:true` 소스는 `classify`가 is_entertainment 게이트를 넓혀 다른 영역의 교차 신호까지 채택(순수 하드뉴스만 제외) - "음악·엔터 밖에서 교차성·인사이트 발굴" 대표 지시. 고신호는 **발신자**로 유지한다(받은편지함 대부분이 노이즈라 탭 통째 수집 안 함). (스윕 이력·보류 발신자 → DR)
- **스케줄**: `.github/workflows/newsletter-ingest.yml` 하루 2회 - **09:30 KST**(아침 클러스터) + **23:00 KST**(저녁 클러스터) + `workflow_dispatch`(lookback_days). lookback 2일 + URL dedup이라 2회가 겹쳐도 안전.
- **시크릿**: `GMAIL_USER`·`GMAIL_APP_PASS`(IMAP 앱비밀번호).
- **중복 제거**: 최근 14일 newsletter URL 집합(Supabase 조회) + URL upsert(merge-duplicates).
- **캐치올 제목 게이트**(대표 지시 "발신자가 아니라 제목 기준으로도"): allowlist 밖 발신자의 메일도 제목이 엔터·콘텐츠·미디어·문화·소비 신호면 수집한다. 2단 게이트 - ① 런당 haiku 1콜로 제목 배치 판정(`subject_gate`, 프로모·행사·계정알림·하드뉴스 제외, 애매하면 제외) ② 통과분만 본문 fetch·strict classify(비-broad 규칙). 런당 상한 `NL_CATCHALL_CAP`(기본 8), `NL_CATCHALL=0`으로 끔. `catchall_ignore`(JSON 최상위 키) = 트랜잭션·서비스 알림 제외 목록 - **편집 매체는 넣지 않는다**(제목 게이트가 건별 판정). 캐치올 수집분의 source=발신 도메인.

## 1-2. 뉴스룸 수집기 (collector='newsroom')

같은 풀을 공유하는 세 번째 수집기. 주요 엔터·미디어·IP홀더 기업의 뉴스룸/블로그 RSS·Atom 피드에서 1차 발표를 가져온다.

- **엔진**: `scripts/newsroom_ingest.py` - RSS(`item`)·Atom(`entry`) 피드 fetch(stdlib `xml.etree`, 외부 feedparser 불필요) → 룩백 내 항목 → Claude haiku 분류(7렌즈 + 한국어 요약) → upsert. 지역은 소스별 고정 힌트.
- **allowlist**: `sources_newsrooms.json` - 15소스. IP홀더·플랫폼(Disney·Netflix·Apple·Spotify·YouTube·UMG·WMG·Sony Music·Toei) + 아카이브·연구(Speakola·Geena Davis Institute) + **차트메트릭 2종**(Blog·Flow Insights, 2026-09-10 등재) + **한국 산업 매체 2종**(테크M·바이라인네트워크, 2026-10-08 등재).
  - **한국 기업 뉴스룸은 넣을 수 없다**(2026-10-08 실측). 카카오엔터·CJ ENM·네이버웹툰·지니뮤직·스튜디오드래곤은 단일 페이지 앱이라 표준 경로에 피드가 없고 두 곳의 `sitemap.xml`은 내비게이션 페이지만 담는다. 하이브는 403. 기관(KOCCA·문체부·저작권위)도 자체 RSS가 없고 네이버 블로그 RSS는 전시·이벤트 홍보다. 집계처 뉴스와이어는 피드가 있으나(`api.newswire.co.kr/rss/all`) B2B 중소기업 보도자료다. 대신 **니치 산업 매체**를 쓴다 - 가십 섹션이 없어 전체 피드가 곧 산업 피드이고 링크가 원문이다. 뺀 것 = 전자신문 방송통신(「[ET포토] 세븐틴」 연작이 피드를 덮는다) · 한국경제 엔터(가십) · 아웃스탠딩(유료 장벽) · 지디넷·블로터(일반 IT·분양). **되돌림** - 두 매체 수집분이 4주간 풀에 한 건도 안 남으면 뺀다.
- **소스 유형 둘** - 기본은 RSS·Atom 피드다. **피드가 없는 곳은 `type: "sitemap"`**으로 받는다(2026-09-10 신설). `sitemap.xml`의 `<loc>`을 `path_prefix`로 거른 뒤 각 기사 페이지에서 프리렌더된 `<title>`과 meta description을 뽑고, 그다음(게이트·번역·적재·중복제거)은 피드 경로와 같다. **사이트맵의 `lastmod`는 안 본다** - 매일 다시 찍혀 전건이 오늘로 나온다(실측). 발행일이 없으므로 최초 발견일을 쓰고, 30일 URL 중복 제거가 한 기사를 한 번만 들인다.
  - 계기 = 차트메트릭 플로우. 단일 페이지 앱이라 `/rss`·`/feed`·`/rss.xml`이 전부 앱 껍데기를 200으로 돌려준다. **200이 곧 유효 피드가 아니다** - 등재 전 본문이 XML인지 본다.
- **차단 도메인은 `allowed_domains`에 넣을 수 없다.** Anthropic 크롤러 차단 도메인이 하나만 끼어도 web_search API가 요청 전체를 400으로 거부한다. 추가하려면 `probe_domains.py`로 사전 검증 후 통과분만. 피드 있는 IP홀더(Sony Music 등)는 newsroom 수집기가 흡수하고, **피드 없는 곳(Sony 그룹·Nintendo·Bandai·Crunchyroll·WBD·NBCU·Paramount)은 `vibe_search.py` `search_terms`에 회사 키워드로 흡수**한다(global-en ent-deals·ip-business + japan ip-business). 키워드는 400에 안전하고, 신뢰 매체의 해당 기업 보도(분석·딜)를 찾는다. (사고 경위 → DR)
- **스케줄**: `.github/workflows/newsroom-ingest.yml` 매일 **10:00 KST**(01:00 UTC) + `workflow_dispatch`(lookback_days). **lookback 7일**.
- **중복 제거**: 최근 30일 newsroom URL 집합(Supabase 조회) + URL upsert.

## 1-3. 인터뷰 수집기 (collector='interview')

같은 풀을 공유하는 수집기 다섯 중 하나. 국내외 아티스트·창작자 인터뷰(텍스트·영상)를 모아 대시보드 인터뷰 탭에 노출한다. 용처 = @nvl.seoul "insight/quote/reels" 소재 파이프라인 + Icon Lab 인물 발굴 레이더.

- **엔진**: `scripts/interview_ingest.py` - 매체 RSS·유튜브 채널 RSS(`videos.xml?channel_id=UC…`) fetch(stdlib `xml.etree`) → Claude haiku 분류 → **is_interview=true만** upsert. YouTube Atom의 `<media:group>` 중첩 제목·설명도 파싱(newsroom 파서 확장). 지역은 소스 고정 힌트(분류 값 우선).
- **분류 게이트**: haiku가 두 축을 따로 판정하고 **둘 다 true여야 적재**한다. ① `is_interview`(형태 - 아티스트 본인 발화 중심만 true. 뉴스·리뷰·차트·퍼포먼스 단독·MV·리스트는 false, 애매하면 false = 정밀 우선) ② `is_music_ent`(주제 - 발화 내용이 음악·엔터 창작이나 그 산업이면 true. 연애·육아·건강·심리·창업·테크·정치는 false. **말하는 사람이 아티스트여도 주제가 음악·엔터가 아니면 false**). 모델이 `is_music_ent` 키를 빠뜨리면 통과시킨다. 그 밖에 `person_ko`(주 인물 한국어 표기)·title_ko/summary_ko/region. summary는 "인물 - 요지" 관례. `is_interview=false`·분류실패는 `filtered_out`(verdict `not_interview`/`classify_failed`)로 적재해 풀·인터뷰탭에서 숨긴다. (주제 축 신설 경위 → DR)
- **allowlist**: `sources_interviews.json` - 30소스 중 활성 8(영상 7 + 텍스트 1, 2026-09-02 대표 지시). 남긴 기준 = 실적 → 영상 → 음악·창작 주제 → 국내 1 → 텍스트 1. **뺀 사유는 각 항목 `_reason`에 적는다**(되살릴 때 왜 뺐는지 보이게). 유입이 부족해지면 FADER·GRAMMYS부터 되살린다. 각 소스 `media`("text"|"video") 힌트 → 대시보드 `tags`. (축소 근거·실측 → DR)
- **스케줄**: `.github/workflows/interview-ingest.yml` **화·금 11:00 KST**(02:00 UTC) + `workflow_dispatch`(lookback_days). 주 2회, **룩백 14일·나이컷 없음**. 대시보드 인터뷰 탭 노출. GitHub 신규 예약 워크플로는 첫 예정 발화를 스킵한다 - 첫 자동 수집이 안 보이면 `workflow_dispatch` 1회 수동.
- **중복 제거**: 최근 **60일** interview URL 집합 + URL upsert(merge-duplicates).
- **픽 시효 면제**: `pool_maintenance.py`의 픽 20일 시효(`picked_expiry_targets`)에서 `collector='interview'` 픽은 면제(소스 뱅크 이관 전까지 보존). 뉴스성 픽에만 20일 적용.
- **쓰는 자리는 대시보드 탭이 아니라 digest다** (2026-10-08 대표). 「다 보고 들을 시간이 없다」 · 「당사자 발언이란 점에서 중요하게 관리하고 싶다」. 2,206건을 카드로 쌓아 타깃 0건·발행 인용 1건이었다 - **팟캐스트는 훑는 물건이 아니라 듣는 물건**이라 카드 목록이 맞지 않았다. `../scripts/podcast-digest.py`가 자동자막(yt-dlp, 위스퍼 불필요)으로 전사하고 haiku로 회차마다 **요지 3줄 + 당사자 발언 3\~5개(타임코드)**를 뽑아 두뇌 `raw/library/podcast/`에 채널별로 쌓는다. 주 23회차 = 듣는 데 17시간, 읽는 데 5분. 비용은 전사 0 + digest 주 $0.23.
  - **채널마다 뽑을 것이 다르다** - `_strength` 칸이 그걸 적고 digest 프롬프트가 읽는다(Trapital 자본·카탈로그 · Music Tectonics 산업 구조 · And The Writer Is 창작과 업계 작동 · Zach Sang 아티스트 발언).
  - **유튜브 채널 피드는 쇼츠·클립이 다수다**(2026-10-08 길이 표본 - 21편 중 0\~3분이 다수). 길이 문턱 12분이 메타만 보고 걸러 API를 안 쓴다. **근본 해법은 팟캐스트 RSS를 따로 붙이는 것**이고 추측 주소 6개가 전부 404라 쇼별 조회가 필요하다(미착수).
  - **검증 층을 겹치지 않는다** - 위키 `status`(미검증→검증)는 **관점의 검증**이고, 당사자 발언의 검증은 「실제로 말했나」다. 후자는 타임코드·전사본이 보증한다. 그래서 적재물은 `citation_ready: false`에 **verbatim 직접 인용 금지**, 패러프레이즈는 출처 표기 후 자유. 선례 = `raw/library/2026-07-11-steve-stoute-unitedmasters.md`.
  - **되돌림** - 8주 동안 digest가 팩트시트·발행에 한 번도 안 쓰이면 수집을 접는다.

## 1-4. 지역 축 (2026-09-10 개편)

**검색 프로파일 5종과 저장 지역 12종은 다른 층이다.** 프로파일은 「어느 언어로 검색해 어느 디스코드 채널에 알리나」이고, 저장 지역은 「그 기사가 다루는 시장이 어디인가」다.

- **저장 값 12종** (`radar_items.region`): `korea` `japan` `china` `southeast-asia` `north-america`(미국·캐나다) `europe`(영국·독일·프랑스·북유럽·동유럽) `latin`(스페인어권·브라질) `mena`(중동·북아프리카) `africa-ssa`(사하라이남) `india-sa`(인도·남아시아) `oceania`(호주·뉴질랜드) `multinational`(특정 국가 귀속 없는 다국적 발표·업계 일반론·글로벌 통계).
- **`global-en`은 신규 저장하지 않는다.** 과거 archived 행이 수천 건이라 라벨 맵에만 「글로벌(구)」로 남는다. 분류가 실패했을 때 소스 힌트로 남는 값도 이 레거시이고, **12종 중 하나를 억지로 찍지 않는다** - `backfill_region.py`가 나중에 내용 기준으로 다시 판정한다.
- **분류 기준(모든 프롬프트 공통 문구)**: 매체 국적이나 기업 본사가 아니라 **기사 내용의 시장**. 여러 시장이면 비중이 큰 쪽 하나. 어느 나라에도 귀속되지 않는 다국적 발표·업계 일반론만 `multinational`이고, **모르겠다고 여기 넣지 않는다.** 각 수집기의 `REGION_GUIDE` 상수가 같은 문장을 쓴다 - 하나를 고치면 다섯을 같이 고친다(`newsletter_ingest`·`newsroom_ingest`·`interview_ingest`·`backfill_region`·`gnews_ingest`).
- **검색 프로파일 5종**은 그대로다(`vibe_search.py` REGIONS 키 = cron 슬롯·webhook·allowed_domains). `global-en` 프로파일 이름만 「영어권 검색」으로 바꿨다. 그 결과의 region은 저장 시점엔 미판정이고 `backfill_region.py --collectors vibe_search`가 재판정한다.
- **gnews는 gl 매핑을 버렸다.** 구글 뉴스 에디션은 독자의 위치지 기사의 시장이 아니다. 지금은 엔터 게이트 haiku가 `region`을 함께 판정하고(**호출 수 불변**), 판정이 없을 때만 gl 표로 떨어진다.
- 라벨은 `supabase_writer.py` `REGION_LABELS` = `send_report_drop.py` `_REGION_LABELS` 두 곳이 같은 문자열을 쓴다.

## 1-5. 판정이 막혔을 때 (2026-09-10 신설)

Anthropic 계정의 지출 한도에 걸리면 게이트 haiku가 전건 400으로 죽는다.
수집은 RSS라 공짜인데 룩백이 2~3일뿐이라 그냥 멈추면 그 사이 기사를 영영 못 줍는다.

- **수집은 계속한다.** `gnews_ingest.py --no-gate`(워크플로 `no_gate` 입력)는 판정을
  건너뛰고 원문 제목 그대로 적재한다. 한도 오류는 자동 감지해 같은 자리로 떨어뜨리므로
  사람이 안 봐도 데이터는 남는다.
- **보류와 실패를 가른다.** `filter_verdict='gate_deferred'`는 「판정을 미뤘다」이고
  `classify_failed`는 「판정하려다 실패」다. 둘 다 `filtered_out`이라 풀에 안 뜬다.
- **한도가 풀리면 `scripts/regate.py`.** 두 verdict를 찾아 게이트에 다시 태운다.
  게이트 로직은 `gnews_ingest`에서 import한다 - 두 벌이 되면 반드시 어긋난다.
  `status`가 `pending`·`filtered_out`인 것만 건드리고 사람이 손댄 상태는 그대로 둔다.
- **표기만 고칠 때는 `scripts/fix_title_notation.py`.** `backfill_translate.py`는
  뉴스룸·뉴스레터 전용이고 `summary`를 덮어쓴다. 구글 뉴스 카드의 `summary`에는
  시제 판정의 `why`가 들어 있어 덮으면 카드 한 줄이 사라진다.

## 2. 품질 게이트

Anthropic `web_search` 도구에 날짜 필터 파라미터가 없어 코드 레벨로 강제한다.

1. 프롬프트에 오늘 날짜(KST)+컷오프 주입, `published_date` 필드 요구
2. **출처 화이트리스트** - 지역별 `allowed_domains`(주요 일간지·주간지·매거진·전문지)로 web_search 검색 자체를 제한 + 코드 검증에서 목록 외 출처 제외(보도자료 재가공 매체 차단, 대표 지시). `BLOCKED_DOMAINS`(나무위키)는 별개 방어선
3. `validate_candidates()` - 필수 필드·한국어 요약·**4지표 점수 재계산(≥4)**·발행일 48시간 컷(`MAX_AGE_HOURS` env로 조정, 지역별 값은 §1). 점수 4지표 = 소재적합·캐러셀적합·출처신뢰·교차정체성(각 0~2, 만점 8). 임계 `MIN_TOTAL_SCORE` 기본 4(만점의 50%, env 조정 - 0건 반복 시 3으로 완화). **발행일 미상은 제외**(신뢰성, 대표 지시). 0건이 반복되면 `ALLOW_UNDATED=1`로 임시 완화(플래그 게재). `radar_items`에 `cross_identity` 개별 컬럼은 없다(total_score에 합산만, 개별 표시가 필요하면 마이그레이션 후속)
4. 점수순 정렬(동점 시 reliability→발행일 확인분 우선) → 배치 내 중복 제거 → **도메인당 2건 상한**(`MAX_PER_DOMAIN`, 1차 패스로 한 매체 독식 방지. 미달 시 2차 패스에서 상한을 풀어 건수 보존) → URL 생존 확인(`check_url_alive`, 404/없는 도메인 차단) → 최대 5건
5. 드롭 통계를 Discord 헤더 subtext + GitHub Actions Step Summary에 노출

단위 테스트: `scripts/test_quality_gate.py`. (도입 경위·4지표 개편 → DR)

## 3. 절대 금지 (위반 시 작업 중단)

- **기억·지식 기반으로 뉴스를 만들어내지 않는다.** 훈련 데이터의 사실, 그럴듯한 추정, 생성된 가짜 URL 일체 금지.
- 모든 기사는 **세션 내 검색·fetch로 직접 확인한 것**만. URL 검증 불가능하면 제외하고, 건수가 부족해도 채우지 않는다.
- "지식 기반 소스 활용", "URL 검증 생략" 같은 판단을 스스로 내리지 않는다.

## 4. 주간 리포트 드롭 (별개 흐름, 존속)

뉴타입컬처클럽 자료실용 산업 리포트 큐레이션. 일일 뉴스 수집과 별개.

- 생성: `/report-scan` 스킬(미네바) - **격주(2주 1회) 운영**(대표 결정). 4언어 검증 → `drops/YY.MM.DD-주간리포트드롭.md` 2곳 저장(weekly-vibe/drops/ + ecri-ceo-staff/operations/) + 마스터 색인 반영(색인이 SSOT)
- 발송 로직: `scripts/send_report_drop.py` - 최신 드롭 찾기·정제(HTML주석 제거·2000자 컷)·Discord 전송. 정시·백업 공용 모듈(stdlib). 워크플로 YAML 안에 heredoc으로 로직을 넣지 않는다. 세 가지 필수 - **① 명시 User-Agent**(urllib 기본 UA는 Discord Cloudflare가 403/`error 1010`으로 차단. vibe_search `send_to_discord`는 `requests` UA로 통과 중이라 명시 UA는 후속 권장·미적용) **② 격주 중복방지: 드롭이 `DROP_MAX_AGE_DAYS`(기본 7)일 이상 지났으면 발송 생략**(`return 0` → 정시 워크플로 success 유지 → watchdog 오경보 없음) **③ 대시보드 적재: 발송 직후 드롭의 1위 메달+신규 리포트를 파싱(`parse_drop_items`)해 `radar_items`에 `collector='newsroom'`으로 적재 → 대시보드 뉴스룸 탭**(다시보기=기보유는 제외, URL 중복 merge-duplicates, `SUPABASE_URL/KEY` env를 정시+백업 워크플로 양쪽에 주입). (403 원인·경계값 버그 → DR)
- 포스팅(정시): `.github/workflows/discord-report-drop.yml` - 매주 월요일 **10:17 KST** cron. 실제 발송은 위 신선도 가드로 **새 드롭 있을 때만 = 격주 리듬**(cron은 매주지만 stale 드롭은 재발송하지 않는다).
- 백업 감시: `.github/workflows/report-drop-watchdog.yml` - 월 **10:40 KST** 점검 -> 정시 누락 시 직접 재발송(`check_drop_posted.py` 발송 판정). **메일 알림은 2026-09-14 폐기**(`send_drop_alert.py` 삭제) - 백업이 성공하면 드롭은 정상 전송된 것이라 알릴 것이 없고, 실패하면 「결과 판정」 스텝이 `::error::` + exit 1로 잡을 빨갛게 만든다. GitHub cron 신뢰성 보완.

## 5. 파일 구조

```
weekly-vibe/
├── CLAUDE.md                    ← 이 파일 (규칙만)
├── DECISION-RATIONALE.md        ← 결정 경위·실측·사고 이력 (규칙 수정 전 필독)
├── scripts/
│   ├── vibe_search.py           ← 수집 엔진 v3 (5지역)
│   ├── llm_json.py              ← LLM 응답 JSON 3층 파싱 (nvl-vibe-radar/llm_json.py와 쌍둥이)
│   ├── supabase_writer.py       ← radar_items upsert
│   ├── send_report_drop.py      ← 리포트 드롭 발송 공용 모듈 (정시+백업)
│   ├── check_drop_posted.py     ← 백업: 오늘 발송 여부 판정 (gh 런 이력)
│   ├── notify_region_failure.py ← 지역 검색 실패 시 잡 실패(::error::)
│   ├── newsletter_ingest.py     ← 뉴스레터 IMAP 수집기 (§1-1)
│   ├── newsroom_ingest.py       ← 뉴스룸 RSS 수집기 (§1-2)
│   ├── interview_ingest.py      ← 인터뷰 RSS·유튜브 수집기 (§1-3)
│   ├── gnews_ingest.py          ← 구글 뉴스 RSS 수집기 (collector='gnews')
│   ├── query_yield.py             ← 질의별 수확 깔때기 보고(죽은 질의 지목)
│   ├── actor_watch.py             ← 행위자 사각지대 감시(수집 없이 「못 본 사건」만)
│   ├── radar_audit.py             ← 검수 5숫자를 radar-audit-log.md에 한 줄로 적재
│   ├── regate.py                ← 보류·실패한 gnews 수집분 일괄 재판정 (§1-5)
│   ├── fix_title_notation.py    ← 적재된 제목의 표기만 정규화 (§1-5)
│   ├── pool_maintenance.py      ← 풀 유지보수(상한 archive + 픽 시효 + 묶음 시의성 시효)
│   ├── probe_domains.py         ← allowed_domains 후보 사전 검증
│   └── test_quality_gate.py     ← 품질 게이트 단위 테스트
├── sources_newsletters.json     ← 뉴스레터 발신자 allowlist
├── sources_newsrooms.json       ← 뉴스룸 피드 allowlist
├── sources_interviews.json      ← 인터뷰 피드·채널 allowlist
├── .github/workflows/
│   ├── ai-news-daily.yml        ← 격일 3시간대 수집
│   ├── newsletter-ingest.yml    ← 매일 09:30·23:00 KST
│   ├── newsletter-sender-scan.yml ← 월 09:00 KST
│   ├── newsroom-ingest.yml      ← 매일 10:00 KST
│   ├── interview-ingest.yml     ← 화·금 11:00 KST
│   ├── gnews-ingest.yml         ← 매일
│   ├── discord-report-drop.yml  ← 월 10:17 KST 정시 리포트 드롭
│   └── report-drop-watchdog.yml ← 월 10:40 KST 백업(누락 시 재발송+메일)
├── drops/                       ← 주간 리포트 드롭 마크다운
├── seen-titles.txt              ← 중복 제거 캐시
└── NEWSPAPER_*.html 등          ← 구 주간 브리핑 발행물 (역사 아카이브, 신규 생성 금지)
```

## 6. 변경 이력

날짜별 변경 이력과 근거는 [`DECISION-RATIONALE.md`](DECISION-RATIONALE.md) §6으로 옮겼다(2026-09-04). 여기에는 더 쌓지 않는다 - 새 변경은 DR에 날짜·근거와 함께 적고, 이 파일에는 바뀐 규칙만 반영한다.
