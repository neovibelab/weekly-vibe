"""
generate_preview.py
엔터문화연구소 글로벌 주간 브리핑 — SNS 미리보기 이미지 생성 스크립트

사용법:
  python preview/generate_preview.py MMDD "issue-title 텍스트"

예시:
  python preview/generate_preview.py 0516 "Disney, Sora로 AI 제작 내재화"

출력:
  preview/PREVIEW_MMDD.png (1200x630px)
"""

import sys
import os
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow가 설치되지 않았습니다. pip install Pillow 실행 후 재시도하세요.")
    sys.exit(1)


# 색상 상수
COLOR_BG = (10, 10, 10)          # #0a0a0a
COLOR_LIME = (200, 255, 0)        # #C8FF00
COLOR_WHITE = (255, 255, 255)
COLOR_GRAY = (102, 102, 102)      # #666666
COLOR_DARK_GRAY = (68, 68, 68)    # #444444


def get_font(size, bold=False):
    """시스템 기본 폰트 로드. 한글 지원 폰트 우선 탐색."""
    # Windows 한글 폰트 우선순위
    font_candidates = [
        # Noto Sans KR (Google Fonts — 미리 설치된 경우)
        "C:/Windows/Fonts/NotoSansKR-Bold.ttf",
        "C:/Windows/Fonts/NotoSansKR-Regular.ttf",
        # 맑은 고딕 (Windows 기본 한글 폰트)
        "C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        # 굴림
        "C:/Windows/Fonts/gulim.ttc",
        # 영문 폴백
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]

    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue

    # 최후 폴백: PIL 기본 비트맵 폰트
    return ImageFont.load_default()


def wrap_text(text, font, draw, max_width):
    """텍스트를 max_width 내에서 줄바꿈."""
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def generate_preview(mmdd: str, issue_title: str, output_dir: str = None):
    """미리보기 이미지 생성."""
    WIDTH, HEIGHT = 1200, 630

    img = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # 날짜 파싱 (MMDD -> 2026.MM.DD)
    mm = mmdd[:2]
    dd = mmdd[2:]
    date_str = f"2026.{mm}.{dd}"

    # 폰트 로드
    font_small = get_font(18)
    font_label = get_font(22, bold=True)
    font_title_large = get_font(72, bold=True)
    font_title_medium = get_font(56, bold=True)
    font_sub = get_font(20)

    # --- 상단 영역: 브랜드명 ---
    brand_text = "엔터문화연구소  글로벌 주간 브리핑"
    draw.text((64, 52), brand_text, font=font_label, fill=COLOR_LIME)

    # --- 상단 구분선 ---
    draw.line([(64, 92), (1136, 92)], fill=COLOR_DARK_GRAY, width=1)

    # --- 중앙 영역: issue-title ---
    title_font = font_title_large
    max_title_width = WIDTH - 128  # 양쪽 64px 패딩

    # 제목 줄바꿈 처리
    lines = wrap_text(issue_title, title_font, draw, max_title_width)

    # 제목이 2줄 이상이면 폰트 크기 축소
    if len(lines) > 2:
        title_font = font_title_medium
        lines = wrap_text(issue_title, title_font, draw, max_title_width)

    # 제목 세로 중앙 정렬
    line_height = title_font.size + 8
    total_title_height = len(lines) * line_height
    title_start_y = (HEIGHT - total_title_height) // 2 - 20

    for i, line in enumerate(lines):
        draw.text((64, title_start_y + i * line_height), line, font=title_font, fill=COLOR_WHITE)

    # --- 하단 구분선 ---
    footer_y = HEIGHT - 100
    draw.line([(64, footer_y), (1136, footer_y)], fill=COLOR_DARK_GRAY, width=1)

    # --- 하단 영역: 날짜 + 지역 ---
    sub_text = f"{date_str}  ·  US  ·  China  ·  Japan"
    draw.text((64, footer_y + 20), sub_text, font=font_sub, fill=COLOR_GRAY)

    # --- 우하단: 라임 액센트 바 ---
    bar_x = WIDTH - 64 - 80
    bar_y = footer_y + 28
    draw.rectangle([(bar_x, bar_y), (bar_x + 80, bar_y + 6)], fill=COLOR_LIME)

    # --- 저장 ---
    if output_dir is None:
        output_dir = Path(__file__).parent

    output_path = Path(output_dir) / f"PREVIEW_{mmdd}.png"
    img.save(str(output_path), "PNG", optimize=True)
    print(f"미리보기 이미지 생성 완료: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python generate_preview.py MMDD \"issue-title\"")
        print("예시:  python generate_preview.py 0516 \"Disney, Sora로 AI 제작 내재화\"")
        sys.exit(1)

    mmdd = sys.argv[1]
    title = sys.argv[2]
    output_dir = Path(__file__).parent  # preview/ 폴더

    generate_preview(mmdd, title, str(output_dir))
