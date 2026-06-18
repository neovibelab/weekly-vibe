#!/usr/bin/env python3
"""품질 게이트 단위 테스트 (API 호출 없음). 수동 실행용:
python scripts/test_quality_gate.py
"""
import datetime
import sys

import vibe_search as vs


def test_parse_date():
    assert vs._parse_date("2026-06-09") == datetime.date(2026, 6, 9)
    assert vs._parse_date("2026-6-9") == datetime.date(2026, 6, 9)
    assert vs._parse_date("발행일: 2026-06-09 오전") == datetime.date(2026, 6, 9)
    assert vs._parse_date(None) is None
    assert vs._parse_date("") is None
    assert vs._parse_date("unknown") is None
    assert vs._parse_date("2026-13-45") is None
    print("  _parse_date OK")


def _cand(**over):
    # 4지표(각 0~2) 만점 8 — 2026-06-17 cross_identity 추가. 기본 합 4 = 임계(4) 통과.
    base = {
        "title": "테스트 기사 제목",
        "url": "https://example.com/a",
        "source": "테스트",
        "topics": ["tech-issues"],
        "summary": "한국어 요약입니다.",
        "published_date": "2026-06-09",
        "newsletter_fit": 1,
        "carousel_fit": 1,
        "reliability": 1,
        "cross_identity": 1,
        "total_score": 4,
    }
    base.update(over)
    return base


def test_validate():
    today = datetime.date(2026, 6, 10)
    cutoff = datetime.date(2026, 6, 8)

    # 정상 통과
    valid, drops = vs.validate_candidates([_cand()], cutoff, today)
    assert len(valid) == 1 and sum(drops.values()) == 0

    # 발행일 미상 → 기본 제외 (2026-06-10 대표 지시)
    valid, drops = vs.validate_candidates([_cand(published_date=None)], cutoff, today)
    assert len(valid) == 0 and drops["no_date"] == 1

    # ALLOW_UNDATED=1이면 플래그(None)로 게재 유지
    vs.ALLOW_UNDATED = True
    try:
        valid, drops = vs.validate_candidates([_cand(published_date=None)], cutoff, today)
        assert len(valid) == 1 and valid[0]["published_date"] is None
    finally:
        vs.ALLOW_UNDATED = False

    # 기한 경과 → 제외
    valid, drops = vs.validate_candidates([_cand(published_date="2026-06-01")], cutoff, today)
    assert len(valid) == 0 and drops["stale"] == 1

    # 미래 발행일 → 제외 (할루시네이션 의심)
    valid, drops = vs.validate_candidates([_cand(published_date="2026-07-01")], cutoff, today)
    assert len(valid) == 0 and drops["future"] == 1

    # 점수 미달 → 제외
    valid, drops = vs.validate_candidates(
        [_cand(newsletter_fit=0, carousel_fit=0, reliability=1, total_score=1)], cutoff, today
    )
    assert len(valid) == 0 and drops["score"] == 1

    # 점수 합산 오류 → 4지표 재계산 후 통과 (1+1+1+1=4인데 total_score=6으로 옴)
    valid, drops = vs.validate_candidates([_cand(total_score=6)], cutoff, today)
    assert len(valid) == 1 and valid[0]["total_score"] == 4

    # 4지표 합산: cross_identity 포함 4개 키를 모두 더한다 (2026-06-17)
    valid, drops = vs.validate_candidates(
        [_cand(newsletter_fit=2, carousel_fit=2, reliability=2, cross_identity=2, total_score=0)],
        cutoff, today,
    )
    assert len(valid) == 1 and valid[0]["total_score"] == 8

    # 임계 경계: 새 MIN_TOTAL_SCORE=4 기준 — 합 3은 제외, 4는 통과
    valid, drops = vs.validate_candidates(
        [_cand(newsletter_fit=1, carousel_fit=1, reliability=1, cross_identity=0, total_score=3)],
        cutoff, today,
    )
    assert len(valid) == 0 and drops["score"] == 1
    valid, drops = vs.validate_candidates(
        [_cand(newsletter_fit=1, carousel_fit=1, reliability=1, cross_identity=1, total_score=4)],
        cutoff, today,
    )
    assert len(valid) == 1

    # cross_identity 누락(구 3지표 응답 호환) → 0으로 계산, 합 3은 제외
    legacy = _cand(newsletter_fit=1, carousel_fit=1, reliability=1, total_score=3)
    del legacy["cross_identity"]
    valid, drops = vs.validate_candidates([legacy], cutoff, today)
    assert len(valid) == 0 and drops["score"] == 1

    # 영어 요약 → 제외
    valid, drops = vs.validate_candidates(
        [_cand(summary="English only summary.")], cutoff, today
    )
    assert len(valid) == 0 and drops["format"] == 1

    # URL 형식 불량 → 제외
    valid, drops = vs.validate_candidates([_cand(url="notaurl")], cutoff, today)
    assert len(valid) == 0 and drops["format"] == 1

    # 차단 도메인(나무위키) → 제외 (서브도메인 포함)
    for bad in ("https://namu.wiki/w/BTS", "https://www.namu.wiki/w/BTS"):
        valid, drops = vs.validate_candidates([_cand(url=bad)], cutoff, today)
        assert len(valid) == 0 and drops["blocked"] == 1
    # 유사 도메인은 통과 (suffix 오탐 방지)
    valid, drops = vs.validate_candidates([_cand(url="https://notnamu.wiki/a")], cutoff, today)
    assert len(valid) == 1

    # 화이트리스트: 목록 외 출처 제외, 목록 내(서브도메인 포함) 통과
    allowed = ("hankyung.com", "yna.co.kr")
    valid, drops = vs.validate_candidates(
        [_cand(url="https://www.aitimes.com/news/1")], cutoff, today, allowed
    )
    assert len(valid) == 0 and drops["blocked"] == 1
    valid, drops = vs.validate_candidates(
        [_cand(url="https://tenasia.hankyung.com/article/1")], cutoff, today, allowed
    )
    assert len(valid) == 1
    print("  validate_candidates OK")


def test_date_from_url():
    import datetime
    assert vs._date_from_url("https://k.com/article/2026/06/09/abc") == datetime.date(2026, 6, 9)
    assert vs._date_from_url("https://k.com/news/2026-06-09-title") == datetime.date(2026, 6, 9)
    assert vs._date_from_url("https://k.com/view/20260609123456") == datetime.date(2026, 6, 9)
    assert vs._date_from_url("https://k.com/articleView.html?idxno=456980") is None
    assert vs._date_from_url("https://k.com/plain-path") is None
    # 무효 날짜(13월)는 무시
    assert vs._date_from_url("https://k.com/20261340/") is None
    print("  _date_from_url OK")


def test_validate_url_date_fallback():
    import datetime
    today = datetime.date(2026, 6, 10)
    cutoff = datetime.date(2026, 6, 8)
    # 모델이 발행일 못 채워도 URL에서 추출해 통과
    valid, drops = vs.validate_candidates(
        [_cand(published_date=None, url="https://k.com/article/2026/06/09/abc")],
        cutoff, today,
    )
    assert len(valid) == 1 and valid[0]["published_date"] == "2026-06-09"
    # URL 날짜가 컷오프 이전이면 기한경과로 제외
    valid, drops = vs.validate_candidates(
        [_cand(published_date=None, url="https://k.com/article/2026/04/29/abc")],
        cutoff, today,
    )
    assert len(valid) == 0 and drops["stale"] == 1
    print("  validate URL 날짜 폴백 OK")


def test_url_alive():
    assert vs.check_url_alive("https://www.google.com") is True
    assert vs.check_url_alive("https://www.google.com/nonexistent-page-404-test-xyz") is False
    assert vs.check_url_alive("https://this-domain-does-not-exist-xyz123456.com/a") is False
    print("  check_url_alive OK")


def test_score_indicators():
    # 4지표 — 교차정체성 인디케이터가 Discord 출력에 반영되는지 (2026-06-17)
    full = vs._score_indicators(
        _cand(newsletter_fit=1, carousel_fit=1, reliability=1, cross_identity=1)
    )
    assert full == ["소재적합", "캐러셀적합", "출처신뢰", "교차정체성"]
    # cross_identity=0이면 교차정체성 미표시
    partial = vs._score_indicators(
        _cand(newsletter_fit=1, carousel_fit=0, reliability=0, cross_identity=0)
    )
    assert partial == ["소재적합"]
    print("  _score_indicators OK")


def test_select_batch_dedup():
    # 네트워크 차단: URL 생존 확인을 항상 통과로 모킹
    original = vs.check_url_alive
    vs.check_url_alive = lambda url: True
    try:
        # 같은 URL 2벌 + 제목만 살짝 다른 1벌 + 별개 기사 1벌
        batch = [
            _cand(title="AI 작곡 저작권 등록 금지 — KOMCA 강경 방침", url="https://a.com/1", total_score=5, reliability=2, carousel_fit=2),
            _cand(title="AI 작곡 저작권 등록 금지 — KOMCA 방침과 업계 논란", url="https://a.com/1", total_score=4),
            _cand(title="AI 작곡 저작권 등록 금지 — KOMCA 강경 방침 확산", url="https://a.com/2", total_score=4),
            _cand(title="하이브 브랜드평판 1위", url="https://b.com/9", total_score=4),
        ]
        selected, dead, dups = vs.select_candidates(batch)
        assert dups == 2, f"배치 중복 2건이어야 함: {dups}"
        assert len(selected) == 2
        urls = {c["url"] for c in selected}
        assert urls == {"https://a.com/1", "https://b.com/9"}
        # 점수순 정렬 확인 (5점이 먼저)
        assert selected[0]["total_score"] == 5
    finally:
        vs.check_url_alive = original
    print("  select_candidates 배치 중복 제거 OK")


def test_select_domain_cap():
    # 도메인 편중 방지: 한 매체가 상위 점수를 독식해도 1차에서 도메인당 cap까지만.
    # 미달분은 2차 패스로 채워 건수는 보존. (이 테스트는 MAX_CANDIDATES=5 기준)
    original = vs.check_url_alive
    original_cap = vs.MAX_CANDIDATES
    vs.MAX_CANDIDATES = 5
    vs.check_url_alive = lambda url: True
    try:
        allowed = ["bangkokpost.com", "rappler.com", "kompas.com"]
        # 방콕포스트 4건(상위 점수) + 라플러 1 + 콤파스 1. www. 서브도메인 변형 포함.
        # 제목은 서로 충분히 달라 cross_dup에 안 걸림 — 도메인 cap만 격리 검증.
        batch = [
            _cand(title="태국 콘서트 시장 30% 성장 전망", url="https://www.bangkokpost.com/a1", total_score=6),
            _cand(title="방탄소년단 방콕 공연 전석 매진", url="https://www.bangkokpost.com/a2", total_score=5),
            _cand(title="현지 인디 레이블 해외 진출 본격화", url="https://bangkokpost.com/a3", total_score=5),
            _cand(title="스트리밍 플랫폼 동남아 점유율 재편", url="https://www.bangkokpost.com/a4", total_score=4),
            _cand(title="라플러 단독: P-pop 산업 구조 분석", url="https://www.rappler.com/b1", total_score=3),
            _cand(title="인도네시아 음악 페스티벌 관객 신기록", url="https://www.kompas.com/c1", total_score=3),
        ]
        selected, dead, dups = vs.select_candidates(batch, allowed, max_per_domain=2)
        hosts = [vs._domain_key(c["url"], allowed) for c in selected]
        # 1차: 방콕 2 + 라플러 1 + 콤파스 1 = 4건. 2차: 방콕 1 보존 → 총 5건·방콕 3.
        assert len(selected) == 5, f"5건 선정이어야: {len(selected)} ({hosts})"
        assert "rappler.com" in hosts and "kompas.com" in hosts, f"다양성 확보 실패: {hosts}"
        assert hosts.count("bangkokpost.com") == 3, f"방콕 1차2+2차1=3이어야: {hosts}"
    finally:
        vs.check_url_alive = original
        vs.MAX_CANDIDATES = original_cap
    print("  select_candidates 도메인 cap OK")


if __name__ == "__main__":
    test_parse_date()
    test_date_from_url()
    test_validate()
    test_validate_url_date_fallback()
    test_url_alive()
    test_score_indicators()
    test_select_batch_dedup()
    test_select_domain_cap()
    print("ALL TESTS PASSED")
    sys.exit(0)
