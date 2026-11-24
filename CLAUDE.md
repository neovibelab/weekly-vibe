# weekly-vibe - 일일 수집 엔진 (vibe_search v3)

> **2026-11-16에 비웠다** (대표 지시 - 「weekly-vibe 비우기」). 39,929바이트였다.
>
> **가른 축은 「돌리는 법인가 설계 서술인가」다.** 이 저장소는 **도는 소프트웨어**라 남은 쪽이
> 많다 - 스크립트 이름 · cron 시각 · env · 저장 값 · 동기화해야 하는 사본 자리 · 복구 절차는
> 지우면 운영이 멈춘다. **지운 것은 그 설계에 이른 실측과 판정 서술**이고
> **전량 13,181바이트를 [`DECISION-RATIONALE.md`](DECISION-RATIONALE.md) 「2026-11-16 비우기」에
> 요약하지 않고 그대로 옮겼다.**
>
> **역할**: 엔터·문화 산업 뉴스의 일일 자동 수집 엔진. 5개 검색 프로파일을 3개 시간대로 돌려
> Supabase `radar_items`에 적재한다. 큐레이션은 `nvl-vibe-radar` 대시보드가 한다(수집 안 한다).
>
> **정본 관계**: 아키텍처·deprecated는 루트 [`CLAUDE.md`](../CLAUDE.md) §5 · 결정 경위·실측·사고
> 이력은 [`DECISION-RATIONALE.md`](DECISION-RATIONALE.md)(이하 DR). 구 「운영 워크플로는
> `OPERATING-MODEL.md` §2」는 **2026-11-15에 그 문서를 비워 받는 자리가 없다.**
>
> **폐기된 것 셋.** ⓐ 주간 브리핑 NEWSPAPER HTML 발행을 **2026-06-09에 폐기했다**(과거 발행물은
> 역사 아카이브로만 보존하고 신규 생성 금지). ⓑ `weeklybriefing.vercel.app` 공개 배포를
> **2026-07-28에 폐기했다**(대표 결정 · 수집 엔진은 계속 돈다). ⓒ **Discord 알림 전량을
> 2026-10-11에 폐기했다**(대표 - 디스코드 운영 종료 · 대시보드 적재만 남는다).
>
> **절차를 못 찾아 멈추면 [멈춘 기록](../agents/STOP-LEDGER.md)에 한 줄 적는다** - 날짜 · 무엇을 찾다 멈췄나. 꺼내는 법과 되돌림도 거기 있다.

---

## 1. 수집 파이프라인

```
격일 3개 시간대 KST (GitHub Actions ai-news-daily.yml - cron 3개 */2)
  vibe_search만 격일, newsletter·newsroom은 매일
  오전 07시 한국·일본 / 오후 14시 중국·동남아 / 저녁 21시 글로벌(영어)
    → scripts/vibe_search.py (Claude Sonnet web_search, 해당 시간대 지역 순차)
    → 품질 게이트 (validate_candidates → URL 생존 확인)
    → Supabase radar_items upsert
    → 대시보드 큐레이션: https://nvl-vibe-radar.vercel.app/
```

**풀을 채우는 수집기는 다섯이다** - `vibe_search`(웹) · `newsletter_ingest`(§1-1) ·
`newsroom_ingest`(§1-2) · `interview_ingest`(§1-3) · `gnews_ingest`(구글 뉴스 RSS).
`nvl-vibe-radar` 자체 수집기는 폐기됐고 radar는 조회·큐레이션 대시보드다.

- **엔진**: `scripts/vibe_search.py` - Claude Sonnet `web_search` 서버사이드 도구(스트리밍).
  지역당 1\~5건, 기준 충족 후보 없으면 그날은 생략. 단일 워크플로에서 각 step `if`가
  `github.event.schedule`·수동 region input으로 분기하고 skip 지역은 outcome=skipped라
  실패 경보에 안 걸린다.
- **검색 예산**: `web_search` 5회 · `web_fetch` 3회 · fetch 본문 3,000토큰. env
  `VS_SEARCH_USES`·`VS_FETCH_USES`·`VS_FETCH_TOKENS`. **서버사이드 검색은 검색할 때마다 앞선
  결과를 다시 읽어 입력이 누적으로 자란다**(검색 n회면 대략 `nB + (n-1)n/2 x S`) - 조일 때
  이 성질을 안고 조인다.
- **나이컷**: 한·글·일 120h, 중·동남아 168h(`MAX_AGE_HOURS`). robots.txt로 막힌 일간지
  (조선·중앙·FT·Reuters 등)는 뉴스레터 구독으로 흡수한다 - 발신자만 `sources_newsletters.json`에 추가.
- **적재**: `scripts/supabase_writer.py` - REST upsert → `radar_items`. env
  `SUPABASE_URL`·`SUPABASE_KEY`(GitHub Secrets). **`SUPABASE_KEY`에는 secret key를 넣는다**
  (2026-10-12에 publishable(anon)에서 교체 · 이름은 안 바꿨다 - 읽는 자리가 42곳이다).
  정본 = `nvl-vibe-radar/README.md` 「키와 권한」. **레이더 쪽 `v20_lock_down_rls.sql`을 돌리면
  anon 키로는 적재가 막힌다** - 키를 먼저 바꾸고 수집을 확인한 뒤에 그 파일을 돌린다.
- **중복 제거**: `seen-titles.txt` + Supabase URL 중복 체크.
- **태깅**: 7렌즈 멀티태깅(`fan-behavior` `consumer-behavior` `ent-deals` `ip-business`
  `artist-ownership` `tech-issues` `taste-values`, `topics` 배열). `taste-values` = 세대를
  가로지르는 취향·가치 신호(엔터 밖 패션·뷰티·F&B·여행·리테일 포함 · 구 `gen-z-lifestyle`의
  재정의, 대시보드가 alias로 호환). **`cross-industry` 태그는 만들지 않는다**(대표 결정 -
  레퍼런스는 일부 신호의 속성이 아니라 전 콘텐츠의 해석 렌즈다).
  - **키 동기화 필수 - 같은 풀(`radar_items.topics`)을 쓰는 자리를 함께 바꾼다**:
    `newsletter_ingest.py`·`newsroom_ingest.py`의 `TOPIC_KEYS` · `nvl-vibe-radar`의
    `app.py` VALID_TOPICS·`dashboard.html` 필터/TOPICS/CROSS_CUL.
- **고유명사 후보**: 게이트가 항목마다 `names`를 받아 `tags`에 `n:<이름>`으로 남긴다. 두뇌
  `raw/names/` 사전의 **후보 공급까지**이고 등재는 세션이 한다(무인 푸시 금지).
  **문구·변환 정본 = `scripts/name_gate.py`** - 수집기 넷이 import한다. **복제하지 않는다** -
  역매칭은 글자가 같아야 세므로 수집기마다 문구가 갈리면 같은 대상이 두 줄로 쌓인다.
  산출 = `../scripts/wiki-candidates.py`.
- **출력 언어**: 모든 외국어 기사 제목은 한국어 번역. **LLM 응답 JSON 파싱은
  `scripts/llm_json.py` 하나로 모았다** - `parse_obj`(코드펜스 제거 → 첫 균형 블록 → 키별
  정규식 3층, 실패 시 예외) · `parse_list`(원본 → 수리 → 개별 객체 추출 3단).
  **`nvl-vibe-radar/llm_json.py`와 쌍둥이다** - 두 저장소는 서로 import할 수 없으니 한쪽을
  고치면 반드시 다른 쪽도 고친다.
- **차단 도메인은 `allowed_domains`에 넣을 수 없다.** Anthropic 크롤러 차단 도메인이 하나만
  끼어도 web_search API가 요청 전체를 **400으로 거부**한다. 추가하려면 `probe_domains.py`로
  사전 검증 후 통과분만. 피드 있는 IP홀더는 newsroom 수집기가 흡수하고, **피드 없는 곳**
  (Sony 그룹·Nintendo·Bandai·Crunchyroll·WBD·NBCU·Paramount)**은 `vibe_search.py`
  `search_terms`에 회사 키워드로 흡수**한다 - 키워드는 400에 안전하다. 그 키워드가 사는 자리는
  **global-en의 `ent-deals`·`ip-business` + japan의 `ip-business`**다.
- **풀 유지보수**: `scripts/pool_maintenance.py`. 매일 실행, `--apply` 없으면 미리보기만.
  - **상한** - collector별 pending 상한(gnews 140 · feed 30 · interview 200 · 그 외 50).
    자르는 순서는 **시제 우선**(바이브 > 시그널 > 미판정 > 배경), 같은 층에서 최신순.
    created_at만 보면 5일 된 바이브가 오늘 들어온 단신에 밀린다.
  - **시제 시효** - 바이브 14일 · 시그널 7일 · 뉴스 3일. **미판정은 시효 없음**, 인터뷰는
    collector 통째로 면제.
  - **픽 시효** - 20일(`picked_expiry_targets`, 인터뷰 픽 면제).
  - **면제** - 살아있는 타깃(`aims` open·drafting)의 근거 신호는 상한·시제 시효에서 뺀다.
    타깃을 세워뒀는데 근거가 사라지면 2 조사가 빈손으로 시작한다.
  - Supabase 전 행 조회는 반드시 `_fetch_paged`(Range 헤더 + id 정렬 순회) - PostgREST는
    `limit`과 무관하게 **1,000행에서 자른다.**
- **측정기 셋** (2026-10-08 신설 · 돌리는 법만 남긴다 · **읽는 법과 기준선은 DR 「2026-11-16 비우기」 ④**)
  - `scripts/query_yield.py` - 질의별 **수확 → 통과 → 좌표 → 타깃** 깔때기. gnews 적재 시
    `tags`에 `q:<질의이름>`을 남기는 것이 전제. **판단은 좌표 열로 한다** - 수확 20건 이상에
    좌표 0이면 「죽었다」, 30건 이상에 5% 미만이면 「묽다」로 지목한다. **질의를 고치려면
    이걸 먼저 돌린다.**
  - `scripts/actor_watch.py` - 「구글 뉴스에 있는데 우리 풀에 한 번도 안 들어온 것」만 제목으로
    보고한다(RSS만 · LLM 없음 · **적재 없음**). 겹침(자카드)으로 사건을 접는다.
    **이 수치로 비율을 계산하지 않는다**(사람이 제목을 보는 전제).
  - `scripts/radar_audit.py` - 위 둘을 불러 **다섯 숫자를 `radar-audit-log.md`에 한 줄**로
    쌓는다(질의·수집/일·좌표율·지목·못본사건·발행일치). 같은 날 행은 덮어쓴다.
    **돌리는 자리가 아직 없다** - `agents/dev-queue/2026-11-08-레이더-검수-로그를-돌리는-자리가-없다.md`.
- **행위자 축**: 질의로 넣은 것은 **관용어 2종**(`엔터 지분 투자`·`기획사 계약`) + **법인형
  행위자 3종**(`카카오엔터테인먼트`·`CJ ENM`·`네이버웹툰`)이다. **회사명을 그대로 넣으면
  터진다**(피드가 컴백·직캠·특징주로 찬다) - 그래서 둘로 나눴다. 넣지 않은 행위자는
  `actor_watch.py`가 본다.
  - **행위자 블록은 `QUERIES` 맨 끝에 둔다.** 앞 질의가 URL을 선점하므로 그 줄들의 「신규」가
    곧 한계 기여다. **순서를 바꾸면 그 측정이 깨진다.**
  - 「멜론」은 행위자로 쓰지 않는다 - 고창멜론연합회 농업마이스터가 섞인다.
- **개별 테스트**: `ai-news-daily.yml`의 `workflow_dispatch` region input =
  **`all`/`korea`/`global-en`/`china`/`japan`/`southeast-asia`**. 이건 **검색 프로파일**이지
  저장 지역이 아니다(§1-4).
- **실패 경보**: 지역 스텝이 검색 실패(web_search API·코드 에러)로 끝나면
  `scripts/notify_region_failure.py`가 **잡을 실패시킨다**(`::error::` + exit 1). GitHub
  Actions가 자기 알림을 보내므로 추가 채널이 없다(메일 경보는 2026-09-14 대표 지시로 폐기).
  **0건(정상)은 exit 0이라 안 잡힌다** - 침묵 실패만 본다.

**§1-1\~1-3 공통**: 대시보드 전용, `total_score=0`(사전 큐레이션 소스), 대시보드에 출처 배지.
시크릿은 `SUPABASE_*` + `ANTHROPIC_API_KEY_WEEKLY_BRIEFING` 재사용(피드는 무인증).
피드 URL은 **실제 fetch로 유효 XML을 검증한 뒤 등재**(죽은 피드 · 헤더만 주고 본문이 빈
깡통 주의 · **200이 곧 유효 피드가 아니다**), `_` 접두 = 비활성. 소스 추가·제거는 각 JSON만 편집.

## 1-1. 뉴스레터 수집기 (collector='newsletter')

**수집 계정은 대표의 뉴스레터·AI서비스 전용 계정 `tmifmdj@gmail.com`이다** - `GMAIL_USER`
시크릿의 값이 무엇인지 적힌 유일한 자리다.

- **엔진**: `scripts/newsletter_ingest.py` - Gmail IMAP 앱비밀번호(stdlib `imaplib`) →
  allowlist 발신자의 최근 메일 → 본문 추출·추적URL 복원(base64 경로 디코드) → Claude haiku
  분류(7렌즈 topics + 한국어 요약) → upsert. 지역은 발신자별 고정 힌트.
- **allowlist**: `sources_newsletters.json` - 발신자 47곳 = 엔터·문화·소비 직결 35 +
  `broad` 12(일반·테크·비즈 종합). `broad:true`는 `classify`가 is_entertainment 게이트를
  넓혀 교차 신호까지 채택(순수 하드뉴스만 제외). **고신호는 발신자로 유지한다** - 받은편지함
  대부분이 노이즈라 탭 통째 수집은 안 한다.
- **스케줄**: `.github/workflows/newsletter-ingest.yml` 하루 2회 - **09:30 KST** +
  **23:00 KST** + `workflow_dispatch`(lookback_days). lookback 2일 + URL dedup이라 2회가
  겹쳐도 안전.
- **시크릿**: `GMAIL_USER`·`GMAIL_APP_PASS`(IMAP 앱비밀번호).
- **중복 제거**: 최근 14일 newsletter URL 집합 + URL upsert(merge-duplicates).
- **캐치올 제목 게이트**: allowlist 밖 발신자도 제목이 엔터·콘텐츠·미디어·문화·소비 신호면
  수집한다. 2단 - ① 런당 haiku 1콜로 제목 배치 판정(`subject_gate` · 프로모·행사·계정알림·
  하드뉴스 제외, 애매하면 제외) ② 통과분만 본문 fetch·strict classify. 런당 상한
  `NL_CATCHALL_CAP`(기본 8), `NL_CATCHALL=0`으로 끔. `catchall_ignore`(JSON 최상위 키) =
  트랜잭션·서비스 알림 제외 목록 - **편집 매체는 넣지 않는다**(제목 게이트가 건별 판정).
  **캐치올 수집분의 `source`는 발신 도메인**이다(저장 값).

## 1-2. 뉴스룸 수집기 (collector='newsroom')

- **엔진**: `scripts/newsroom_ingest.py` - RSS(`item`)·Atom(`entry`) fetch(stdlib
  `xml.etree`) → 룩백 내 항목 → Claude haiku 분류 → upsert. 지역은 소스별 고정 힌트.
- **allowlist**: `sources_newsrooms.json` - 17소스(IP홀더·플랫폼 + 아카이브·연구 +
  차트메트릭 2종 + 한국 산업 매체 2종 + 1차 자료 RIAA·Deezer).
- **소스 유형 둘** - 기본은 RSS·Atom. **피드가 없는 곳은 `type: "sitemap"`**으로 받는다.
  `sitemap.xml`의 `<loc>`을 `path_prefix`로 거른 뒤 각 기사 페이지에서 프리렌더된 `<title>`과
  meta description을 뽑고, 그다음(게이트·번역·적재·중복제거)은 피드 경로와 같다.
  **사이트맵의 `lastmod`는 안 본다** - 매일 다시 찍혀 전건이 오늘로 나온다. 발행일이 없으므로
  최초 발견일을 쓰고 30일 URL 중복 제거가 한 기사를 한 번만 들인다.
- **등재 기준은 밀도다.** 피드가 살아 있어도 엔터 밀도가 낮으면 안 붙인다(그룹 전체
  보도자료·스타트업 일반·가십 섹션·유료 장벽). **한국 기업 뉴스룸과 기관 1차 자료는 넣을 수
  없다**(실측 2026-10-08·10-17 - SPA라 피드가 없거나 403, 기관은 RSS가 없고 밀도가 0\~8%).
  **그 공백을 메우는 자리는 수집기가 아니라** 주간 `/report-scan`(4언어)과 OneDrive 리포트
  인덱스다. **실측 전량 → DR 「2026-11-16 비우기」 ⑤**(거절한 피드 목록이 거기 있다).
- **스케줄**: `.github/workflows/newsroom-ingest.yml` 매일 **10:00 KST** +
  `workflow_dispatch`(lookback_days). **lookback 7일**.
- **중복 제거**: 최근 30일 newsroom URL 집합 + URL upsert.

## 1-3. 인터뷰 수집기 (collector='interview')

국내외 아티스트·창작자 인터뷰(텍스트·영상). 용처 = @nvl.seoul 소재 파이프라인 + 인물 발굴.

- **엔진**: `scripts/interview_ingest.py` - 매체 RSS·유튜브 채널 RSS
  (`videos.xml?channel_id=UC…`) fetch → Claude haiku 분류 → **is_interview=true만** upsert.
  YouTube Atom의 `<media:group>` 중첩 제목·설명도 파싱.
- **분류 게이트**: haiku가 두 축을 따로 판정하고 **둘 다 true여야 적재**한다.
  ① `is_interview`(형태 - 아티스트 본인 발화 중심만 true. 뉴스·리뷰·차트·퍼포먼스 단독·MV·
  리스트는 false, **애매하면 false = 정밀 우선**) ② `is_music_ent`(주제 - 발화 내용이 음악·
  엔터 창작이나 그 산업이면 true. **말하는 사람이 아티스트여도 주제가 음악·엔터가 아니면
  false**). 모델이 `is_music_ent` 키를 빠뜨리면 통과시킨다. 그 밖에 `person_ko` ·
  title_ko/summary_ko/region. summary는 「인물 - 요지」 관례. `is_interview=false`·분류실패는
  `filtered_out`(verdict `not_interview`/`classify_failed`)로 적재해 풀·인터뷰탭에서 숨긴다.
- **allowlist**: `sources_interviews.json` - 31소스 중 **활성 8**(영상 7 + 텍스트 1).
  **뺀 사유는 각 항목 `_reason`에, 무엇을 뽑을지는 `_strength`에 적는다**(`_strength`는
  digest 프롬프트가 읽는다). 유입이 부족해지면 FADER·GRAMMYS부터 되살린다. 각 소스
  `media`("text"|"video") 힌트 → 대시보드 `tags`. **국내 소스가 0이다** - 한국 음악·엔터
  업계 당사자가 말하는 팟캐스트를 찾으면 이 자리에 넣는다.
- **대시보드 탭 라벨은 「사람」이다.** 인터뷰 수집분에 더해 **gnews 게이트가 `is_person`을
  판정해 `tags`에 `person`을 붙인 기사**도 이 탭에 뜬다(`regate.py`도 붙인다).
  **표시는 영어·일본어·한국어 판 질의에서 온 기사에만 붙는다**(`PERSON_LANGS`) - 판정은
  질의의 `hl`로 하고 `q:` 태그가 없는 10-08 이전 적재분은 붙이지 않는다.
  `collector` 값과 파일명은 `interview`로 둔다 - `radar_items` 2,206행이 걸려 있다.
- **스케줄**: `.github/workflows/interview-ingest.yml` **화·금 11:00 KST** +
  `workflow_dispatch`. **룩백 14일 · 나이컷 없음**. GitHub 신규 예약 워크플로는 첫 예정
  발화를 스킵한다 - 첫 자동 수집이 안 보이면 `workflow_dispatch` 1회 수동.
- **중복 제거**: 최근 **60일** interview URL 집합 + URL upsert(merge-duplicates).
- **픽 시효 면제**: `pool_maintenance.py`의 픽 20일 시효에서 `collector='interview'` 픽은 면제.
- **쓰는 자리는 대시보드 탭이 아니라 digest다** (2026-10-08 대표 - 「다 보고 들을 시간이
  없다」). `../scripts/podcast-digest.py`가 자동자막(yt-dlp)으로 전사하고 haiku로 회차마다
  **요지 3줄 + 당사자 발언 3\~5개(타임코드)**를 뽑아 두뇌 `raw/library/podcast/`에 채널별로 쌓는다.
  - **적재물은 `citation_ready: false`에 verbatim 직접 인용 금지**, 패러프레이즈는 출처 표기 후
    자유. **검증 층이 다르다** - 위키 `status`는 관점의 검증이고 당사자 발언의 검증은
    「실제로 말했나」이며 타임코드·전사본이 그것을 보증한다.
  - **유튜브 채널 피드는 쇼츠·클립이 다수다** - 길이 문턱 12분이 메타만 보고 걸러 API를 안 쓴다.
    근본 해법은 팟캐스트 RSS를 따로 붙이는 것이고 **미착수**(추측 주소 6개가 전부 404).

## 1-4. 지역 축

**검색 프로파일 5종과 저장 지역 12종은 다른 층이다.** 프로파일은 「어느 언어·시간대로
검색하나」이고 저장 지역은 「그 기사가 다루는 시장이 어디인가」다.

- **저장 값 12종** (`radar_items.region`): `korea` `japan` `china` `southeast-asia`
  `north-america`(미국·캐나다) `europe`(영국·독일·프랑스·북유럽·동유럽) `latin`(스페인어권·
  브라질) `mena`(중동·북아프리카) `africa-ssa`(사하라이남) `india-sa`(인도·남아시아)
  `oceania`(호주·뉴질랜드) `multinational`(특정 국가 귀속 없는 다국적 발표·업계 일반론·글로벌 통계).
- **`global-en`은 신규 저장하지 않는다.** 과거 archived 행이 수천 건이라 라벨 맵에만
  「글로벌(구)」로 남는다. 분류 실패 시 소스 힌트로 남는 값도 이 레거시이고 **12종 중 하나를
  억지로 찍지 않는다** - `backfill_region.py`가 나중에 내용 기준으로 다시 판정한다.
- **분류 기준(모든 프롬프트 공통 문구)**: 매체 국적이나 기업 본사가 아니라 **기사 내용의
  시장**. 여러 시장이면 비중이 큰 쪽 하나. 어느 나라에도 귀속되지 않는 다국적 발표·업계
  일반론만 `multinational`이고 **모르겠다고 여기 넣지 않는다.**
  - **각 수집기의 `REGION_GUIDE` 상수가 같은 문장을 쓴다 - 하나를 고치면 다섯을 같이 고친다**
    (`newsletter_ingest`·`newsroom_ingest`·`interview_ingest`·`backfill_region`·`gnews_ingest`).
- **검색 프로파일 5종**은 `vibe_search.py` `REGIONS` 키(= cron 슬롯·allowed_domains).
  그 결과의 region은 저장 시점엔 미판정이고 `backfill_region.py --collectors vibe_search`가 재판정한다.
- **gnews는 gl 매핑을 버렸다.** 구글 뉴스 에디션은 독자의 위치지 기사의 시장이 아니다.
  엔터 게이트 haiku가 `region`을 함께 판정하고(**호출 수 불변**) 판정이 없을 때만 gl 표로 떨어진다.
- 라벨은 `supabase_writer.py` `REGION_LABELS` = `send_report_drop.py` `_REGION_LABELS`
  **두 곳이 같은 문자열을 쓴다.**

## 1-5. 판정이 막혔을 때

Anthropic 계정의 지출 한도에 걸리면 게이트 haiku가 전건 400으로 죽는다. 수집은 RSS라 공짜인데
룩백이 2\~3일뿐이라 그냥 멈추면 그 사이 기사를 영영 못 줍는다.

- **수집은 계속한다.** `gnews_ingest.py --no-gate`(워크플로 `no_gate` 입력)는 판정을 건너뛰고
  원문 제목 그대로 적재한다. 한도 오류는 자동 감지해 같은 자리로 떨어뜨린다.
- **보류와 실패를 나눈다.** `filter_verdict='gate_deferred'`는 「판정을 미뤘다」이고
  `classify_failed`는 「판정하려다 실패」다. 둘 다 `filtered_out`이라 풀에 안 뜬다.
- **한도가 풀리면 `scripts/regate.py`.** 두 verdict를 찾아 게이트에 다시 태운다. 게이트 로직은
  `gnews_ingest`에서 import한다 - **두 벌이 되면 반드시 어긋난다.** `status`가 `pending`·
  `filtered_out`인 것만 건드리고 사람이 손댄 상태는 그대로 둔다.
- **표기만 고칠 때는 `scripts/fix_title_notation.py`.** `backfill_translate.py`는 뉴스룸·
  뉴스레터 전용이고 `summary`를 덮어쓴다 - 구글 뉴스 카드의 `summary`에는 시제 판정의 `why`가
  들어 있어 덮으면 카드 한 줄이 사라진다.

## 2. 품질 게이트

Anthropic `web_search` 도구에 날짜 필터 파라미터가 없어 코드 레벨로 강제한다.

1. 프롬프트에 오늘 날짜(KST)+컷오프 주입, `published_date` 필드 요구
2. **출처 화이트리스트** - 지역별 `allowed_domains`로 web_search 검색 자체를 제한 + 코드
   검증에서 목록 외 출처 제외(보도자료 재가공 매체 차단, 대표 지시). `BLOCKED_DOMAINS`
   (나무위키)는 별개 방어선
3. `validate_candidates()` - 필수 필드·한국어 요약·**4지표 점수 재계산(≥4)**·발행일 48시간 컷
   (`MAX_AGE_HOURS` env, 지역별 값은 §1). 4지표 = 소재적합·캐러셀적합·출처신뢰·교차정체성
   (각 0\~2, 만점 8). 임계 `MIN_TOTAL_SCORE` 기본 4(env 조정 - 0건 반복 시 3으로 완화).
   **발행일 미상은 제외**(대표 지시). 0건이 반복되면 `ALLOW_UNDATED=1`로 임시 완화(플래그 게재).
   `radar_items`에 `cross_identity` 개별 컬럼은 없다(total_score에 합산만).
4. 점수순 정렬(동점 시 reliability→발행일 확인분 우선) → 배치 내 중복 제거 → **도메인당 2건
   상한**(`MAX_PER_DOMAIN`, 1차 패스로 한 매체 독식 방지. 미달 시 2차 패스에서 상한을 풀어
   건수 보존) → URL 생존 확인(`check_url_alive`, 404/없는 도메인 차단) → 최대 5건
5. 드롭 통계를 GitHub Actions Step Summary에 노출

단위 테스트: `scripts/test_quality_gate.py`.

## 3. 절대 금지 (위반 시 작업 중단)

- **기억·지식 기반으로 뉴스를 만들어내지 않는다.** 훈련 데이터의 사실, 그럴듯한 추정, 생성된
  가짜 URL 일체 금지.
- 모든 기사는 **세션 내 검색·fetch로 직접 확인한 것**만. URL 검증 불가능하면 제외하고,
  건수가 부족해도 채우지 않는다.
- 「지식 기반 소스 활용」·「URL 검증 생략」 같은 판단을 스스로 내리지 않는다.

## 4. 주간 리포트 드롭 (별개 흐름, 존속)

뉴타입컬처클럽 자료실용 산업 리포트 큐레이션. 일일 뉴스 수집과 별개.

- 생성: `/report-scan` 스킬 - **주간(매주 월 10:00 예약 작업 `report-scan-weekly`)**.
  4언어 검증 → `drops/YYYY-MM-DD-주간리포트드롭.md` 2곳 저장(weekly-vibe/drops/ +
  ecri-business/operations/) + 마스터 색인 반영(**색인이 SSOT**).
- 발송 로직: `scripts/send_report_drop.py` - 최신 드롭 찾기·정제 → **대시보드 적재만**.
  정시·백업 공용 모듈(stdlib). **워크플로 YAML 안에 heredoc으로 로직을 넣지 않는다.**
  필수 둘 - **① 중복방지**: 드롭이 `DROP_MAX_AGE_DAYS`(기본 7)일 이상 지났으면 발송 생략
  (`return 0` → 정시 워크플로 success 유지 → watchdog 오경보 없음) **② 대시보드 적재**:
  드롭의 1위 메달+신규 리포트를 파싱(`parse_drop_items`)해 `radar_items`에
  `collector='newsroom'`으로 적재(다시보기=기보유 제외, URL 중복 merge-duplicates,
  `SUPABASE_URL/KEY` env를 정시+백업 양쪽에 주입).
- 적재: `.github/workflows/discord-report-drop.yml`(**파일명은 호환 위해 유지** - Discord는
  폐지됐다) - `drops/` push 시 즉시 + 월요일 **13:17 KST** 백업 cron. 실제 발송은 위 신선도
  가드로 **새 드롭 있을 때만.**
- 백업 감시: `.github/workflows/report-drop-watchdog.yml` - 월 **13:40 KST** 점검 → 정시
  누락 시 직접 재발송(`check_drop_posted.py` 발송 판정). 실패하면 「결과 판정」 스텝이
  `::error::` + exit 1로 잡을 빨갛게 만든다(메일 알림은 2026-09-14 폐기).

## 5. 되돌림 조건 - 기계가 읽는 꼴로 적는다

**`retirement-scan.py`와 `retire-judge.py`가 이 표를 읽는다**(gitignore지만 검사 범위 안이다 -
`retirement-scan.py` 376행이 **「폴더로 좁히지 않는다」**고 적어 뒀다). **읽히려면 꼴이 둘 맞아야 한다.**

1. **표 머리에 「되돌림」이 들어가야 한다.** `table_rows()`가 **머리 줄에 그 낱말이 있는 표만**
   데이터 행으로 읽는다(`retirement-scan.py` 515\~521행 · `KEY = "되돌림"`).
2. **조건 칸에 `기준일 YYYY-MM-DD`와 `N주(YYYY-MM-DD)`를 적어야 한다.** `retire-judge.py`가
   그 꼴에서 만기·미달을 판정한다. 그 꼴이 아니면 **「못셈」**으로 떨어진다.
3. **판정하면 그 행에 `판정 YYYY-MM-DD`를 적는다.** `RE_JUDGED`가 그것을 가장 먼저 보고
   **다시 안 찍는다** - 그게 이 표의 처분 기전이다.

> **2026-11-16에 이 꼴을 한 번 어겼다.** 비우면서 산문 열 군데를 표로 묶었는데 **머리에
> 「되돌림」을 안 넣어 열 행이 통째로 안 읽혔다**(비우기 전 9줄 → 0줄). `classify()`에 행을
> 직접 먹여 「안 줄었다」고 쟀는데 그 앞단의 머리 관문을 건너뛴 측정이었다.
> **표로 옮길 때는 `python scripts/retirement-scan.py --raw | grep weekly-vibe/CLAUDE`로 잰다.**

**재는 명령** - `python scripts/retire-judge.py --paths weekly-vibe/CLAUDE.md`.

| 장치 | 되돌림 조건 (기준일 · 창) | 판정 |
|---|---|---|
| 행위자 축 3종 | 기준일 2026-10-08 · **4주(2026-11-05)**에 좌표율이 기준선(41.2%) 절반 아래면 뺀다 | **판정 2026-11-16 · 미발동 · 유지**(누계 기준 · 주별로는 네이버웹툰이 미달) |
| 뉴스룸 묶음 전체 | 분모 = `collector='newsroom'` 주간 수율(통과/적재) · 기준일 2026-11-18 · **1주(2026-11-25)**에 기준선 34.8%의 절반인 17.4% 아래면 밀도 낮은 피드를 접는다 | 미달 (11-18 실측 적재 158 · 통과 55) |
| 고유명사 후보 산출 | 기준일 2026-10-14 · **4주(2026-11-11)** 돌려 승격 0건이면 산출을 접는다 | **판정 2026-11-16 · 미발동 · 유지**(`이름` 축만 · `렌즈` 축 창은 12-08) |
| `actor_watch` | 기준일 2026-10-08 · **6주(2026-11-19)** 연속 사각지대 0이면 접는다 | **판정 2026-11-23 · 못셈 · 셀 자리 없음**(아래 블록) |
> **판정 2026-11-23 - 못셈. 셀 자리가 없다.** 창(11-19)이 닫혔는데 **`scripts/actor_watch.py`가 적재를 안 한다** - 그 파일이 스스로 「RSS는 공짜이고 LLM을 안 쓴다. 적재도 안 한다」고 적고 산출 파일도 0개다(`find . -name '*actor_watch*' -not -name '*.py'`). **「6주 연속」을 세려면 6주치 실행 기록이 있어야 하는데 과거를 볼 자리가 없다.** 고칠 자리는 조건이 아니라 **그 도구에 한 줄 로그를 붙이는 것**이고, 안 붙일 거면 조건을 접는다. 둘 중 무엇을 하나는 그 도구의 값 판단이라 열어 둔다.
| 검수 적재 (`radar_audit`) | 기준일 2026-10-08 · **8주(2026-12-03)** 쌓아 다섯 숫자가 모두 평평하면 접는다 | **셀 수 없다** |
| 인터뷰 digest | 기준일 2026-10-08 · **8주(2026-12-03)** 동안 리서치 보고서·발행에 안 쓰이면 수집을 접는다 | 미달 |
| 질의별 수확 보고 | 기준일 2026-10-08 · **6개월(2027-04-08)** 돌려 지목된 질의가 하나도 없으면 접는다 | 미달 |
| 검색 예산 (`VS_SEARCH_USES=5`) | 기준일 2026-09-18 · **창이 없다** - 지역당 후보가 2주 연속 평균 1건 아래면 6으로 되돌린다 | 상시 |
| 실패 경보 (잡 실패) | **기준일도 창도 없다** - 침묵 실패가 또 늦게 발견되면 Discord 전용 웹훅을 세운다 | 상시·못셈 |

**창이 닫힌 넷을 2026-11-16에 판정했다 - 넷 다 미발동이고 전부 유지한다.**
**실측·주별 분포·조건 결함 다섯은 [`DECISION-RATIONALE.md`](DECISION-RATIONALE.md)
「2026-11-16 만기 넷 판정」이 든다** - 이 파일은 돌리는 법만 진다.

**위 표의 소스별 두 줄을 2026-11-18에 접었다** (대표 결정 「조건을 접는다」). 임계가
「4주간 풀에 한 건도 안 남으면」이라 **1건이면 미발동**이었고 등재 사유인 **밀도를 못 받았다**
(같은 창에 테크M 216 대 RIAA 전수 1). 소스 하나를 기계가 처분하는 자리를 없애고 **묶음 전체
수율 한 줄**로 바꿨다 - 수율이 반 토막이면 **밀도 낮은 피드를 사람이 골라 접는다.** 재는 명령 =
`python scripts/newsroom_yield.py`(그 머리가 안 보는 축도 든다). 소스 목록 자체는 안 건드렸다.

**한 줄로 남기는 것 둘.** ⓐ 「풀에 남으면」류는 **게이트 통과로 읽어야 판정이 된다**
(시제 시효 때문에 「지금 살아 있는 것」은 4주 전 적재면 어느 소스든 0이다). ⓑ **네이버웹툰은
누계로 통과하고 주별로는 거의 전부 미달이다** - 조건이 누계냐 주별이냐를 안 적었고 그 선택은
대표 몫이다. 조건 결함 다섯은 `agents/dev-queue/2026-11-16-되돌림-조건-128건이-못셈이다.md`와
`SESSION_LOG` `## OPEN`이 받는다.

「상시」 둘은 **닫히는 창이 없다** - 조건이 걸리는 그때 보는 것이라 만기라는 개념이 없다
(검색 예산은 기준일만 있고 실패 경보는 기준일도 없다).

**판정하려면 Supabase 키가 필요하다**(`nvl-vibe-radar/.env`). 재는 명령 -
`cd weekly-vibe && python scripts/newsroom_yield.py` · `python scripts/query_yield.py` ·
`python scripts/actor_watch.py` ·
`python scripts/radar_audit.py`.

**`radar_audit`은 「셀 수 없다」다** - 로그에 10-08·10-11·11-08 **세 행**뿐이라 평평한지
안 돈 건지 안 갈린다. 그 조건의 기준일은 **첫 자동 적재일로 다시 적어야 한다.**
카드 = `agents/dev-queue/2026-11-08-레이더-검수-로그를-돌리는-자리가-없다.md`.

## 6. 파일 구조

```
weekly-vibe/
├── CLAUDE.md                    ← 이 파일 (돌리는 법만)
├── DECISION-RATIONALE.md        ← 결정 경위·실측·사고 이력 (규칙 수정 전 필독)
├── scripts/
│   ├── vibe_search.py           ← 수집 엔진 v3 (5지역)
│   ├── llm_json.py              ← LLM 응답 JSON 3층 파싱 (nvl-vibe-radar/llm_json.py와 쌍둥이)
│   ├── name_gate.py             ← 고유명사 게이트 문구·`n:` 태그 변환 (수집기 넷 공통 정본)
│   ├── supabase_writer.py       ← radar_items upsert
│   ├── send_report_drop.py      ← 리포트 드롭 공용 모듈 (정시+백업)
│   ├── check_drop_posted.py     ← 백업: 오늘 발송 여부 판정 (gh 런 이력)
│   ├── notify_region_failure.py ← 지역 검색 실패 시 잡 실패(::error::)
│   ├── newsletter_ingest.py     ← 뉴스레터 IMAP 수집기 (§1-1)
│   ├── newsroom_ingest.py       ← 뉴스룸 RSS 수집기 (§1-2)
│   ├── interview_ingest.py      ← 인터뷰 RSS·유튜브 수집기 (§1-3)
│   ├── gnews_ingest.py          ← 구글 뉴스 RSS 수집기 (collector='gnews')
│   ├── query_yield.py           ← 질의별 수확 깔때기 보고(죽은 질의 지목)
│   ├── actor_watch.py           ← 행위자 사각지대 감시(수집 없이 「못 본 사건」만)
│   ├── radar_audit.py           ← 검수 5숫자를 radar-audit-log.md에 한 줄로 적재
│   ├── regate.py                ← 보류·실패한 gnews 수집분 일괄 재판정 (§1-5)
│   ├── fix_title_notation.py    ← 적재된 제목의 표기만 정규화 (§1-5)
│   ├── pool_maintenance.py      ← 풀 유지보수(상한 archive + 시제·픽 시효)
│   ├── probe_domains.py         ← allowed_domains 후보 사전 검증
│   └── test_quality_gate.py     ← 품질 게이트 단위 테스트
├── sources_newsletters.json     ← 뉴스레터 발신자 allowlist
├── sources_newsrooms.json       ← 뉴스룸 피드 allowlist
├── sources_interviews.json      ← 인터뷰 피드·채널 allowlist
├── .github/workflows/
│   ├── ai-news-daily.yml        ← 격일 3시간대 수집
│   ├── newsletter-ingest.yml    ← 매일 09:30·23:00 KST
│   ├── newsroom-ingest.yml      ← 매일 10:00 KST
│   ├── interview-ingest.yml     ← 화·금 11:00 KST
│   ├── gnews-ingest.yml         ← 매일
│   ├── discord-report-drop.yml  ← drops/ push 시 대시보드 적재 (월 13:17 백업)
│   └── report-drop-watchdog.yml ← 월 13:40 KST 백업(13:17 적재 뒤 점검)
├── drops/                       ← 주간 리포트 드롭 마크다운
├── radar-audit-log.md           ← 검수 5숫자 (돌리는 자리가 아직 없다 · §5)
├── seen-titles.txt              ← 중복 제거 캐시
└── NEWSPAPER_*.html 등          ← 구 주간 브리핑 발행물 (역사 아카이브, 신규 생성 금지)
```

---

## 지금 무엇이 그 자리를 드나

**기계가 집행하는 것은 그대로 돈다** - 이 파일을 읽는 코드는 없지만 **이 파일이 검사 대상이다**
(`drift-scope.py`가 지침 전량에 넣고 `drift-scan`·`canon-ref-check`·`spec-sync`가 거기서 받는다).

| 무엇 | 어디 |
|---|---|
| **수집기 다섯의 cron** | `.github/workflows/` 실물이 정본. 이 파일 §1\~1-3은 서술이고 **`spec-sync [4] 축 ⓑ`는 빈도 낱말만 댄다 - 시각을 보는 자리는 없다**(gnews·pool-maintenance는 여기 「매일」만 있다) |
| **저장 지역 12종 값** | 이 파일 §1-4 + 각 수집기 `REGION_GUIDE` 상수 다섯 |
| **7렌즈 토픽 키** | 이 파일 §1 + `TOPIC_KEYS` 둘 + `nvl-vibe-radar` 셋 |
| **고유명사 문구** | `scripts/name_gate.py` 하나 (수집기 넷이 import) |
| **LLM JSON 파싱** | `scripts/llm_json.py` + `nvl-vibe-radar/llm_json.py` **쌍둥이** |
| **Supabase 키 권한** | `nvl-vibe-radar/README.md` 「키와 권한」 |
| **되돌림 조건 열** | 이 파일 「되돌림 조건」. `retirement-scan`이 표 행을 조건으로 읽고 **`retire-judge`가 만기를 판정한다** |
| **경위·실측·사고 이력** | [`DECISION-RATIONALE.md`](DECISION-RATIONALE.md) |

## 없어진 본문

**바이트 수는 이 파일에 적지 않는다.** 자기 크기를 자기 안에 적으면 **그 문장이 크기를 바꿔
영원히 안 맞는다** - 2026-11-16에 세 번 낡았고 감사가 두 번 잡았다. **전·후 수는
[`agents/2026-11-15-2027-정본-리셋-계획.md`](../agents/2026-11-15-2027-정본-리셋-계획.md)
「비운 것을 여기 적는다」 표가 든다**(다른 정본 열넷과 같은 자리). **감축률이 다른 정본들보다
낮은 것은 정상이다** - 도는 소프트웨어라 지우면 운영이 멈추는 쪽이 대부분이다.

지운 산문은 버린 것이 아니라 **[`DECISION-RATIONALE.md`](DECISION-RATIONALE.md)
「2026-11-16 비우기」로 옮겼다**(원문 ①\~⑦ + 절 머리 + 「끊긴 것 넷」).
> **이 수는 세 번 낡았다**(감사가 두 번 잡았다) - **마지막 편집 뒤에 재서 넣는다.**

지운 것 - **Discord 채널·Secret 표**(5행 · 전부 폐지분) · **검색 예산 실측**(9월 검색
288회에 입력 1,431만 토큰 · 월 비용의 32% · 모델이 허용 8회 중 6회를 썼다) · **행위자 축 실측
전량**(룩백 3일 신규가 티빙 86·JYP 85·YG 82·SM 66·하이브 57건 · 9개를 넣으면 한국 수집이
63→516건 +719% · 관용어·법인형의 한계 수확 40·22·4건 · 합계 69건/룩백) · **뉴스룸 등재 판정
서술 셋**(한국 기업 뉴스룸 SPA·403 · `korea.kr` 밀도 8%와 보도자료 0% · `kocca.kr` 404와
FileDown 차단 · CJ 뉴스룸·벤처스퀘어·전자신문·한국경제·아웃스탠딩을 뺀 사유) · **인터뷰 축소
근거**(30→8 기준 · 2,206건에 타깃 0건·발행 인용 1건 · 주 23회차 17시간 → 읽기 5분 · digest
주 $0.23 · 길이 표본 21편) · **검수 로그 읽는 법**(쌍으로 읽는다 · 좌표율은 순환한다 ·
기준선 다섯 숫자) · **품질 게이트 도입 경위와 4지표 개편** · **변경 이력 절**.

**두 곳에 있다** - ⓐ [`DECISION-RATIONALE.md`](DECISION-RATIONALE.md) 「2026-11-16 비우기」가
**13,181바이트를 그대로** 들고 있다(①~⑥) ⓑ `cd weekly-vibe && git log --oneline -- CLAUDE.md`로
비운 커밋을 찾아 `git show <그 커밋>^:CLAUDE.md`. **별 저장소다** - NVL `.gitignore`가 이 폴더를
통째로 제외하므로 NVL의 git 히스토리에는 이 파일이 없다.

## 비우면서 끊긴 것 넷 (다시 쓸 때 여기부터)

1. **읽는 법이 없어진 측정기가 셋이다.** `query_yield`·`actor_watch`·`radar_audit`의 **돌리는
   법은 남겼지만 「무엇이 개선인가」를 지웠다.** 가장 비싼 한 줄이 **「못본사건이 내려가면서
   수집/일이 안 오르는 것이 개선이다」**였다 - 그것 없이 숫자만 보면 **질의를 늘려 사각지대를
   줄이는 것을 개선으로 읽는다.** 그 판정은 **DR 「2026-11-16 비우기」 ④**와 dev-queue 11-08 카드에 있다.
2. **「왜 회사명을 질의로 안 넣나」가 수치 없이 남았다.** §1의 「회사명을 그대로 넣으면 터진다」는
   결론만이고 +719%라는 크기가 빠졌다. **다음 세션이 「하이브를 넣어 보자」고 할 때 막을 수치가
   이 파일에 없다.**
3. **뉴스룸 등재를 거절한 선례 목록이 없다.** 어느 피드를 왜 뺐는지가 **DR 「2026-11-16 비우기」 ⑤**에만
   있어 **같은 후보를 다시 검토하게 된다**(CJ 뉴스룸·벤처스퀘어·전자신문·한국경제·아웃스탠딩·
   지디넷·블로터·뉴스와이어·IFPI·Grammy).
4. **4지표 점수의 설계 근거가 없다.** 지표 이름과 임계만 남았고 **「만점의 50%」라는 유도도,
   왜 4지표인지, 왜 각 0\~2인지도 빠졌다**(그 줄은 DR ⑦에 있다). **임계를 바꾸려 할 때 근거를 DR 「§2 - 품질 게이트」에서 받는다.**
