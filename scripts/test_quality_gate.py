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

    # 발행일 없음 → 제외
    valid, drops = vs.validate_candidates([_cand(published_date=None)], cutoff, today)
    assert len(valid) == 0 and drops["no_date"] == 1

    # 기한 경과 → 제외
    valid, drops = vs.validate_candidates([_cand(published_date="2026-06-01")], cutoff, today)
    assert len(valid) == 0 and drops["stale"] == 1

    # 미래 발행일 → 제외
    valid, drops = vs.validate_candidates([_cand(published_date="2026-07-01")], cutoff, today)
    assert len(valid) == 0 and drops["no_date"] == 1

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
    print("  validate_candidates OK")


def test_url_alive():
    assert vs.check_url_alive("https://www.google.com") is True
    assert vs.check_url_alive("https://www.google.com/nonexistent-page-404-test-xyz") is False
    assert vs.check_url_alive("https://this-domain-does-not-exist-xyz123456.com/a") is False
    print("  check_url_alive OK")


if __name__ == "__main__":
    test_parse_date()
    test_validate()
    test_url_alive()
    print("ALL TESTS PASSED")
    sys.exit(0)
