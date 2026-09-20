"""Archive gifticons automatically and manage their use in one Telegram chat."""

from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from PIL import Image, ImageFilter, ImageOps
import pytesseract
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
try:
    from telegram import LinkPreviewOptions
except ImportError:
    LinkPreviewOptions = None
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from storage import Gifticon, GifticonStore

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
logger = logging.getLogger("gifticon_archive_bot")

OCR_SEMAPHORE = asyncio.Semaphore(1)
OCR_MAX_FILE_BYTES = 10 * 1024 * 1024
OCR_MAX_PIXELS = 20_000_000
OCR_MAX_DIMENSION = 2000
OCR_TIMEOUT_SECONDS = 15
NOTIFICATION_JOB_NAME = "daily-expiry-notice"
# Telegram albums arrive as several updates.  Keep the complete archive
# transaction (copy -> download/OCR -> DB -> acknowledgement) serialized so
# concurrent album updates cannot interfere with one another.
ARCHIVE_SEMAPHORE = asyncio.Semaphore(1)

DEFAULT_DB = Path(__file__).resolve().parent / "gifticons.db"
STORE = GifticonStore(Path(os.environ.get("GIFTICON_DB_PATH", str(DEFAULT_DB))))
CUSTOM_BRANDS = set(STORE.list_brands_sync())
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("!미사용"), KeyboardButton("!임박"), KeyboardButton("!삭제")]],
    resize_keyboard=True,
    is_persistent=True,
)


def configured_chat_id(name: str) -> int:
    value = os.environ.get(name, "").strip()
    try:
        return int(value)
    except ValueError:
        logger.error("Set %s to a numeric Telegram chat ID.", name)
        sys.exit(1)


ACTIVE_CHAT_ID = configured_chat_id("GIFTICON_ACTIVE_CHAT_ID")
ARCHIVE_CHAT_ID = configured_chat_id("GIFTICON_ARCHIVE_CHAT_ID")
try:
    KST = ZoneInfo("Asia/Seoul")
except Exception:
    KST = timezone(timedelta(hours=9), name="Asia/Seoul")


def description_for(message) -> str:
    """Use the caption/text for human-readable lists and searches."""
    return (message.caption or message.text or "").strip()[:500]


def ocr_bytes(image_bytes: bytes) -> str:
    with Image.open(io.BytesIO(image_bytes)) as source:
        if source.width * source.height > OCR_MAX_PIXELS:
            raise ValueError("image is too large for OCR")
        image = ImageOps.exif_transpose(source).convert("RGB")

    # Do not upscale small images. Downscaling large images keeps OCR bounded.
    image.thumbnail((OCR_MAX_DIMENSION, OCR_MAX_DIMENSION), Image.Resampling.LANCZOS)
    image = ImageOps.autocontrast(image).filter(ImageFilter.SHARPEN)
    result = pytesseract.image_to_string(
        image,
        lang="kor+eng",
        config="--psm 6",
        timeout=OCR_TIMEOUT_SECONDS,
    )
    lines = [line.strip() for line in result.splitlines() if line.strip()]
    return "\n".join(dict.fromkeys(lines))[:1500]


def expiry_from_text(text: str) -> date | None:
    normalized = re.sub(r"\s+", "", text)
    patterns = [
        r"(?P<y>20\d{2})[.\-/년](?P<m>\d{1,2})[.\-/월](?P<d>\d{1,2})일?",
        r"(?P<y>20\d{2})(?P<m>\d{2})(?P<d>\d{2})",
        r"(?P<y>\d{2})[.\-/](?P<m>\d{1,2})[.\-/](?P<d>\d{1,2})",
    ]
    found: list[date] = []
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            try:
                year = int(match.group("y"))
                if year < 100:
                    year += 2000
                found.append(date(year, int(match.group("m")), int(match.group("d"))))
            except ValueError:
                continue
    if found:
        return max(found)
    return None


def parse_manual_expiry(value: str) -> date | None:
    """Parse an explicit date, defaulting a missing year to the current KST year."""
    normalized = value.strip().replace(".", "-").replace("/", "-")
    full_date = re.fullmatch(r"(20\d{2})-(\d{1,2})-(\d{1,2})", normalized)
    short_date = re.fullmatch(r"(\d{1,2})-(\d{1,2})", normalized)
    if full_date:
        year, month, day = map(int, full_date.groups())
    elif short_date:
        month, day = map(int, short_date.groups())
        year = datetime.now(KST).year
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def gifticon_expiry(gifticon: Gifticon) -> date | None:
    """Return the saved expiry date, falling back to legacy OCR-only records."""
    return (
        date.fromisoformat(gifticon.expiry_date)
        if gifticon.expiry_date
        else expiry_from_text(gifticon.description)
    )


def kst_today() -> date:
    return datetime.now(KST).date()


def gifticon_label(gifticon: Gifticon) -> str:
    return gifticon_summary(
        gifticon.description, gifticon_expiry(gifticon), gifticon.title
    )


def telegram_message_link(chat_id: int, message_id: int) -> str | None:
    """Build a Telegram message link for a private supergroup/channel."""
    chat_id_text = str(chat_id)
    if not chat_id_text.startswith("-100"):
        return None
    return f"https://t.me/c/{chat_id_text[4:]}/{message_id}"


def no_preview_kwargs() -> dict:
    """Disable URL link previews in Telegram messages."""
    if LinkPreviewOptions is not None:
        return {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
    return {"disable_web_page_preview": True}


def gifticon_url(gifticon: Gifticon) -> str | None:
    target_chat_id = ACTIVE_CHAT_ID if gifticon.status == "available" else ARCHIVE_CHAT_ID
    target_message_id = (
        gifticon.source_message_id
        if gifticon.status == "available"
        else gifticon.archive_message_id
    )
    return telegram_message_link(target_chat_id, target_message_id)


def gifticon_open_link(gifticon: Gifticon) -> str:
    url = gifticon_url(gifticon)
    return f' <a href="{url}">열기</a>' if url else ""


def gifticon_open_button(gifticon: Gifticon, index: int = 1) -> InlineKeyboardButton | None:
    url = gifticon_url(gifticon)
    if not url:
        return None
    summary = gifticon_label(gifticon)
    name = summary.split(" / ", 1)[0].strip() or "기프티콘"
    if len(name) > 22:
        name = f"{name[:21]}…"
    return InlineKeyboardButton(f"열기 · {index}. {name}", url=url)


def gifticon_summary(text: str, expiry: date | None = None, title: str | None = None) -> str:
    """Return a compact merchant/product summary instead of the full OCR text."""
    if title:
        expiry_text = expiry.isoformat() if expiry else "유효기간 미인식"
        return f"{title.strip()[:120]} / {expiry_text}"

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    if not lines:
        lines = [re.sub(r"\s+", " ", text).strip()]

    merchant = ""
    product = ""
    for line in lines:
        merchant_match = re.search(r"(?:교환처|브랜드|매장)\s*[:：]?\s*(.+)", line)
        if merchant_match:
            merchant = merchant_match.group(1).strip()
        product_match = re.search(r"상품명\s*[:：]?\s*(.+)", line)
        if product_match:
            product = product_match.group(1).strip()

    noise = re.compile(
        r"^(보유 쿠폰 상세|쿠폰함|쿠폰|쿠폰상세|바코드|사용 가능|혜택 내용|이용 방법|유의 사항)$"
    )
    candidates = []
    for line in lines:
        compact = re.sub(r"\d[\d\s-]{7,}", "", line)
        compact = re.sub(r"유효기간\s*[:：]?", "", compact)
        compact = re.sub(r"20\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}일?", "", compact)
        compact = re.sub(r"\d{2}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}", "", compact)
        compact = compact.strip(" :：~-→")
        if compact and not noise.match(compact) and len(compact) >= 2:
            candidates.append(compact)
    known_brands = (
        "롯데리아", "롯데마트", "롯데마트 & 슈퍼", "CU", "GS25", "세븐일레븐",
        "메가MGC커피", "메가커피", "투썸플레이스", "스타벅스", "이마트24",
        "파리바게뜨", "배스킨라빈스", "버거킹", "맥도날드",
    )
    known_brands = tuple(sorted((*CUSTOM_BRANDS, *known_brands), key=len, reverse=True))
    for candidate in candidates:
        for brand in known_brands:
            if brand in candidate:
                merchant = brand
                if candidate != brand:
                    product = candidate
                break
        if merchant:
            break
    if not merchant and candidates:
        merchant = candidates[0]
    if not product:
        for candidate in candidates[1:]:
            if (
                candidate != merchant
                and not re.fullmatch(r"\[[^]]+\]", candidate)
                and not re.search(r"유효기간|쿠폰번호|주문번호|사용 방법|까지", candidate)
            ):
                product = candidate
                break
    parts = [part for part in (merchant, product) if part]
    if not parts:
        parts = [re.sub(r"\s+", " ", text).strip()[:80] or "(인식된 텍스트 없음)"]
    expiry_text = expiry.isoformat() if expiry else "유효기간 미인식"
    return f"{' · '.join(dict.fromkeys(parts))} / {expiry_text}"


async def description_for_message(message, bot) -> str:
    """Use caption/text, or OCR the image when no caption exists."""
    description = description_for(message)
    if description:
        return description

    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id = message.document.file_id
    if not file_id:
        return "(캡션 없음)"

    try:
        telegram_file = await bot.get_file(file_id)
        image_bytes = bytes(await telegram_file.download_as_bytearray())
        if len(image_bytes) > OCR_MAX_FILE_BYTES:
            return "(이미지가 너무 커서 OCR을 생략했습니다)"
        async with OCR_SEMAPHORE:
            recognized = await asyncio.to_thread(ocr_bytes, image_bytes)
        return recognized or "(이미지 글자 인식 실패)"
    except Exception:
        logger.exception("Could not OCR message %s", message.message_id)
        return "(이미지 글자 인식 실패)"


def is_management_command(message) -> bool:
    raw = (message.text or "").strip()
    return (
        raw.startswith("!")
        or is_expiry_update_request(message)
        or is_title_update_request(message)
        or is_compact_metadata_update_request(message)
    )


def is_expiry_update_request(message) -> bool:
    raw = (message.text or "").strip()
    return message.reply_to_message is not None and raw.split(maxsplit=1)[0] == "유효기간"


def is_title_update_request(message) -> bool:
    raw = (message.text or "").strip()
    return message.reply_to_message is not None and raw.split(maxsplit=1)[0] == "제목"


def is_compact_metadata_update_request(message) -> bool:
    raw = (message.text or "").strip()
    if message.reply_to_message is None or not raw:
        return False
    if raw.startswith("!") or raw.startswith("/") or raw.split(maxsplit=1)[0] in {"제목", "유효기간"}:
        return False
    if is_use_request(message):
        return False
    return True


async def manage_brand(msg, chat_id: int, command: str, argument: str) -> None:
    if chat_id != ARCHIVE_CHAT_ID:
        await msg.reply_text("브랜드 관리는 이력 방에서만 실행할 수 있습니다.")
        return
    name = argument.strip()
    if command == "!브랜드목록":
        brands = await STORE.list_brands()
        if not brands:
            await msg.reply_text("추가한 브랜드가 없습니다.")
            return
        await msg.reply_text("추가한 브랜드:\n" + "\n".join(f"- {brand}" for brand in brands))
        return
    if not name or len(name) > 80 or "\n" in name or "\r" in name:
        await msg.reply_text("사용법: !브랜드추가 <브랜드명> 또는 !브랜드삭제 <브랜드명>")
        return
    if command == "!브랜드추가":
        if not await STORE.add_brand(name):
            await msg.reply_text(f"이미 등록된 브랜드입니다: {name}")
            return
        CUSTOM_BRANDS.add(name)
        await msg.reply_text(f"브랜드를 추가했습니다: {name}")
    elif command == "!브랜드삭제":
        if not await STORE.remove_brand(name):
            await msg.reply_text(f"등록된 브랜드가 아닙니다: {name}")
            return
        CUSTOM_BRANDS.discard(name)
        await msg.reply_text(f"브랜드를 삭제했습니다: {name}")


def is_use_request(message) -> bool:
    raw = (message.text or "").strip()
    if message.reply_to_message is None or not raw:
        return False
    if raw.startswith("!"):
        return raw == "!사용"
    if "미사용" in raw:
        return False
    return raw in {"사용", "사용완료"} or raw.startswith("사용 ") or raw.startswith("사용\n")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            "기프티콘 보관 봇입니다. 사용 방에 올린 사진·파일·텍스트를 이력 방에 자동 보관합니다.\n\n"
            "사용할 기프티콘에 답장: !사용 또는 '사용'이 포함된 답장\n"
            "이력 방의 보관본에 답장: !복구\n"
            "!미사용 / !목록 / !검색 <단어> / !사용완료\n"
            "!임박 [일수] / !만료 / !삭제 / !사용완료삭제 확인 / !초기화 확인\n\n"
            "이력 방에서 !브랜드추가 <이름> / !브랜드목록 / !브랜드삭제 <이름>으로 자동 요약 브랜드를 관리합니다.\n\n"
            "목록·검색·임박 알림의 '열기' 링크를 누르면 원본 메시지로 이동합니다.\n"
            "기본 목록과 검색에는 만료된 기프티콘이 표시되지 않으며, !만료에서 확인할 수 있습니다.\n\n"
            "기프티콘에 답장해 유효기간 YYYY-MM-DD 또는 M/D 입력: 유효기간 수정\n\n"
            "기프티콘에 답장해 제목 새 제목 입력: 목록 제목 수정\n\n"
            "관리용 명령은 사용 방 또는 이력 방에서만 동작합니다.",
            reply_markup=MAIN_KEYBOARD,
        )


async def cmd_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if msg and chat:
        await msg.reply_text(f"이 채팅 ID: {chat.id}")


async def archive_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if msg is None or chat is None or chat.id != ACTIVE_CHAT_ID or is_management_command(msg):
        return
    # Replies are reserved for actions on an existing gifticon, not new archives.
    if msg.reply_to_message is not None:
        return
    if not (msg.photo or msg.document or msg.animation or msg.video or msg.text):
        return
    async with ARCHIVE_SEMAPHORE:
        try:
            copied = await context.bot.copy_message(
                chat_id=ARCHIVE_CHAT_ID,
                from_chat_id=ACTIVE_CHAT_ID,
                message_id=msg.message_id,
            )
            description = await description_for_message(msg, context.bot)
            expiry = expiry_from_text(description)
            await STORE.add(
                msg.message_id,
                copied.message_id,
                description,
                expiry.isoformat() if expiry else None,
            )
        except TelegramError:
            logger.exception("Could not archive message %s", msg.message_id)
            try:
                await msg.reply_text("보관에 실패했습니다. 봇의 이력 방 접근 권한을 확인해 주세요.")
            except TelegramError:
                logger.exception("Could not send archive failure notice for %s", msg.message_id)
            return

        # A rate-limit or transient failure while sending the acknowledgement
        # must not make a successfully copied/OCR'd album item look failed.
        recognized_preview = gifticon_summary(description, expiry)
        try:
            await msg.reply_text(
                "이력 방에 보관했습니다.\n\n"
                f"인식 내용: {recognized_preview}",
                reply_markup=MAIN_KEYBOARD,
            )
        except TelegramError:
            logger.exception("Could not acknowledge archived message %s", msg.message_id)


async def on_management_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if msg is None or chat is None or chat.id not in {ACTIVE_CHAT_ID, ARCHIVE_CHAT_ID}:
        return
    raw = (msg.text or "").strip()
    if chat.id == ACTIVE_CHAT_ID and is_use_request(msg):
        await mark_as_used(msg, context)
        return
    if not raw.startswith("!") and not (
        is_expiry_update_request(msg)
        or is_title_update_request(msg)
        or is_compact_metadata_update_request(msg)
    ):
        return
    if is_compact_metadata_update_request(msg):
        await update_compact_metadata(msg, chat.id, raw)
        return
    command, _, argument = raw.partition(" ")
    if not command.startswith("!"):
        command = f"!{command}"
    argument = argument.strip()
    if command in {"!브랜드추가", "!브랜드삭제", "!브랜드목록"}:
        await manage_brand(msg, chat.id, command, argument)
    elif command == "!사용":
        if chat.id == ACTIVE_CHAT_ID:
            await mark_as_used(msg, context)
        else:
            await msg.reply_text("!사용은 사용방의 원본 기프티콘에 답장해서 입력하세요.")
    elif command == "!복구":
        await restore_gifticon(msg, chat.id, context)
    elif command in {"!목록", "!미사용"}:
        await show_list(msg, "available")
    elif command == "!사용완료":
        await show_list(msg, "used")
    elif command == "!사용완료삭제":
        await delete_used_all(msg, chat.id, argument, context)
    elif command == "!알림설정":
        await update_notification_settings(msg, chat.id, argument, context)
    elif command == "!알림테스트":
        await test_expiry_notification(msg, chat.id, argument, context)
    elif command == "!검색":
        if not argument:
            await msg.reply_text("사용법: !검색 <단어>")
        else:
            await show_list(msg, "available", argument)
    elif command == "!유효기간":
        await update_expiry(msg, chat.id, argument)
    elif command == "!제목":
        await update_title(msg, chat.id, argument)
    elif command == "!임박":
        try:
            days = int(argument) if argument else 30
            if days < 0 or days > 3650:
                raise ValueError
        except ValueError:
            await msg.reply_text("사용법: !임박 [남은 일수] (기본값 30)")
        else:
            await show_expiring(msg, days, include_expired=False)
    elif command == "!만료":
        await show_expiring(msg, 0, include_expired=True)
    elif command in {"!삭제", "!삭제목록"}:
        if chat.id == ACTIVE_CHAT_ID:
            await show_delete_list(msg, chat.id)
        else:
            await force_delete(msg, chat.id, context)
    elif command == "!강제삭제":
        await force_delete(msg, chat.id, context)
    elif command == "!초기화":
        await reset_store(msg, chat.id, argument)


async def cmd_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg and update.effective_chat and update.effective_chat.id == ACTIVE_CHAT_ID:
        await mark_as_used(msg, context)
    elif msg:
        await msg.reply_text("!사용은 사용 방에서 기프티콘 원본에 답장해 실행하세요.")


async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg and update.effective_chat:
        await restore_gifticon(msg, update.effective_chat.id, context)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await show_list(update.effective_message, "available")


async def cmd_unused(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await show_list(update.effective_message, "available")


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg:
        query = " ".join(context.args).strip()
        if not query:
            await msg.reply_text("사용법: /search <검색어>")
        else:
            await show_list(msg, "available", query)


async def cmd_expiring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    try:
        days = int(context.args[0]) if context.args else 30
        if days < 0 or days > 3650:
            raise ValueError
    except ValueError:
        await msg.reply_text("사용법: /expiring [남은 일수] (기본값 30)")
        return
    await show_expiring(msg, days, include_expired=False)


async def cmd_expired(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await show_expiring(update.effective_message, 0, include_expired=True)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    if chat.id not in {ACTIVE_CHAT_ID, ARCHIVE_CHAT_ID}:
        return
    replied = msg.reply_to_message
    if replied is None:
        await msg.reply_text("삭제할 기프티콘 메시지에 답장해서 /delete 를 입력하세요.")
        return
    gifticon = (
        await STORE.get_by_archive(replied.message_id)
        if chat.id == ARCHIVE_CHAT_ID
        else await STORE.get_by_source(replied.message_id)
    )
    if gifticon is None:
        await msg.reply_text("답장한 메시지는 보관된 기프티콘이 아닙니다.")
        return
    if gifticon.status != "used":
        await msg.reply_text("안전상 사용 완료된 기프티콘만 /delete 할 수 있습니다.")
        return
    if not await STORE.delete(gifticon.source_message_id):
        await msg.reply_text("DB 항목을 삭제하지 못했습니다.")
        return
    try:
        await context.bot.delete_message(ARCHIVE_CHAT_ID, gifticon.archive_message_id)
        archive_note = "이력 방 메시지도 삭제했습니다."
    except TelegramError:
        archive_note = "DB에서는 삭제했지만 이력 방 메시지 삭제에는 실패했습니다."
    await msg.reply_text(f"사용 완료 기프티콘을 강제 삭제했습니다. {archive_note}")


async def force_delete(msg, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a tracked gifticon using its archive copy, even if the source is gone."""
    replied = msg.reply_to_message
    if replied is None:
        await msg.reply_text("이력 방의 보관 메시지에 답장해서 !강제삭제를 입력하세요.")
        return
    gifticon = (
        await STORE.get_by_archive(replied.message_id)
        if chat_id == ARCHIVE_CHAT_ID
        else await STORE.get_by_source(replied.message_id)
    )
    if gifticon is None:
        await msg.reply_text("답장한 메시지는 보관 DB에 등록된 기프티콘이 아닙니다.")
        return
    if not await STORE.delete(gifticon.source_message_id):
        await msg.reply_text("DB 항목을 삭제하지 못했습니다.")
        return
    deleted_messages = []
    for target_chat, message_id in (
        (ARCHIVE_CHAT_ID, gifticon.archive_message_id),
        (ACTIVE_CHAT_ID, gifticon.source_message_id),
    ):
        try:
            await context.bot.delete_message(target_chat, message_id)
            deleted_messages.append(message_id)
        except TelegramError:
            pass
    await msg.reply_text(
        "DB에서 강제 삭제했습니다. "
        + ("관련 Telegram 메시지도 삭제했습니다." if deleted_messages else "Telegram 메시지는 이미 없거나 삭제 권한이 없습니다.")
    )


async def show_delete_list(msg, chat_id: int) -> None:
    if chat_id != ACTIVE_CHAT_ID:
        await msg.reply_text("!삭제는 사용방에서 실행하세요.")
        return
    records = await STORE.list()
    if not records:
        await msg.reply_text("🗑 삭제할 기프티콘이 없습니다.", reply_markup=MAIN_KEYBOARD)
        return

    # Sort available first, then used; newest first
    records.sort(key=lambda r: (0 if r.status == "available" else 1))

    buttons = []
    for item in records[:30]:
        state = "미사용" if item.status == "available" else "사용완료"
        label = gifticon_label(item)
        buttons.append([InlineKeyboardButton(f"🗑 [{state}] {label[:40]}", callback_data=f"delete_pick:{item.source_message_id}")])
    buttons.append([InlineKeyboardButton("❌ 취소", callback_data="delete_cancel")])
    await msg.reply_text(
        "🗑 <b>삭제할 기프티콘을 선택하세요:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


async def delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE, source_id: int) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    if query.message.chat.id != ACTIVE_CHAT_ID:
        await query.answer("사용방에서만 삭제할 수 있습니다.", show_alert=True)
        return
    gifticon = await STORE.get_by_source(source_id)
    if gifticon is None:
        await query.answer("이미 삭제되었거나 찾을 수 없는 기프티콘입니다.", show_alert=True)
        await query.edit_message_text("⚠️ 이미 삭제되었거나 찾을 수 없는 기프티콘입니다.")
        return
    label = gifticon_label(gifticon)
    if not await STORE.delete(source_id):
        await query.answer("DB 항목을 삭제하지 못했습니다.", show_alert=True)
        await query.edit_message_text("❌ DB 항목을 삭제하지 못했습니다.")
        return
    deleted_messages = []
    for target_chat, message_id in (
        (ARCHIVE_CHAT_ID, gifticon.archive_message_id),
        (ACTIVE_CHAT_ID, gifticon.source_message_id),
    ):
        try:
            await context.bot.delete_message(target_chat, message_id)
            deleted_messages.append(message_id)
        except TelegramError:
            pass
    await query.answer(f"삭제 완료")
    await query.edit_message_text(
        f"🗑 <b>{label}</b> 기프티콘을 삭제했습니다."
        + ("\n(관련 메시지도 삭제되었습니다.)" if deleted_messages else ""),
        parse_mode=ParseMode.HTML,
    )


async def reset_store(msg, chat_id: int, argument: str) -> None:
    if chat_id != ARCHIVE_CHAT_ID:
        await msg.reply_text("!초기화는 이력 방에서만 실행할 수 있습니다.")
        return
    if argument != "확인":
        await msg.reply_text("개발용 전체 초기화: !초기화 확인")
        return
    count = await STORE.clear()
    await msg.reply_text(
        f"DB의 기프티콘 목록 {count}개를 초기화했습니다. "
        "이력 방의 Telegram 메시지는 삭제하지 않았습니다."
    )


async def gifticon_for_reply(msg, chat_id: int) -> Gifticon | None:
    """Resolve a gifticon from a direct reply or a reply to the archive notice."""
    replied = msg.reply_to_message
    if replied is None or chat_id not in {ACTIVE_CHAT_ID, ARCHIVE_CHAT_ID}:
        return None

    message_ids = [replied.message_id]
    if replied.reply_to_message is not None:
        message_ids.append(replied.reply_to_message.message_id)
    for message_id in message_ids:
        gifticon = (
            await STORE.get_by_source(message_id)
            if chat_id == ACTIVE_CHAT_ID
            else await STORE.get_by_archive(message_id)
        )
        if gifticon is not None:
            return gifticon
    return None


async def delete_used_all(msg, chat_id: int, argument: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete every used gifticon after an explicit confirmation in the archive chat."""
    if chat_id != ARCHIVE_CHAT_ID:
        await msg.reply_text("!사용완료삭제는 이력 방에서만 실행할 수 있습니다.")
        return
    if argument != "확인":
        await msg.reply_text("사용 완료 목록 전체 삭제: !사용완료삭제 확인")
        return

    records = await STORE.list(status="used")
    if not records:
        await msg.reply_text("삭제할 사용 완료 기프티콘이 없습니다.")
        return

    deleted = 0
    telegram_deleted = 0
    for gifticon in records:
        if await STORE.delete(gifticon.source_message_id):
            deleted += 1
        for target_chat, message_id in (
            (ARCHIVE_CHAT_ID, gifticon.archive_message_id),
            (ACTIVE_CHAT_ID, gifticon.source_message_id),
        ):
            try:
                await context.bot.delete_message(target_chat, message_id)
                telegram_deleted += 1
            except TelegramError:
                pass
    await msg.reply_text(
        f"사용 완료 기프티콘 {deleted}개를 DB에서 삭제했습니다. "
        f"관련 Telegram 메시지는 {telegram_deleted}개 삭제했습니다."
    )


async def update_expiry(msg, chat_id: int, argument: str) -> None:
    """Update or clear the expiry date by replying to a tracked gifticon."""
    if chat_id not in {ACTIVE_CHAT_ID, ARCHIVE_CHAT_ID}:
        return
    if msg.reply_to_message is None:
        await msg.reply_text("기프티콘 메시지에 답장하고 유효기간 YYYY-MM-DD를 입력하세요.")
        return

    gifticon = await gifticon_for_reply(msg, chat_id)
    if gifticon is None:
        await msg.reply_text("답장한 메시지는 보관된 기프티콘이 아닙니다.")
        return

    value = argument.strip()
    if value in {"없음", "미인식", "삭제"}:
        expiry = None
    else:
        expiry = parse_manual_expiry(value)
        if expiry is None:
            await msg.reply_text("사용법: 유효기간 YYYY-MM-DD (삭제하려면 유효기간 없음)")
            return

    if not await STORE.set_expiry(gifticon.source_message_id, expiry.isoformat() if expiry else None):
        await msg.reply_text("유효기간을 수정하지 못했습니다.")
        return
    await msg.reply_text(f"유효기간을 {expiry.isoformat() if expiry else '미인식'}으로 수정했습니다.")


async def update_title(msg, chat_id: int, argument: str) -> None:
    """Set or clear the custom title shown in lists and notices."""
    if chat_id not in {ACTIVE_CHAT_ID, ARCHIVE_CHAT_ID}:
        return
    if msg.reply_to_message is None:
        await msg.reply_text("기프티콘 메시지에 답장하고 제목 새 제목을 입력하세요.")
        return

    gifticon = await gifticon_for_reply(msg, chat_id)
    if gifticon is None:
        await msg.reply_text("답장한 메시지는 보관된 기프티콘이 아닙니다.")
        return

    title = argument.strip()
    if title in {"없음", "삭제", "자동"}:
        title = None
    elif not title:
        await msg.reply_text("사용법: 제목 새 제목 (자동 요약으로 되돌리려면 제목 없음)")
        return
    else:
        title = title[:120]

    if not await STORE.set_title(gifticon.source_message_id, title):
        await msg.reply_text("제목을 수정하지 못했습니다.")
        return
    await msg.reply_text(f"목록 제목을 {title or '자동 요약'}으로 수정했습니다.")


async def update_compact_metadata(msg, chat_id: int, raw: str) -> None:
    """Set title and/or expiry from a short reply message."""
    if chat_id not in {ACTIVE_CHAT_ID, ARCHIVE_CHAT_ID}:
        return
    if msg.reply_to_message is None:
        return

    gifticon = await gifticon_for_reply(msg, chat_id)
    if gifticon is None:
        await msg.reply_text("답장한 메시지는 보관된 기프티콘이 아닙니다.")
        return

    match = re.search(
        r"(?<!\d)(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}[./-]\d{1,2})(?!\d)",
        raw,
    )
    expiry = gifticon.expiry_date
    title = raw
    if match is not None:
        try:
            expiry = parse_manual_expiry(match.group(0))
        except ValueError:
            expiry = None
        if expiry is None:
            await msg.reply_text("유효기간은 YYYY-MM-DD 또는 M/D 형식으로 입력하세요.")
            return
        title = raw[:match.start()] + " " + raw[match.end():]

    title = re.sub(r"\s+", " ", title).strip(" -_/|:")[:120]
    if not title and match is not None:
        title = gifticon.title
    if not title and expiry is None:
        await msg.reply_text("제목 또는 유효기간을 입력하세요.")
        return

    expiry_value = expiry.isoformat() if isinstance(expiry, date) else expiry
    if not await STORE.set_title_and_expiry(gifticon.source_message_id, title, expiry_value):
        await msg.reply_text("제목과 유효기간을 수정하지 못했습니다.")
        return
    if match is None:
        await msg.reply_text(f"목록 제목을 '{title}'로 수정했습니다.")
        return
    if not title:
        await msg.reply_text(f"유효기간을 {expiry_value}로 수정했습니다.")
        return
    await msg.reply_text(f"목록 제목을 '{title}', 유효기간을 {expiry_value}으로 수정했습니다.")


async def request_use(msg, chat_id: int) -> None:
    if chat_id != ACTIVE_CHAT_ID:
        await msg.reply_text("!사용은 사용 방에서 원본 기프티콘에 답장해 실행하세요.")
        return
    replied = msg.reply_to_message
    if replied is None:
        await msg.reply_text("사용할 기프티콘 메시지에 답장하여 !사용을 입력하세요.")
        return
    gifticon = await STORE.get_by_source(replied.message_id)
    if gifticon is None:
        await msg.reply_text("이 메시지는 보관된 기프티콘이 아닙니다.")
        return
    if gifticon.status == "used":
        await msg.reply_text("이미 사용 처리된 기프티콘입니다.")
        return
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("사용 완료 및 삭제", callback_data=f"use:{gifticon.source_message_id}"),
            InlineKeyboardButton("취소", callback_data="cancel"),
        ]]
    )
    await msg.reply_text("사용 완료로 처리하고 사용 방의 원본을 삭제할까요?", reply_markup=keyboard)


async def mark_as_used(msg, context: ContextTypes.DEFAULT_TYPE) -> None:
    replied = msg.reply_to_message
    if replied is None:
        return
    gifticon = await gifticon_for_reply(msg, ACTIVE_CHAT_ID)
    if gifticon is None:
        await msg.reply_text("답장한 메시지는 보관된 기프티콘이 아닙니다.")
        return
    if gifticon.status == "used":
        await msg.reply_text("이미 사용 처리된 기프티콘입니다.")
        return

    user = msg.from_user
    used_by = (user.full_name if user else None) or (user.username if user else None) or "알 수 없음"
    if not await STORE.mark_used(gifticon.source_message_id, used_by):
        await msg.reply_text("이미 처리되었거나 찾을 수 없는 기프티콘입니다.")
        return
    label = gifticon_label(gifticon)
    try:
        await context.bot.delete_message(ACTIVE_CHAT_ID, gifticon.source_message_id)
        deletion = "원본 메시지도 삭제했습니다."
    except BadRequest as error:
        logger.warning("Failed to delete source message %s: %s", gifticon.source_message_id, error)
        err_msg = str(error).lower()
        if "can't be deleted" in err_msg or "message to delete not found" in err_msg:
            deletion = "사용 처리는 완료되었습니다.\n(텔레그램 정책상 48시간이 지난 원본 메시지는 봇이 삭제할 수 없어 직접 삭제해 주셔야 합니다.)"
        else:
            deletion = f"사용 처리는 했지만 원본 삭제에 실패했습니다. ({error.message})"
    await context.bot.send_message(
        ARCHIVE_CHAT_ID,
        f"사용 완료: {label}\n처리자: {used_by} (원본 메시지 #{gifticon.source_message_id})",
    )
    await msg.reply_text(f"사용 처리했습니다: {label}\n{deletion}")


async def confirm_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    if query.data and query.data.startswith("delete_pick:"):
        source_id = int(query.data.split(":", 1)[1])
        await delete_selected(update, context, source_id)
        return
    if query.data == "delete_cancel":
        await query.answer("삭제를 취소했습니다.")
        await query.edit_message_text("❌ 삭제가 취소되었습니다.")
        return
    if query.data and query.data.startswith("delete_confirm:"):
        await delete_selected(update, context, int(query.data.split(":", 1)[1]))
        return
    if query.data == "cancel":
        await query.edit_message_text("사용 처리를 취소했습니다.")
        return
    if not query.data or not query.data.startswith("use:"):
        return
    source_id = int(query.data.split(":", 1)[1])
    gifticon = await STORE.get_by_source(source_id)
    if gifticon is None:
        await query.edit_message_text("이미 삭제되었거나 찾을 수 없는 기프티콘입니다.")
        return
    user = query.from_user
    used_by = user.full_name or user.username or str(user.id)
    if not await STORE.mark_used(source_id, used_by):
        await query.edit_message_text("이미 처리되었거나 찾을 수 없는 기프티콘입니다.")
        return
    label = gifticon_label(gifticon)
    try:
        await context.bot.delete_message(ACTIVE_CHAT_ID, source_id)
        deletion = "사용 방의 원본을 삭제했습니다."
    except BadRequest as error:
        logger.warning("Failed to delete source message %s: %s", source_id, error)
        err_msg = str(error).lower()
        if "can't be deleted" in err_msg or "message to delete not found" in err_msg:
            deletion = "사용 처리는 기록되었습니다.\n(텔레그램 정책상 48시간이 지난 원본 메시지는 직접 삭제해 주셔야 합니다.)"
        else:
            deletion = f"사용 처리는 기록했지만 원본 삭제에 실패했습니다. ({error.message})"
    await context.bot.send_message(
        ARCHIVE_CHAT_ID,
        f"사용 완료: {label}\n처리자: {used_by} (원본 메시지 #{source_id})",
    )
    await query.edit_message_text(f"사용 완료로 기록했습니다: {label}\n{deletion}")


async def restore_gifticon(msg, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    if chat_id != ARCHIVE_CHAT_ID:
        await msg.reply_text("!복구는 이력 방의 보관 메시지에 답장해 실행하세요.")
        return
    replied = msg.reply_to_message
    if replied is None:
        await msg.reply_text("복구할 이력 메시지에 답장하여 !복구를 입력하세요.")
        return
    gifticon = await STORE.get_by_archive(replied.message_id)
    if gifticon is None:
        await msg.reply_text("이 메시지는 봇이 보관한 기프티콘이 아닙니다.")
        return
    try:
        copied = await context.bot.copy_message(
            chat_id=ACTIVE_CHAT_ID,
            from_chat_id=ARCHIVE_CHAT_ID,
            message_id=gifticon.archive_message_id,
        )
        await STORE.restore(gifticon.source_message_id, copied.message_id)
        await msg.reply_text("사용 방으로 복구했고 다시 미사용 상태로 표시했습니다.")
    except TelegramError:
        logger.exception("Could not restore archived message %s", gifticon.archive_message_id)
        await msg.reply_text("복구에 실패했습니다. 봇의 두 채팅방 접근 권한을 확인해 주세요.")


async def show_list(msg, status: str | None, query: str = "") -> None:
    status = status or "available"
    records = await STORE.list(status=status, query=query)
    # The normal unused list and search should only contain usable gifticons.
    # Expired entries remain accessible through !만료 (/expired).
    if status == "available":
        today = kst_today()
        records = [
            item
            for item in records
            if (expiry := gifticon_expiry(item)) is None or expiry >= today
        ]
    if not records:
        await msg.reply_text("조건에 맞는 기프티콘이 없습니다.")
        return
    lines = [f"{len(records)}개 기프티콘"]
    for index, item in enumerate(records[:50], start=1):
        state = "미사용" if item.status == "available" else f"사용 ({html.escape(item.used_by or '알 수 없음')})"
        expiry = gifticon_expiry(item)
        summary = html.escape(gifticon_summary(item.description, expiry, item.title))
        open_link = gifticon_open_link(item)
        lines.append(f"{index}. [{state}] {summary}{open_link}")
    if len(records) > 50:
        lines.append(f"… 나머지 {len(records) - 50}개는 검색으로 좁혀 보세요.")
    await msg.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        **no_preview_kwargs(),
    )


async def show_expiring(msg, days: int, include_expired: bool) -> None:
    records = await STORE.list(status="available")
    today = kst_today()
    cutoff = today + timedelta(days=days)
    matches = []
    for item in records:
        expiry = gifticon_expiry(item)
        if expiry is None:
            continue
        if include_expired and expiry < today:
            matches.append((item, expiry))
        elif not include_expired and today <= expiry <= cutoff:
            matches.append((item, expiry))
    matches.sort(key=lambda pair: pair[1])
    if not matches:
        label = "이미 만료된" if include_expired else f"앞으로 {days}일 이내 만료되는"
        await msg.reply_text(f"{label} 미사용 기프티콘이 없습니다.")
        return
    lines = [f"{len(matches)}개 기프티콘"]
    for index, (item, expiry) in enumerate(matches[:50], start=1):
        remaining = (expiry - today).days
        summary = html.escape(gifticon_summary(item.description, expiry, item.title))
        open_link = gifticon_open_link(item)
        lines.append(f"{index}. {summary} ({remaining}일 남음){open_link}")
    await msg.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        **no_preview_kwargs(),
    )


async def notify_expiring(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Notify the normal use room once daily about gifts expiring soon."""
    logger.info("Running daily expiry notification job...")
    settings = await STORE.get_notification_settings()
    if not settings.enabled:
        logger.info("Daily expiry notification is disabled.")
        return 0
    count = await _send_expiry_notification(context, settings.days, claim=True)
    logger.info("Daily expiry notification completed. Sent for %d gifticons.", count)
    return count


async def _send_expiry_notification(
    context: ContextTypes.DEFAULT_TYPE, days: int, claim: bool
) -> int:
    """Send an expiry notice and return the number of included gifticons."""
    today = kst_today()
    cutoff = today + timedelta(days=days)
    records = await STORE.list(status="available")
    matches = []
    for item in records:
        expiry = gifticon_expiry(item)
        if expiry and today <= expiry <= cutoff:
            if not claim or await STORE.claim_expiry_notification(item.source_message_id, today.isoformat()):
                matches.append((item, expiry))
    if not matches:
        return 0
    matches.sort(key=lambda pair: pair[1])
    lines = [
        "⚠️ 유효기간 임박 기프티콘 알림",
        f"{days}일 이내 만료 예정인 미사용 기프티콘입니다.",
    ]
    for index, (item, expiry) in enumerate(matches[:50], start=1):
        remaining = (expiry - today).days
        summary = html.escape(gifticon_summary(item.description, expiry, item.title))
        open_link = gifticon_open_link(item)
        lines.append(f"{index}. {summary} ({remaining}일 남음){open_link}")
    try:
        await context.bot.send_message(
            ACTIVE_CHAT_ID,
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            **no_preview_kwargs(),
        )
    except TelegramError:
        logger.exception("Could not send expiry notification to active chat %s", ACTIVE_CHAT_ID)
        return 0
    return len(matches)


def parse_notification_time(value: str) -> time | None:
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        return None
    return parsed.replace(tzinfo=KST)


async def schedule_expiry_job(job_queue) -> None:
    """Replace the daily job using the persisted notification settings."""
    if job_queue is None:
        logger.warning("Job queue is not available; cannot schedule expiry notification.")
        return
    for job in job_queue.get_jobs_by_name(NOTIFICATION_JOB_NAME):
        job.schedule_removal()
    settings = await STORE.get_notification_settings()
    if settings.enabled:
        send_time = parse_notification_time(settings.send_time)
        if send_time is None:
            logger.error("Invalid persisted notification time: %s", settings.send_time)
            return
        job_queue.run_daily(notify_expiring, time=send_time, name=NOTIFICATION_JOB_NAME)
        logger.info(
            "Scheduled daily expiry notification: %s (KST), criteria <= %d days",
            settings.send_time,
            settings.days,
        )
    else:
        logger.info("Daily expiry notification is disabled in settings.")


async def load_expiry_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await schedule_expiry_job(context.application.job_queue)


async def update_notification_settings(
    msg, chat_id: int, argument: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show or update the persisted expiry-notification settings."""
    if chat_id != ARCHIVE_CHAT_ID:
        await msg.reply_text("알림 설정은 이력 방에서만 변경할 수 있습니다.")
        return

    settings = await STORE.get_notification_settings()
    value = argument.strip()
    if not value:
        state = "켜짐" if settings.enabled else "꺼짐"
        await msg.reply_text(
            f"유효기간 알림: {state}\n"
            f"기준 기간: {settings.days}일 이내\n"
            f"발송 시각: 매일 {settings.send_time} (KST)\n\n"
            "변경: !알림설정 7일 09:00\n"
            "끄기: !알림설정 끄기 / 켜기: !알림설정 켜기"
        )
        return

    if value in {"끄기", "off", "disable"}:
        await STORE.set_notification_settings(False, settings.days, settings.send_time)
        await schedule_expiry_job(context.application.job_queue)
        await msg.reply_text("유효기간 알림을 껐습니다.")
        return

    if value in {"켜기", "on", "enable"}:
        await STORE.set_notification_settings(True, settings.days, settings.send_time)
        await schedule_expiry_job(context.application.job_queue)
        await msg.reply_text(
            f"유효기간 알림을 켰습니다. 매일 {settings.send_time} (KST), "
            f"{settings.days}일 이내 기준입니다."
        )
        return

    parts = value.split()
    days = settings.days
    send_time = settings.send_time
    for part in parts:
        if re.fullmatch(r"\d+일?", part):
            days = int(part.rstrip("일"))
        elif re.fullmatch(r"\d{1,2}:\d{2}", part):
            if parse_notification_time(part) is None:
                await msg.reply_text("발송 시각은 00:00부터 23:59까지 입력하세요.")
                return
            send_time = part
        else:
            await msg.reply_text(
                "사용법: !알림설정 [7일] [09:00]\n"
                "예: !알림설정 7일 08:30\n"
                "끄기/켜기: !알림설정 끄기 또는 !알림설정 켜기"
            )
            return
    if days < 0 or days > 3650:
        await msg.reply_text("알림 기간은 0일부터 3650일까지 입력하세요.")
        return
    await STORE.set_notification_settings(True, days, send_time)
    await schedule_expiry_job(context.application.job_queue)
    await msg.reply_text(
        f"유효기간 알림을 저장했습니다. 매일 {send_time} (KST), {days}일 이내 기준입니다."
    )


async def test_expiry_notification(
    msg, chat_id: int, argument: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Send a real-looking test notice without consuming today's daily claim."""
    if chat_id != ARCHIVE_CHAT_ID:
        await msg.reply_text("알림 테스트는 이력 방에서만 실행할 수 있습니다.")
        return
    settings = await STORE.get_notification_settings()
    days = settings.days
    if argument:
        if not re.fullmatch(r"\d+", argument):
            await msg.reply_text("사용법: !알림테스트 또는 !알림테스트 7")
            return
        days = int(argument)
    if days < 0 or days > 3650:
        await msg.reply_text("테스트 기간은 0일부터 3650일까지 입력하세요.")
        return
    count = await _send_expiry_notification(context, days, claim=False)
    if count:
        await msg.reply_text(f"알림 테스트를 보냈습니다. 대상 쿠폰: {count}개")
    else:
        await msg.reply_text(f"{days}일 이내 만료 예정인 미사용 쿠폰이 없습니다.")


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "도움말 및 하단 버튼 활성화"),
        BotCommand("unused", "미사용 기프티콘 목록"),
        BotCommand("expiring", "만료 임박 기프티콘"),
        BotCommand("expired", "만료된 기프티콘"),
        BotCommand("chatid", "현재 채팅방 ID 확인"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception:
        logger.exception("Could not set bot commands")
    await schedule_expiry_job(application.job_queue)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error("Set TELEGRAM_BOT_TOKEN in the environment or .env file.")
        sys.exit(1)
    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("chatid", cmd_chat_id))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("unused", cmd_unused))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("expiring", cmd_expiring))
    app.add_handler(CommandHandler("expired", cmd_expired))
    app.add_handler(CallbackQueryHandler(confirm_use))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_management_message), group=0)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, archive_message), group=1)
    logger.info("Polling started for active=%s archive=%s", ACTIVE_CHAT_ID, ARCHIVE_CHAT_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
