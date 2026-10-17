#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""고유명사 게이트 - 프롬프트 문구와 `n:` 태그 변환을 한 자리에 둔다 (2026-10-17 신설).

## 왜 모았나

`names` 필드는 2026-10-14에 `gnews_ingest`에만 붙었다. 두뇌 `raw/names/` 고유명사
사전의 **후보 공급**이고, 산출은 `../scripts/wiki-candidates.py`가 `n:` 태그를 읽어
사전에 없는 것만 올린다. 10-17에 나머지 세 수집기(newsletter · newsroom · interview)로
넓히면서 **문구와 변환을 복제하지 않고 여기로 뽑았다.**

> 복제하지 않고 import한다 - 두 벌이 되면 반드시 어긋난다 (`regate.py` 머리말)

**왜 그게 이 필드에 특히 중요한가** - 역매칭은 **글자가 같아야** 센다. 수집기마다
문구가 갈리면 한쪽은 「카카오엔터」, 다른 쪽은 「카카오엔터테인먼트」를 내고 **같은
대상이 두 줄로 쌓인다.** 사전에 올리는 순간 표기가 확정되므로 그 전까지가 위험 구간이다.

## 쓰는 법

    from name_gate import NAMES_RULE, name_tags       # scripts/를 sys.path에 넣은 뒤
    prompt = ... + NAMES_RULE + ...                    # 게이트 프롬프트에 끼운다
    tags = topics + name_tags(data.get("names"))       # `tags` 배열에 얹는다

`parse_obj` 키 명세에 `"names": "str"`을 같이 넣는다 - **배열이 아니라 쉼표로 이은
문자열**로 받는다(`llm_json.py`의 3층 방어가 list를 못 건진다. 2026-10-14 판단).
"""

from __future__ import annotations

# 프롬프트 문구 - 수집기 넷이 같은 문장을 쓴다. 고치면 넷이 같이 바뀐다.
NAMES_RULE = (
    "names: **고유명사**를 쉼표로 이어 최대 3개. 회사 · 단체 · 인물 · 아티스트 · 작품 · 행사 이름만.\n"
    "표기는 `title_ko`와 같게 쓴다(같은 대상이 매번 같은 글자여야 뒤에서 센다).\n"
    "없으면 빈 문자열. **일반명사를 넣지 않는다** - 음악 · 스트리밍 · 저작권 · 플랫폼 · 시장 · 산업은 이름이 아니다.\n"
    "(2026-10-14 - 두뇌 `raw/names/` 고유명사 사전의 후보 공급이다. 여기서 사전에 올리지는 않는다.)\n"
)

# 일반명사가 새면 뒤에서 세는 자리가 쓰레기가 되므로 길이·개수로만 1차 방어하고
# **사전에 없는 것을 고르는 일은 산출 스크립트가 한다**(`../scripts/wiki-candidates.py`).
NAME_STOP = {"음악", "스트리밍", "저작권", "플랫폼", "시장", "산업", "콘텐츠", "엔터테인먼트",
             "아티스트", "레이블", "팬덤", "차트", "앨범", "공연", "AI", "인공지능"}


def name_tags(raw) -> list:
    """모델이 준 `names` 문자열을 `n:<이름>` 태그 최대 3개로."""
    out = []
    for piece in str(raw or "").replace("·", ",").split(","):
        n = piece.strip().strip("'\"[]")
        if not n or len(n) < 2 or len(n) > 40 or n in NAME_STOP:
            continue
        t = "n:" + n
        if t not in out:
            out.append(t)
        if len(out) >= 3:
            break
    return out


def selftest() -> int:
    """정상 1 + 실패 3. 표본 없이 넣은 검사기가 오탐을 낸 적이 있다(저장소 관례)."""
    cases = [
        ("정상(셋 이하)", "하이브, 민희진", ["n:하이브", "n:민희진"]),
        ("일반명사 제외", "음악, 스트리밍, 두나무", ["n:두나무"]),
        ("상한 3개", "A사, B사, C사, D사", ["n:A사", "n:B사", "n:C사"]),
        ("빈 값", "", []),
        ("중점 구분자·중복", "SM · SM · 카카오엔터", ["n:SM", "n:카카오엔터"]),
    ]
    ok = 0
    for name, raw, want in cases:
        got = name_tags(raw)
        mark = "OK" if got == want else "<-- %s" % got
        ok += 1 if got == want else 0
        print("  %-20s %s" % (name, mark))
    print("표본 %d/%d" % (ok, len(cases)))
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest() if "--selftest" in sys.argv else 0)
