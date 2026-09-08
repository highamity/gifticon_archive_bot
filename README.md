# 텔레그램 기프티콘 보관 봇

두 사람이 쓰는 **사용 방**에 기프티콘을 한 번만 올리면, 봇이 **이력 방**에 원본을 자동 복사해 보관합니다. 사용 완료를 확인하면 사용 방 원본만 삭제하고 이력은 남습니다. 실수로 삭제해도 이력 방에서 복구할 수 있습니다.

## 동작

```text
사용 방에 기프티콘 업로드 → 이력 방으로 자동 복사 → !사용 확인 → 원본 삭제 + 이력 유지
                                                       └→ 이력 방에서 !복구 가능
```

- 보관 대상: 사진, 문서, GIF, 동영상, 일반 텍스트 메시지
- 사용 처리: 사용 방에서 기프티콘 메시지에 답장해 `!사용` 입력 후 버튼으로 확인
- 복구: 이력 방의 보관 메시지에 답장해 `!복구` 입력
- 조회: `!미사용`, `!목록`, `!검색 <단어>`

`!검색`은 기프티콘 사진의 캡션 또는 텍스트를 대상으로 검색합니다. 따라서 업로드할 때 `스타벅스 아메리카노 2026-12-31`처럼 간단한 캡션을 넣는 것을 권장합니다.

## 최초 설정

1. BotFather에서 봇을 만들고 토큰을 받습니다.
2. 사용 방과 이력 방에 봇을 초대합니다.
3. **사용 방에서 봇을 관리자로 지정하고 메시지 삭제 권한을 부여**합니다. 이 권한이 없으면 사용 처리 기록은 남지만 원본 삭제는 실패합니다.
4. BotFather의 `/setprivacy`에서 봇의 Privacy Mode를 **Disable**로 설정합니다. 그래야 일반 사진과 메시지를 자동 보관할 수 있습니다.
5. 두 방에서 각각 `/chatid`를 입력해 숫자 채팅 ID를 확인합니다.
6. `.env.example`을 `.env`로 복사하고 토큰과 두 ID를 설정합니다.

`.env` 예시:

```env
TELEGRAM_BOT_TOKEN=실제_토큰
GIFTICON_ACTIVE_CHAT_ID=-1001234567890
GIFTICON_ARCHIVE_CHAT_ID=-1009876543210
```

이력 방은 실수로 삭제되지 않도록 두 사람만 두고, 봇에는 일반 메시지 권한만 주는 것을 권장합니다.

## 로컬 실행 (Windows)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
# .env를 편집해 실제 값 입력
python bot.py
```

## 코드 검증

```powershell
python -m py_compile bot.py storage.py
python -m unittest discover -s tests
```

## QNAP Docker 실행

`/share/Container/gifticon_bot_data`는 NAS에서 SQLite DB를 보존할 디렉터리입니다.

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

## 보안 및 제한

- `.env`와 `gifticons.db`는 커밋하거나 공유하지 마세요.
- 봇은 등록된 사용 방의 메시지만 자동 보관하므로 이력 방 메시지를 다시 복사하는 루프가 생기지 않습니다.
- 사진 자체의 중복 여부는 판별하지 않습니다. 같은 기프티콘을 여러 번 올리면 각각 별도 항목으로 보관됩니다.
