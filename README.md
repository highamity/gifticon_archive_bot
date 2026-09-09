# 기프티콘 보관 봇

텔레그램 사용 방에 올라온 기프티콘을 이력 방에 자동 보관하고, 사용 여부와 유효기간을 관리하는 봇입니다.

## 주요 기능

- 사진, 이미지 파일, GIF, 동영상, 일반 텍스트 메시지를 이력 방에 자동 보관
- 캡션이 없는 이미지에서 Tesseract OCR로 한글·영문 텍스트와 유효기간 인식
- 목록과 검색 결과를 `브랜드 · 상품명 / YYYY-MM-DD` 형식으로 요약
- 사용 완료 처리 시 사용 방의 원본 메시지 삭제
- 이력 방의 보관 메시지에 답장해 원본 기프티콘 복구
- 유효기간 임박 조회와 만료 조회
- 매일 오전 9시(KST) 유효기간 임박 알림
- 사용 완료 기프티콘 선택 삭제, 강제 삭제, 개발용 전체 초기화
- 기존 SQLite DB에 `expiry_date`와 알림 기록 테이블을 자동으로 추가하는 마이그레이션

## 동작 구조

```text
사용 방의 기프티콘
        │
        ├─ OCR/캡션 분석 및 DB 저장
        └─ 이력 방으로 복사

사용 방: !사용, !목록, !검색, !임박, !만료, !삭제목록
이력 방: !복구, !강제삭제, !초기화 확인
```

## 명령어

| 명령어 | 설명 |
| --- | --- |
| `/start` | 도움말 표시 |
| `/chatid` | 현재 채팅방 ID 확인 |
| `!사용` | 사용 방에서 원본 기프티콘에 답장해 사용 완료 처리 |
| `!복구` | 이력 방의 보관 메시지에 답장해 사용 방으로 복구 |
| `!목록` | 전체 기프티콘 목록 조회 |
| `!미사용` | 사용 가능한 기프티콘만 조회 |
| `!검색 <단어>` | OCR/캡션 내용 검색 |
| `!임박 [일수]` | 기본 30일 이내 만료 예정 기프티콘 조회 |
| `!만료` | 만료된 기프티콘 조회 |
| `!삭제목록` | 사용 완료 기프티콘 선택 삭제 |
| `!강제삭제` | 이력 방 보관 메시지에 답장해 DB와 관련 메시지 삭제 |
| `!초기화 확인` | 이력 방의 DB 목록 전체 초기화(개발용) |

`!사용`은 사용 방의 원본 메시지에 답장해서 입력할 수 있으며, 메시지에 `사용`이 포함된 답장도 인식합니다. 사용 처리 전 확인 버튼이 표시됩니다.

## 최초 설정

1. BotFather에서 봇을 생성하고 토큰을 받습니다.
2. 사용 방과 이력 방에 봇을 초대합니다.
3. 두 방에서 봇을 관리자로 지정하고 메시지 삭제 권한을 부여합니다.
4. BotFather의 `/setprivacy`에서 Privacy Mode를 **Disable**로 설정합니다.
5. 각 방에서 `/chatid`를 입력해 채팅방 ID를 확인합니다.
6. `.env.example`을 `.env`로 복사하고 실제 값을 입력합니다.

```env
TELEGRAM_BOT_TOKEN=실제_봇_토큰
GIFTICON_ACTIVE_CHAT_ID=-1001234567890
GIFTICON_ARCHIVE_CHAT_ID=-1009876543210
GIFTICON_DB_PATH=gifticons.db
```

이력 방에는 봇과 관리자만 접근할 수 있도록 설정하는 것을 권장합니다. 봇은 사용 방의 원본 메시지를 삭제할 수 있어야 합니다.

## 로컬 실행 (Windows)

Tesseract OCR과 한국어 언어 데이터가 설치되어 있어야 합니다.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
# .env에 실제 값을 입력
python bot.py
```

## 검증

```powershell
python -m py_compile bot.py storage.py
python -m unittest discover -s tests
```

## QNAP Docker 실행

SQLite DB를 NAS의 `/share/Container/gifticon_bot_data`에 저장하는 예시입니다.

```bash
cd gifticon_archive_bot
docker build -t gifticon-archive-bot .
docker run -d --name gifticon-archive-bot \
  --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN="YOUR_TOKEN_HERE" \
  -e GIFTICON_ACTIVE_CHAT_ID="-1001234567890" \
  -e GIFTICON_ARCHIVE_CHAT_ID="-1009876543210" \
  -v /share/Container/gifticon_bot_data:/data \
  gifticon-archive-bot
```

Docker 이미지에는 `tesseract-ocr`와 `tesseract-ocr-kor`가 포함됩니다.

## 보안 및 제한

- `.env`와 `gifticons.db`를 Git에 커밋하지 마세요.
- 토큰은 README나 소스에 직접 기록하지 마세요.
- OCR은 이미지 품질에 따라 결과가 달라질 수 있으며, 유효기간을 인식하지 못하면 `유효기간 미인식`으로 표시됩니다.
- 동일한 기프티콘 이미지를 여러 번 올리면 각각 별도의 기록으로 보관됩니다.
- `!초기화 확인`은 DB의 목록만 삭제하고 이력 방의 Telegram 메시지는 삭제하지 않습니다.
