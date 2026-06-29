# weekly-vibe — 일일 수집 엔진 (vibe_search v3)

> **역할 (2026-06-09 전환)**: 엔터·문화 산업 뉴스의 **일일 자동 수집 엔진**. 매일 5개 지역을 현지 발행 리듬에 맞춰 3개 시간대로 나눠(오전 한국·일본 / 오후 중국·동남아 / 저녁 글로벌, 2026-06-17) 수집해 Discord 5개 지역 채널에 알리고 Supabase `radar_items`에 적재한다.
>
> **주간 브리핑(NEWSPAPER HTML) 발행은 2026-06-09 폐기.** 과거 발행물(`NEWSPAPER_*.html` · `SPECIAL_*.html` · `index.html` · `preview/`)은 역사 아카이브로만 보존한다. `weeklybriefing.vercel.app`은 배포 중단 대상, 제작 스킬(`.claude/skills/weekly-vibe/`)은 2026-06-10 삭제. 구 제작 가이드(태그 시스템·디자인 시스템·검증 체크리스트)가 필요하면 이 파일의 git 히스토리(2026-06-10 이전) 참조.
>
> **정본 관계**: 운영 워크플로(무엇을·언제·왜)는 [`ecri-ceo-staff/operations/26.06.08-daily-weekly-workflow.md`](../ecri-ceo-staff/operations/26.06.08-daily-weekly-workflow.md) §2 · 아키텍처·deprecated는 루트 [`CLAUDE.md`](../CLAUDE.md) §5.

---

## 1. 수집 파이프라인

```
격일 3개 시간대 KST (GitHub Actions ai-news-daily.yml — cron 3개 */2, 2026-06-29 토큰절감. vibe_search만 격일, newsletter·newsroom은 매일)
  오전 07시 한국·일본 / 오후 14시 중국·동남아 / 저녁 21시 글로벌(영어)
    → scripts/vibe_search.py (Claude Sonnet web_search, 해당 시간대 지역 순차)
    → 품질 게이트 (validate_candidates → URL 생존 확인)
    → Discord 5개 지역 채널 알림 + Supabase radar_items upsert
    → 대시보드 큐레이션: https://nvl-vibe-radar.vercel.app/
```

| 지역 | 언어 | Discord 채널 | Secret |
|------|------|-------------|--------|
| 한국 | 한국어 | `#korea_vibe` | `DISCORD_KOREA_WEBHOOK` |
| 글로벌 | 영어 | `#global_vibe` | `DISCORD_GLOBAL_EN_WEBHOOK` |
| 중국 | 중국어 | `#vibe-china` | `DISCORD_CHINA_WEBHOOK` |
| 일본 | 일본어 | `#vibe-japan` | `DISCORD_JAPAN_WEBHOOK` |
| 동남아 | 영어+현지 | `#asia_vibe` | `DISCORD_SOUTHEAST_ASIA_WEBHOOK` |

- **엔진**: `scripts/vibe_search.py` — Claude Sonnet `web_search` 서버사이드 도구(스트리밍 호출). 지역당 최소 1~최대 5건, 기준 충족 후보 없으면 그날은 생략.
- **적재**: `scripts/supabase_writer.py` — REST API upsert → `radar_items`. env: `SUPABASE_URL`, `SUPABASE_KEY` (GitHub Secrets). nvl-vibe-radar 자체 수집기는 2026-06-09 폐기 — 풀을 채우는 수집기는 **vibe_search(웹)·newsletter_ingest(구독 뉴스레터, §1-1)·newsroom_ingest(기업 뉴스룸 RSS, §1-2) 3개**이고 radar는 조회·큐레이션 대시보드(collector/region 필터).
- **중복 제거**: `seen-titles.txt` + Supabase URL 중복 체크.
- **태깅**: 7렌즈 멀티태깅(`fan-behavior` `consumer-behavior` `ent-deals` `ip-business` `artist-ownership` `tech-issues` `taste-values`, `topics` 배열). `taste-values`(취향·가치)는 **세대를 가로지르는** 취향/가치 신호 — 지속가능·로컬·디깅 문화·시티팝/Y2K 리바이벌·앰비언트·취향 공동체 등(엔터 밖 패션·뷰티·F&B·여행·리테일 포함). 구 `gen-z-lifestyle`(Z세대 인구통계 축)을 2026-06-17 재정의(메타 마케팅 서밋 "세대 말고 취향·가치관" 명제 반영). **키 동기화 필수** — 같은 풀(`radar_items.topics`)을 쓰는 `newsletter_ingest.py`·`newsroom_ingest.py`의 `TOPIC_KEYS`, `nvl-vibe-radar` 대시보드(`app.py` VALID_TOPICS·`dashboard.html` 필터/TOPICS/CROSS_CUL)도 함께 변경. 대시보드는 과거 누적 `gen-z-lifestyle` 항목을 alias로 호환(마이그레이션 무용).
- **출력 언어**: 모든 외국어 기사 제목은 한국어 번역. JSON 파싱은 `_parse_json_robust()` 3단계 폴백(원본→수리→개별 객체 추출).
- **개별 테스트**: `ai-news-daily.yml`의 `workflow_dispatch` region input (all/korea/global-en/china/japan/southeast-asia).
- **실패 경보 (2026-06-17 신설)**: 지역 스텝이 검색 실패(web_search API·코드 에러)로 끝나면 `scripts/notify_region_failure.py`가 woojin@에 메일. **0건(정상)과 실패를 종료코드로 구분** — vibe_search가 검색 실패만 `exit 1`, 워크플로가 각 지역 `outcome`을 모아 `failure`만 통지(정상 0건엔 메일 안 감). 지역 스텝이 `continue-on-error`라 잡 전체는 success로 떠서 `gh run`·디스코드에 침묵 실패가 안 보이던 문제(06-15~16 글로벌 이틀 공백)의 능동 경보 장치. 구 `|| echo`(항상 exit 0으로 실패를 가리던 것)는 제거.

## 1-1. 뉴스레터 수집기 (collector='newsletter', 2026-06-15 신설)

vibe_search(웹 수집)와 **같은 풀(`radar_items`)을 공유하는 두 번째 수집기.** 대표의 뉴스레터·AI서비스 전용 계정(tmifmdj@gmail.com)으로 구독하는 정예 뉴스레터를 소스 풀에 합친다.

- **엔진**: `scripts/newsletter_ingest.py` — Gmail **IMAP 앱비밀번호**(stdlib `imaplib`, OAuth·검증 불필요) → allowlist 발신자의 최근 메일 → 본문 추출·추적URL 복원(base64 경로 디코드) → Claude haiku 분류(7렌즈 topics + 한국어 요약) → upsert. 지역은 발신자별 고정 힌트(본문 분류 비의존 — 매체별 지역이 고정).
- **allowlist**: `sources_newsletters.json` — 발신자 27곳 = 엔터 직결 20(기존 12 + 2026-06-29 스윕 보강 8: Music Business Worldwide·CMU·NME·Consequence·Luminate·Media Innovation·Tokyo Weekender·캐릿) + **`broad` 7**(Bloomberg·The Information·WIRED·New Yorker·Vox·NPR·Stratechery — 일반·테크·비즈 종합 매체). **`broad:true`** 소스는 `classify`가 is_entertainment 게이트를 넓혀 *다른 영역의 교차 신호*까지 채택(순수 하드뉴스만 제외) — "음악·엔터 밖에서 교차성·인사이트 발굴" 대표 지시(2026-06-29). vibe_search의 도메인 화이트리스트 철학과 동일 — **발신자**로 고신호 유지(받은편지함 대부분이 노이즈라 탭 통째 수집 안 함). 발신자 추가·제거는 이 JSON만 편집.
- **스케줄**: `.github/workflows/newsletter-ingest.yml` 하루 2회 — **09:30 KST**(아침 클러스터: 빌보드·롱블랙·국내 일간 08시) + **23:00 KST**(저녁 클러스터: SCMP·Jing 2판·Nanjing·Marion) + `workflow_dispatch`(lookback_days). 받은편지함 실측 도착 시각 기반(2026-06-15). lookback 2일+URL dedup이라 2회 겹쳐도 안전(각 런은 직전 런 이후 신규만 적재). **Discord 미포스팅 — 대시보드 전용.** `total_score=0`(사전 큐레이션 소스라 점수 비중 낮음), 대시보드에 `📬` 출처 배지.
- **시크릿**: `GMAIL_USER`·`GMAIL_APP_PASS`(IMAP 앱비밀번호) 신규. ANTHROPIC은 `ANTHROPIC_API_KEY_WEEKLY_BRIEFING` 재사용.
- **중복 제거**: 최근 14일 newsletter URL 집합(Supabase 조회) + URL upsert(merge-duplicates).

## 1-2. 뉴스룸 수집기 (collector='newsroom', 2026-06-16 신설)

vibe_search·newsletter와 **같은 풀을 공유하는 세 번째 수집기.** 주요 엔터·미디어·IP홀더 기업의 뉴스룸/블로그 RSS·Atom 피드에서 1차 발표를 가져온다.

- **엔진**: `scripts/newsroom_ingest.py` — RSS·Atom 피드 fetch(stdlib `xml.etree`, 외부 feedparser 불필요) → 룩백 내 항목 → Claude haiku 분류(7렌즈 + 한국어 요약) → upsert. 지역은 소스별 고정 힌트. RSS(`item`)·Atom(`entry`) 양식 모두 파싱.
- **allowlist**: `sources_newsrooms.json` — 피드 검증된 9소스(Disney·Netflix·Apple·Spotify·YouTube·UMG·WMG·Sony Music·Toei). 피드가 `_`로 시작하면 비활성. 추가·제거는 이 JSON만 편집(단 **피드 URL은 실제 fetch로 유효 XML 검증 후** 등재 — 죽은 피드, 또는 헤더만 주고 본문 빈 깡통[예: Sanrio] 주의).
- **피드 없는 IP홀더: allowed_domains 사고 → 검색 키워드 흡수로 전환 (2026-06-17)**: Sony·WBD·NBCU·Nintendo·Bandai·Crunchyroll·Pokémon·Paramount를 `vibe_search.py` global-en `allowed_domains`에 넣어 웹검색으로 흡수하려 했으나(2026-06-16), `sony.com` 등이 Anthropic 크롤러 차단 도메인이라 **차단 도메인이 하나만 끼어도 web_search API가 요청 전체를 400 거부** → 글로벌 검색이 06-15~16 이틀 전량 실패(`continue-on-error`라 잡은 success로 가려짐). 8개 전부 제거해 원복. **차단 도메인은 `allowed_domains`에 넣을 수 없다 — 재시도하려면 `probe_domains.py`로 사전 검증 후 통과분만.** 피드 있는 IP홀더(Sony Music 등)는 newsroom 수집기가 흡수. **피드 없는 곳(Sony 그룹·Nintendo·Bandai·Crunchyroll·WBD·NBCU·Paramount)은 `vibe_search.py` `search_terms`에 회사 키워드 추가로 흡수**(2026-06-17 전환, global-en ent-deals·ip-business + japan ip-business). domains 아니라 400 안전 — 회사 자체 PR이 아니라 신뢰 매체 내 해당 기업 보도(분석·딜)를 적극 검색.
- **스케줄**: `.github/workflows/newsroom-ingest.yml` 매일 **10:00 KST**(01:00 UTC — 미국 뉴스룸 전일분 흡수) + `workflow_dispatch`(lookback_days). 뉴스룸은 저빈도 발행이라 **lookback 7일**(뉴스레터 2일보다 김). Discord 미포스팅·대시보드 전용, `total_score=0`, 대시보드 `📰` 출처 배지.
- **시크릿**: `SUPABASE_*`·ANTHROPIC(`ANTHROPIC_API_KEY_WEEKLY_BRIEFING`). 피드는 무인증이라 신규 시크릿 없음.
- **중복 제거**: 최근 30일 newsroom URL 집합(Supabase 조회) + URL upsert.

## 2. 품질 게이트 (2026-06-10)

Anthropic `web_search` 도구에 날짜 필터 파라미터가 없어 코드 레벨로 강제한다.

1. 프롬프트에 오늘 날짜(KST)+컷오프 주입, `published_date` 필드 요구
2. **출처 화이트리스트** — 지역별 `allowed_domains`(주요 일간지·주간지·매거진·전문지)로 web_search 검색 자체를 제한 + 코드 검증에서 목록 외 출처 제외 (AI타임스·에너지신문류 보도자료 재가공 매체 차단, 2026-06-10 대표 지시). `BLOCKED_DOMAINS`(나무위키)는 별개 방어선
3. `validate_candidates()` — 필수 필드·한국어 요약·**4지표 점수 재계산(≥4)**·발행일 48시간 컷(`MAX_AGE_HOURS` env로 조정). 점수 4지표 = 소재적합·캐러셀적합·출처신뢰·**교차정체성**(각 0~2, 만점 8, 2026-06-17 추가). 임계 `MIN_TOTAL_SCORE` 기본 4(만점의 50%, env 조정 가능 — 0건 반복 시 3으로 완화). **발행일 미상은 제외**(신뢰성 — 2026-06-10 대표 지시). 0건이 반복되면 `ALLOW_UNDATED=1`로 임시 완화(플래그 게재)
4. 점수순 정렬(동점 시 reliability→발행일 확인분 우선) → 배치 내 중복 제거 → **도메인당 2건 상한**(`MAX_PER_DOMAIN`, 1차 패스 — 한 매체 독식 방지·동남아 방콕포스트 편중 대응, 2026-06-17. 미달 시 2차 패스에서 상한 풀어 건수 보존) → URL 생존 확인(`check_url_alive`, 404/없는 도메인 차단) → 최대 5건
4. 드롭 통계를 Discord 헤더 subtext + GitHub Actions Step Summary에 노출

단위 테스트: `scripts/test_quality_gate.py`.

## 3. 절대 금지 (위반 시 작업 중단)

- **기억·지식 기반으로 뉴스를 만들어내지 않는다.** 훈련 데이터의 사실, 그럴듯한 추정, 생성된 가짜 URL 일체 금지.
- 모든 기사는 **세션 내 검색·fetch로 직접 확인한 것**만. URL 검증 불가능하면 제외하고, 건수가 부족해도 채우지 않는다.
- "지식 기반 소스 활용", "URL 검증 생략" 같은 판단을 스스로 내리지 않는다.

## 4. 주간 리포트 드롭 (별개 흐름, 존속)

뉴타입컬처클럽 자료실용 산업 리포트 큐레이션. 일일 뉴스 수집과 별개.

- 생성: `/report-scan` 스킬(미네바) → `drops/YY.MM.DD-주간리포트드롭.md` (weekly-vibe/drops/ + ecri-ceo-staff/operations/ 2곳 저장)
- 발송 로직: `scripts/send_report_drop.py` — 최신 드롭 찾기·정제(HTML주석 제거·2000자 컷)·Discord 전송. 정시·백업 공용 모듈(stdlib). 과거 YAML 인라인 heredoc 들여쓰기로 startup_failure 났던 이력 → 스크립트 분리로 재발 차단(2026-06-15).
- 포스팅(정시): `.github/workflows/discord-report-drop.yml` — 매주 월요일 **10:17 KST**(정시 :00 = GitHub 고부하 슬롯 회피).
- 백업 감시: `.github/workflows/report-drop-watchdog.yml` — 월 **10:40 KST** 점검 → 정시 누락 시 직접 재발송 + woojin@ 메일 알림(`check_drop_posted.py` 발송판정·`send_drop_alert.py` 메일). GitHub cron best-effort 누락 대비. 중복 발송·지연 레이스 가드 포함.

## 5. 파일 구조

```
weekly-vibe/
├── CLAUDE.md                    ← 이 파일
├── scripts/
│   ├── vibe_search.py           ← 수집 엔진 v3 (5지역)
│   ├── supabase_writer.py       ← radar_items upsert
│   ├── send_report_drop.py      ← 리포트 드롭 발송 공용 모듈 (정시+백업)
│   ├── check_drop_posted.py     ← 백업: 오늘 발송 여부 판정 (gh 런 이력)
│   ├── send_drop_alert.py       ← 백업: 리포트 드롭 누락 시 woojin@ 메일 알림
│   ├── notify_region_failure.py ← 일일 수집: 지역 검색 실패 시 woojin@ 메일 경보
│   └── test_quality_gate.py    ← 품질 게이트 단위 테스트
├── .github/workflows/
│   ├── ai-news-daily.yml        ← 매일 3시간대 수집(오전 한·일/오후 중·동남아/저녁 글로벌)
│   ├── discord-report-drop.yml  ← 월 10:17 KST 정시 리포트 드롭
│   └── report-drop-watchdog.yml ← 월 10:40 KST 백업(누락 시 재발송+메일)
├── drops/                       ← 주간 리포트 드롭 마크다운
├── seen-titles.txt              ← 중복 제거 캐시
└── NEWSPAPER_*.html 등          ← 구 주간 브리핑 발행물 (역사 아카이브, 신규 생성 금지)
```

## 6. 변경 이력

- 2026-06-29: **리포트 드롭 403 진짜 원인 = User-Agent** (≠ 죽은 웹훅). `send_report_drop.py`가 urllib 기본 UA(`Python-urllib`)로 POST → Discord Cloudflare가 `error 1010`으로 차단(06-22 이후 규칙 강화 추정). 명시 UA(`Mozilla/…`) 헤더 추가로 해결, 게시→삭제 실전송으로 end-to-end 검증. 웹훅 자체는 유효였음(대표 신규 발급분으로 `DISCORD_REPORT_WEBHOOK_URL` secret 갱신). vibe_search `send_to_discord`는 `requests`(python-requests UA)라 현재 통과 중 — 차단 강화 대비 명시 UA는 후속 권장(현재 미적용).
- 2026-06-29: 뉴스레터 allowlist 12→27 (Gmail 전수 스윕). ① 고신호 8(MBW·CMU·NME·Consequence·Luminate·Media Innovation·Tokyo Weekender·캐릿). ② **교차성 `broad` 7**(Bloomberg·The Information·WIRED·New Yorker·Vox·NPR·Stratechery) — `classify(broad=True)`로 is_entertainment 게이트 확대(엔터·소비·문화 함의 또는 교차성 있으면 채택, 순수 하드뉴스만 제외). 대표 "음악 집중 X, 무관한 영역에서 교차성·인사이트" 지시. §1-1 참조.
- 2026-06-29: **vibe_search 격일 전환** (`ai-news-daily.yml` cron 3개 `*/2` + step if 동기화). Sonnet web_search가 토큰 비용 주범인데 수확은 풀의 20%뿐 → 격일로 ~절반 절감. newsletter·newsroom(haiku·고수확·robots.txt 우회)은 **매일 유지**. 한·글·일 나이컷 72→120h(격일 갭+주말 보강), 중·동남아는 168h 유지. ※커버리지 정공법은 차단 일간지(조선·중앙·FT·Reuters 등 robots.txt 막힘)를 newsletter 구독으로 흡수 — 발신자만 `sources_newsletters.json`에 추가.
- 2026-06-08: vibe_search v3 — 주제 기반(6토픽)에서 지역·언어 기반(5지역)으로 재설계. 구 RSS 워크플로 5개 삭제.
- 2026-06-09: Discord 5지역 웹훅 통합, Supabase 동시 적재, radar 자체 수집기 폐기, 주간 브리핑 발행 폐기(`weekly-briefing.yml`·`discord-notify.yml` 삭제).
- 2026-06-10: 품질 게이트 추가(48시간 컷·URL 생존 확인). CLAUDE.md 재작성 — 구 주간 브리핑 제작 가이드 제거, 수집 엔진 정본으로 전환.
- 2026-06-15: 리포트 드롭 워크플로 YAML 깨짐(인라인 heredoc) 수정 — 6/4부터 startup_failure로 미발송이던 것 복구. 발송 로직을 `scripts/send_report_drop.py`로 분리, cron 10:00→10:17(정시 고부하 회피), 백업 감시 워크플로(`report-drop-watchdog.yml`, 월 10:40) 신설 — 정시 누락 시 자동 재발송 + 메일 알림.
- 2026-06-17: 수집 시간대 분산 — `ai-news-daily.yml` 단일 cron(07:00 일괄)에서 cron 3개로(07:00 한·일 / 14:00 중·동남아 / 21:00 글로벌). 지역별 현지 발행 리듬에 맞춰 신선도↑. 단일 워크플로 유지(각 step `if`가 `github.event.schedule`·수동 region input 분기), skip 지역은 outcome=skipped라 실패 경보 무영향.
- 2026-06-17: 도메인 다양성 — `select_candidates`에 도메인당 2건 상한(`MAX_PER_DOMAIN`) 추가. 한 매체(동남아 방콕포스트)가 점수순 5건을 독식하던 구조 차단. 2-패스(1차 상한 적용 → 미달 시 2차 상한 해제)로 도메인 얕은 지역 건수 보존. `test_quality_gate.py` 케이스 추가.
- 2026-06-17: **세대 축 → 취향·가치 축 개편**(메타 마케팅 서밋 "세대 말고 취향·가치관" 명제 반영, 근거 `ecri-marketing/26.06.17-메타-마케팅-서밋-2026-인사이트.md` §4). ① 7번째 렌즈 `gen-z-lifestyle`(Z세대)→`taste-values`(취향·가치): TOPIC_LABELS·5지역 search_terms(각 지역 언어)·프롬프트 문단 모두 세대 라벨에서 세대 횡단 취향/가치 신호로 교체. ② 폐기된 `z_lifestyle_digest.py`의 ③교차정체성 지표를 4번째 스코어링 지표(`cross_identity`, 0~2)로 부활 — SCORE_KEYS 4개·만점 8·`MIN_TOTAL_SCORE` 3→4(env 조정)·Discord 🟢 배지 5→6·`_score_indicators` 반영. ③ 같은 풀 공유하는 `newsletter_ingest`·`newsroom_ingest`·`nvl-vibe-radar` 대시보드 키 동기화(대시보드는 구 키 alias 호환). 미해결: `radar_items`에 `cross_identity` 개별 컬럼 없음(total_score엔 합산 반영, 개별 표시 필요 시 마이그레이션 후속). 미배포(대표 검토 대기).
