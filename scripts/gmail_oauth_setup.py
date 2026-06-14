#!/usr/bin/env python3
"""[로컬 1회 실행] Gmail OAuth 동의 → refresh token 발급. (대표 직접 — Claude는 동의 대행 불가)

사전 준비 (Google Cloud Console, console.cloud.google.com):
  1) 새 프로젝트 생성
  2) 'API 및 서비스 > 라이브러리'에서 'Gmail API' 사용 설정
  3) 'OAuth 동의 화면': User Type=External, 앱 이름 입력, 테스트 사용자에 tmifmdj@gmail.com 추가,
     scope에 .../auth/gmail.readonly 추가
  4) '사용자 인증 정보 > 사용자 인증 정보 만들기 > OAuth 클라이언트 ID > 애플리케이션 유형=데스크톱 앱'
     → 생성 후 JSON 다운로드 (client_secret_xxx.json)
  5) pip install google-auth-oauthlib

실행:
  python scripts/gmail_oauth_setup.py <client_secret_xxx.json 경로>
  → 브라우저로 동의 → 콘솔에 CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN 출력

등록: 출력된 3개를 weekly-vibe GitHub repo의 Secrets로:
  GMAIL_CLIENT_ID · GMAIL_CLIENT_SECRET · GMAIL_REFRESH_TOKEN
"""
import sys

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: python scripts/gmail_oauth_setup.py <client_secret.json 경로>")
        return 1
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("먼저: pip install google-auth-oauthlib")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")  # prompt=consent → refresh_token 보장

    print("\n=== GitHub Secrets 에 등록 (weekly-vibe repo) ===")
    print("GMAIL_CLIENT_ID     =", creds.client_id)
    print("GMAIL_CLIENT_SECRET =", creds.client_secret)
    print("GMAIL_REFRESH_TOKEN =", creds.refresh_token)
    if not creds.refresh_token:
        print("\n⚠️ refresh_token이 비어 있으면: Google 계정 > 보안 > 서드파티 액세스에서 "
              "이 앱을 제거하고 재실행하세요(첫 동의에서만 발급).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
