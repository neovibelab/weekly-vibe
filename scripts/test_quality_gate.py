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
    base = {
        "title": "테스트 기사 제목",
        "url": "https://example.com/a",
        "source": "테스트",
        "topics": ["tech-issues"],
        "summary": "한국어 요약입니다.",
        "published_date": "2026-06-09",
        "newsletter_fit": 1,
        "carousel_fit": 1,
        "reliability": 2,
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

    # 점수 합산 오류 → 재계산 후 통과 (1+1+2=4인데 total_score=6으로 옴)
    valid, drops = vs.validate_candidates([_cand(total_score=6)], cutoff, today)
    assert len(valid) == 1 and valid[0]["total_score"] == 4

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


def test_url_alive():
    assert vs.check_url_alive("https://www.google.com") is True
    assert vs.check_url_alive("https://www.google.com/nonexistent-page-404-test-xyz") is False
    assert vs.check_url_alive("https://this-domain-does-not-exist-xyz123456.com/a") is False
    print("  check_url_alive OK")


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


if __name__ == "__main__":
    test_parse_date()
    test_validate()
    test_url_alive()
    test_select_batch_dedup()
    print("ALL TESTS PASSED")
    sys.exit(0)
