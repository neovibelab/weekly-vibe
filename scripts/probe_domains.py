#!/usr/bin/env python3
"""allowed_domains 접근성 프로브 — Anthropic web_search가 거부하는 도메인 검출.

일부 언론사는 robots.txt로 Anthropic 크롤러를 차단하며, 그런 도메인이
allowed_domains에 있으면 API가 요청 자체를 400으로 거부한다 (2026-06-10 실측:
조선·중앙·동아·한겨레·매경·연합). 이 스크립트는 검색을 실행하지 않는 최소
호출로 도메인 검증만 유발해 지역별 차단/통과 목록을 출력한다.

사용법 (로컬 또는 GitHub Actions domain-probe.yml):
  ANTHROPIC_API_KEY=... python scripts/probe_domains.py
"""
from __future__ import annotations

import os
import re
import sys

from anthropic import Anthropic

from vibe_search import REGIONS

# 화이트리스트 보강 후보 — 기존 목록이 차단으로 빠질 때 대체할 매체
EXTRA_CANDIDATES = {
    "korea-extra": [
        "hankookilbo.com", "seoul.co.kr", "heraldcorp.com",
        "edaily.co.kr", "asiae.co.kr", "fnnews.com", "ytn.co.kr",
    ],
    "global-extra": [
        "musically.com", "digitalmusicnews.com",
        "completemusicupdate.com", "economist.com", "axios.com",
    ],
}


def probe(client: Anthropic, domains: list[str]) -> tuple[list[str], list[str]]:
    """(차단 목록, 통과 목록) 반환. 400 메시지에서 차단 도메인을 빼며 반복."""
    blocked: list[str] = []
    pool = list(domains)
    for _ in range(5):
        if not pool:
            break
        try:
            client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=8,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 1,
                    "allowed_domains": pool,
                }],
                messages=[{"role": "user", "content": "안녕이라고만 답해"}],
            )
            break
        except Exception as exc:
            msg = str(exc)
            found = [d for d in re.findall(r"'([a-z0-9.-]+\.[a-z]{2,})'", msg) if d in pool]
            if not found:
                print(f"  예상외 오류 (도메인 미검출): {msg[:300]}")
                break
            blocked.extend(found)
            pool = [d for d in pool if d not in found]
    return blocked, pool


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY 미설정")
        return 1
    client = Anthropic()

    targets: dict[str, list[str]] = {
        key: region["allowed_domains"] for key, region in REGIONS.items()
    }
    targets.update(EXTRA_CANDIDATES)

    for name, domains in targets.items():
        blocked, ok = probe(client, domains)
        print(f"[{name}]")
        print(f"  차단({len(blocked)}): {', '.join(blocked) if blocked else '-'}")
        print(f"  통과({len(ok)}): {', '.join(ok) if ok else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
