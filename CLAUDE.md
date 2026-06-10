# weekly-vibe — 일일 수집 엔진 (vibe_search v3)

> **역할 (2026-06-09 전환)**: 엔터·문화 산업 뉴스의 **일일 자동 수집 엔진**. 매일 07:00 KST 5개 지역을 수집해 Discord 5개 지역 채널에 알리고 Supabase `radar_items`에 적재한다.
>
> **주간 브리핑(NEWSPAPER HTML) 발행은 2026-06-09 폐기.** 과거 발행물(`NEWSPAPER_*.html` · `SPECIAL_*.html` · `index.html` · `preview/`)은 역사 아카이브로만 보존한다. `weeklybriefing.vercel.app`은 배포 중단 대상, 제작 스킬(`.claude/skills/weekly-vibe/`)은 2026-06-10 삭제. 구 제작 가이드(태그 시스템·디자인 시스템·검증 체크리스트)가 필요하면 이 파일의 git 히스토리(2026-06-10 이전) 참조.
>
> **정본 관계**: 운영 워크플로(무엇을·언제·왜)는 [`ecri-ceo-staff/operations/26.06.08-daily-weekly-workflow.md`](../ecri-ceo-staff/operations/26.06.08-daily-weekly-workflow.md) §2 · 아키텍처·deprecated는 루트 [`CLAUDE.md`](../CLAUDE.md) §5.

---

## 1. 수집 파이프라인

```
매일 07:00 KST (GitHub Actions ai-news-daily.yml)
    → scripts/vibe_search.py (Claude Sonnet web_search, 5지역 순차)
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
- **적재**: `scripts/supabase_writer.py` — REST API upsert → `radar_items`. env: `SUPABASE_URL`, `SUPABASE_KEY` (GitHub Secrets). nvl-vibe-radar 자체 수집기는 2026-06-09 폐기 — vibe_search가 유일한 수집 엔진이고 radar는 조회·큐레이션 대시보드(collector/region 필터).
- **중복 제거**: `seen-titles.txt` + Supabase URL 중복 체크.
- **태깅**: 6렌즈 멀티태깅(`fan-behavior` `consumer-behavior` `ent-deals` `ip-business` `artist-ownership` `tech-issues`, `topics` 배열) + 비엔터 교차 소재는 `cross-industry` 병기.
- **출력 언어**: 모든 외국어 기사 제목은 한국어 번역. JSON 파싱은 `_parse_json_robust()` 3단계 폴백(원본→수리→개별 객체 추출).
- **개별 테스트**: `ai-news-daily.yml`의 `workflow_dispatch` region input (all/korea/global-en/china/japan/southeast-asia).

## 2. 품질 게이트 (2026-06-10)

Anthropic `web_search` 도구에 날짜 필터 파라미터가 없어 코드 레벨로 강제한다.

1. 프롬프트에 오늘 날짜(KST)+컷오프 주입, `published_date` 필드 요구
2. `validate_candidates()` — 필수 필드·한국어 요약·점수 재계산(≥3)·발행일 48시간 컷(`MAX_AGE_HOURS` env로 조정). 발행일 미상은 제외하지 않고 "발행일 미상" 플래그로 게재(한국 언론사 등 page_age 미제공 사이트가 많아 하드 컷이면 전멸 — 2026-06-10 실측 2연속 0건). 확인된 구식·미래 날짜만 제외
3. 점수순 정렬 후 URL 생존 확인(`check_url_alive`, 404/없는 도메인 차단)
4. 드롭 통계를 Discord 헤더 subtext + GitHub Actions Step Summary에 노출

단위 테스트: `scripts/test_quality_gate.py`.

## 3. 절대 금지 (위반 시 작업 중단)

- **기억·지식 기반으로 뉴스를 만들어내지 않는다.** 훈련 데이터의 사실, 그럴듯한 추정, 생성된 가짜 URL 일체 금지.
- 모든 기사는 **세션 내 검색·fetch로 직접 확인한 것**만. URL 검증 불가능하면 제외하고, 건수가 부족해도 채우지 않는다.
- "지식 기반 소스 활용", "URL 검증 생략" 같은 판단을 스스로 내리지 않는다.

## 4. 주간 리포트 드롭 (별개 흐름, 존속)

뉴타입컬처클럽 자료실용 산업 리포트 큐레이션. 일일 뉴스 수집과 별개.

- 생성: `/report-scan` 스킬(미네바) → `drops/YY.MM.DD-주간리포트드롭.md`
- 포스팅: `.github/workflows/discord-report-drop.yml` — 매주 월요일 10:00 KST Discord 드롭

## 5. 파일 구조

```
weekly-vibe/
├── CLAUDE.md                    ← 이 파일
├── scripts/
│   ├── vibe_search.py           ← 수집 엔진 v3 (5지역)
│   ├── supabase_writer.py       ← radar_items upsert
│   └── test_quality_gate.py    ← 품질 게이트 단위 테스트
├── .github/workflows/
│   ├── ai-news-daily.yml        ← 매일 07:00 KST 수집
│   └── discord-report-drop.yml  ← 월요일 10:00 KST 리포트 드롭
├── drops/                       ← 주간 리포트 드롭 마크다운
├── seen-titles.txt              ← 중복 제거 캐시
└── NEWSPAPER_*.html 등          ← 구 주간 브리핑 발행물 (역사 아카이브, 신규 생성 금지)
```

## 6. 변경 이력

- 2026-06-08: vibe_search v3 — 주제 기반(6토픽)에서 지역·언어 기반(5지역)으로 재설계. 구 RSS 워크플로 5개 삭제.
- 2026-06-09: Discord 5지역 웹훅 통합, Supabase 동시 적재, radar 자체 수집기 폐기, 주간 브리핑 발행 폐기(`weekly-briefing.yml`·`discord-notify.yml` 삭제).
- 2026-06-10: 품질 게이트 추가(48시간 컷·URL 생존 확인). CLAUDE.md 재작성 — 구 주간 브리핑 제작 가이드 제거, 수집 엔진 정본으로 전환.
