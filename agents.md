# AGENTS.md - gifticon_archive_bot 개발 및 운영 가이드

본 문서는 `gifticon_archive_bot` 프로젝트를 분석, 수정, 확장 또는 유지보수하는 AI 에이전트와 개발자를 위한 기술 명세서 및 가이드라인입니다.

---

## 1. 프로젝트 개요 (Overview)

`gifticon_archive_bot`은 텔레그램 메신저 환경에서 기프티콘(모바일 쿠폰)을 효율적으로 관리하기 위한 자동화 봇입니다. 
가족, 동아리, 팀 등이 사용하는 **사용 방(Active Chat)**에 기프티콘 이미지가 올라오면 이를 백업 및 관리용인 **이력 방(Archive Chat)**으로 자동 복사하고, Tesseract OCR을 통해 교환처, 상품명, 유효기간을 추출하여 SQLite 데이터베이스에 기록·추적합니다.

### 핵심 목표
- **중복 사용 및 분실 방지**: 사용 완료된 기프티콘은 사용 방에서 즉시 삭제하여 미사용 쿠폰만 채팅방에 남도록 유지.
- **이력 복구 지원**: 실수로 삭제되거나 취소된 경우 이력 방의 보관 메시지로부터 언제든 원본 복구 가능.
- **유효기간 추적 및 알림**: 매일 아침 9시(KST) 만료 임박(7일 이내) 기프티콘 자동 공지 및 수시 조회 명령 제공.

---

## 2. 시스템 아키텍처 및 동작 구조

### 2.1 2채팅방(Two-Chat) 분리 구조

```text
[사용 방 (ACTIVE_CHAT_ID)]                          [이력 방 (ARCHIVE_CHAT_ID)]
       │                                                    │
  (1) 기프티콘 업로드 ──────────────────────────────────────>│ (2) 자동 복사 (copy_message)
       │                                                    │
  (3) OCR 및 메타데이터 파싱 ───────────────────────────────>│ (4) SQLite DB 저장
       │                                                    │
  (5) !사용 (답장) ──> 사용 방 원본 메시지 삭제 ──────────────>│ (6) 사용 완료 로그 전송
       │                                                    │
       │<──────────────── !복구 (답장) ──────────────────────┤ (7) 사용 방으로 재전송 & 상태 복원
```

| 채팅방 구분 | 환경 변수 키 | 역할 및 권한 |
|---|---|---|
| **사용 방 (Active Chat)** | `GIFTICON_ACTIVE_CHAT_ID` | 사용자가 실제로 기프티콘을 공유하고 사용하는 공간. 봇에게 **메시지 삭제 권한(관리자)** 필수. |
| **이력 방 (Archive Chat)** | `GIFTICON_ARCHIVE_CHAT_ID` | 모든 기프티콘이 영구 보관되는 백업 공간. 일반 사용자의 오작동을 막기 위해 봇과 관리자만 접근 권한을 갖는 것을 권장. |

### 2.2 메시지 처리 파이프라인 및 핸들러 그룹 (`bot.py`)

`python-telegram-bot`의 핸들러 그룹 순서를 통해 메시지 충돌을 방지합니다:
1. **Group 0 (`on_management_message`)**:
   - `!`로 시작하는 관리 명령어 및 기프티콘 답장 형태의 `사용` 텍스트를 최우선 가로채서 처리.
   - 명령어로 처리된 메시지는 다음 그룹으로 전파되지 않음.
2. **Group 1 (`archive_message`)**:
   - 일반 사진(`photo`), 문서(`document`), GIF(`animation`), 비디오(`video`), 일반 텍스트(`text`)를 감지.
   - `ACTIVE_CHAT_ID`에서 온 경우만 이력 방(`ARCHIVE_CHAT_ID`)으로 `copy_message` 수행.
   - OCR 처리 및 만료일 파싱 후 DB에 `available` 상태로 등록.

### 2.3 OCR 및 텍스트 파싱 파이프라인

1. **이미지 전처리 및 OCR (`ocr_bytes`)**:
   - PIL을 사용하여 `exif_transpose` (회전 보정), 2500px 미만인 경우 2배 확대(upscale).
   - `autocontrast` 및 `SHARPEN` 필터 적용 후 `pytesseract` 호출.
   - `lang="kor+eng"`, Tesseract Page Segmentation Mode `--psm 6`, `--psm 11` 병합 실행하여 텍스트 인식률 극대화.
2. **유효기간 인식 (`expiry_from_text`)**:
   - 정규식을 통해 `YYYY.MM.DD`, `YYYY-MM-DD`, `YYYY년 MM월 DD일`, `YY.MM.DD`, `YYYYMMDD` 패턴 매칭.
   - 여러 날짜가 잡힐 경우 최댓값(`max(found)`)을 유효기간으로 판정.
3. **요약 생성 (`gifticon_summary`)**:
   - `교환처:`, `상품명:` 키워드 라인 추출.
   - 사전 정의된 유명 브랜드 키워드(`스타벅스`, `CU`, `GS25`, `배스킨라빈스`, `투썸플레이스`, `이마트24`, `롯데리아` 등) 매칭.
   - 노이즈 텍스트(바코드 번호, 쿠폰 유의사항 등) 필터링 후 `브랜드 · 상품명 / YYYY-MM-DD` 형식으로 포맷.

### 2.4 스케줄링 (`JobQueue`)

- KST 기준 매일 09:00에 `notify_expiring` 실행.
- 7일 이내 만료 예정인 기프티콘을 조회하여 사용 방에 요약 메시지 전송.
- `expiry_notifications` 테이블을 활용하여 동일 날짜/기프티콘에 대한 중복 발송 방지.

---

## 3. 파일 및 디렉터리 구성

```text
gifticon_archive_bot/
├── bot.py                # 텔레그램 봇 메인 실행부, 이벤트 핸들러, OCR/텍스트 파싱 로직
├── storage.py            # SQLite DB 인터페이스, 스레드 안전성(Lock), 비동기 래핑
├── tests/
│   └── test_storage.py   # storage.py 유닛 테스트
├── Dockerfile            # Python 3.12-slim + Tesseract-OCR(kor) 기반 컨테이너 빌드 파일
├── requirements.txt      # 의존성 패키지 명세
├── .env.example          # 환경 변수 템플릿
├── .gitignore            # DB, .env, 가상환경 제외 설정
├── README.md             # 사용자용 사용 설명서
└── agents.md             # [본 문서] AI 에이전트 및 개발자를 위한 기술 명세서
```

---

## 4. 데이터베이스 명세 (`storage.py`)

기본 저장소는 로컬 SQLite3 DB 파일 (`gifticons.db` 또는 환경변수 지정 경로)입니다.

### 4.1 스키마

#### `gifticons` 테이블
| 컬럼명 | 타입 | 제약 조건 | 설명 |
|---|---|---|---|
| `source_message_id` | INTEGER | PRIMARY KEY | 사용 방의 원본 메시지 ID |
| `archive_message_id` | INTEGER | NOT NULL | 이력 방의 보관 메시지 ID |
| `description` | TEXT | NOT NULL DEFAULT '' | 캡션 또는 OCR 인식 전체 텍스트 |
| `status` | TEXT | CHECK(status IN ('available', 'used')) | 사용 상태 (`available`, `used`) |
| `used_by` | TEXT | NULLABLE | 사용 처리자 (텔레그램 표시 이름/username) |
| `used_at` | TEXT | NULLABLE | 사용 완료 처리 일시 (`datetime('now')`) |
| `created_at` | TEXT | NOT NULL DEFAULT (datetime('now')) | 보관 생성 일시 |
| `expiry_date` | TEXT | NULLABLE | 추출된 유효기간 (`YYYY-MM-DD` 형식) |

#### `expiry_notifications` 테이블
| 컬럼명 | 타입 | 제약 조건 | 설명 |
|---|---|---|---|
| `source_message_id` | INTEGER | NOT NULL | 기프티콘 식별자 |
| `notice_date` | TEXT | NOT NULL | 알림 발송 일자 (`YYYY-MM-DD`) |
| **기본키** | PRIMARY KEY (`source_message_id`, `notice_date`) | 일별 중복 알림 방지 |

### 4.2 스레드 안전성 및 비동기 처리
- SQLite 연결은 `check_same_thread=False`로 열리며, 모든 동기 작업은 `threading.Lock()`을 통과합니다.
- `bot.py` 비동기 루프와의 통합을 위해 `asyncio.to_thread`를 사용하여 동기 DB I/O가 이벤트 루프를 블로킹하지 않도록 설계되었습니다.
- `_init_db()`는 `PRAGMA table_info`를 확인하여 기존 구버전 DB에 `expiry_date` 컬럼이 없으면 자동으로 `ALTER TABLE`을 수행하는 하위 호환 마이그레이션 로직을 갖추고 있습니다.

---

## 5. 명령어 및 상호작용 상세

| 명령어 | 지원 채팅방 | 동작 방식 및 요구사항 |
|---|---|---|
| `/start`, `/help` | 공통 | 사용 설명 안내 메시지 출력 |
| `/chatid` | 공통 | 현재 채팅방의 ID 출력 (초기 설정 시 활용) |
| `!사용` | 사용 방 | **반드시 기프티콘 메시지에 답장** 형태로 입력. 또는 답장 메시지에 `사용` 단어가 포함되어도 동작. 원본 삭제 후 이력 방에 기록. |
| `!복구` | 이력 방 | **반드시 이력 방 보관 메시지에 답장** 형태로 입력. 사용 방으로 메시지를 복사(`copy_message`)하고 DB 상태를 `available`로 갱신. |
| `!목록` | 공통 | 전체 기프티콘(사용/미사용) 목록 조회 (최대 50건) |
| `!미사용` | 공통 | 미사용(`available`) 상태인 기프티콘만 조회 |
| `!검색 <키워드>` | 공통 | `description` 필드 LIKE 검색 |
| `!임박 [일수]` | 공통 | 지정 일수(기본값 30일) 이내 만료 예정인 미사용 기프티콘 조회 |
| `!만료` | 공통 | 이미 유효기간이 지난 미사용 기프티콘 조회 |
| `!삭제`, `!삭제목록` | 사용 방 | 사용 완료(`used`)된 기프티콘 선택 삭제 인라인 버튼 UI 호출 (이력 방 답장 시 강제 삭제) |
| `!강제삭제` | 이력 방 | 이력 방 보관 메시지에 답장해 DB 및 양쪽 방의 텔레그램 메시지 강제 삭제 |
| `!초기화 확인` | 이력 방 | 개발/테스트용. DB의 모든 레코드 삭제 (`clear()`). 이력 방의 텔레그램 메시지는 보존됨. |

> [!NOTE]
> 모바일 사용성을 위해 한 줄 크기의 슬림 고정 키보드(`MAIN_KEYBOARD`: `[!미사용]`, `[!임박]`, `[!삭제]`) 및 텔레그램 공식 메뉴(`set_my_commands`)가 상시 제공됩니다.

---

## 6. 환경 변수 및 설정 (`.env`)

| 변수명 | 필수 여부 | 기본값 | 설명 |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **필수** | - | @BotFather에서 발급받은 봇 토큰 |
| `GIFTICON_ACTIVE_CHAT_ID` | **필수** | - | 기프티콘을 올리고 사용하는 사용 방의 텔레그램 Chat ID (숫자) |
| `GIFTICON_ARCHIVE_CHAT_ID` | **필수** | - | 기프티콘이 영구 보관되는 이력 방의 텔레그램 Chat ID (숫자) |
| `GIFTICON_DB_PATH` | 선택 | `gifticons.db` | SQLite 데이터베이스 파일 경로 (Docker 환경 기본값: `/data/gifticons.db`) |

> [!IMPORTANT]
> - 봇 생성 후 BotFather의 `/setprivacy` 설정을 반드시 **Disable**로 변경해야 방에 올라오는 사진과 일반 메시지를 정상 수신할 수 있습니다.
> - 사용 방에서 기프티콘 사용 시 원본 메시지를 삭제해야 하므로, 봇에게 **메시지 삭제 권한(Delete Messages)**이 반드시 부여되어 있어야 합니다.

---

## 7. 개발, 테스트 및 유지보수 규칙 (Agent Rules)

AI 에이전트가 본 프로젝트의 코드를 수정하거나 기능을 추가할 때 준수해야 할 원칙입니다.

### 7.1 코드 수정 가이드라인
1. **`storage.py` 수정 시**:
   - DB 메서드는 반드시 `_method_sync` (동기 메서드 + `self._lock` 보유)와 `async def method` (`asyncio.to_thread` 호출) 쌍으로 구현해야 합니다.
   - DB 스키마를 변경할 때는 기존 DB 파일과의 하위 호환성을 위해 `_init_db()` 내에 `ALTER TABLE` 또는 마이그레이션 검사 로직을 작성해야 합니다.
2. **`bot.py` 핸들러 수정 시**:
   - `on_management_message`와 `archive_message`의 `group` 분리를 훼손하지 마십시오 (`group=0` -> 관리 명령, `group=1` -> 아카이빙).
   - 텔레그램 API 호출(`copy_message`, `delete_message`)은 사용자의 권한 부족, 메시지 기삭제 등으로 실패할 수 있으므로 항상 `try-except TelegramError` 또는 `BadRequest` 처리를 수반해야 합니다.
   - 시간 관련 계산(만료일, 일일 알림) 시 항상 `ZoneInfo("Asia/Seoul")`을 명시하여 시스템 로컬 타임존 차이로 인한 오동작을 방지해야 합니다.
3. **OCR 및 텍스트 파싱 수정 시**:
   - `gifticon_summary`에 새로운 브랜드나 정규식을 추가할 때는 기존 포맷(`브랜드 · 상품명 / YYYY-MM-DD`)과의 일관성을 유지해야 합니다.

### 7.2 검증 및 테스트 실행
코드 변경 후 아래 명령을 통해 구문 검사 및 단위 테스트를 반드시 실행하십시오.

```powershell
# 1. 파이썬 문법 및 바이트코드 컴파일 검증
python -m py_compile bot.py storage.py

# 2. 단위 테스트 실행
python -m unittest discover -s tests
```

신규 비즈니스 로직이나 DB 함수를 추가한 경우, `tests/test_storage.py` 또는 새로운 테스트 파일을 작성하여 검증해야 합니다.

### 7.3 보안 주의사항
- 실제 토큰(`TELEGRAM_BOT_TOKEN`), 채팅 ID, 개인 기프티콘 정보가 포함된 `.db` 파일 및 `.env` 파일은 절대 Git 커밋에 포함시키지 마십시오.
