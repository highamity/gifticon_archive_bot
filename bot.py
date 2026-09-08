"""Archive gifticons automatically and manage their use in one Telegram chat."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from storage import Gifticon, GifticonStore

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
logger = logging.getLogger("gifticon_archive_bot")

DEFAULT_DB = Path(__file__).resolve().parent / "gifticons.db"
STORE = GifticonStore(Path(os.environ.get("GIFTICON_DB_PATH", str(DEFAULT_DB))))


def configured_chat_id(name: str) -> int:
    value = os.environ.get(name, "").strip()
    try:
        return int(value)
    except ValueError:
        logger.error("Set %s to a numeric Telegram chat ID.", name)
        sys.exit(1)


ACTIVE_CHAT_ID = configured_chat_id("GIFTICON_ACTIVE_CHAT_ID")
ARCHIVE_CHAT_ID = configured_chat_id("GIFTICON_ARCHIVE_CHAT_ID")


def description_for(message) -> str:
    """Use the caption/text for human-readable lists and searches."""
    return (message.caption or message.text or "(캡션 없음)").strip()[:500]


def is_management_command(message) -> bool:
    return (message.text or "").strip().startswith("!")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            "기프티콘 보관 봇입니다. 사용 방에 올린 사진·파일·텍스트를 이력 방에 자동 보관합니다.\n\n"
            "사용할 기프티콘에 답장: !사용\n"
            "이력 방의 보관본에 답장: !복구\n"
            "!미사용 / !목록 / !검색 <단어>\n\n"
            "관리용 명령은 사용 방 또는 이력 방에서만 동작합니다."
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
    if not (msg.photo or msg.document or msg.animation or msg.video or msg.text):
        return
    try:
        copied = await context.bot.copy_message(
            chat_id=ARCHIVE_CHAT_ID,
            from_chat_id=ACTIVE_CHAT_ID,
            message_id=msg.message_id,
        )
        await STORE.add(msg.message_id, copied.message_id, description_for(msg))
        await msg.reply_text("이력 방에 보관했습니다.")
    except TelegramError:
        logger.exception("Could not archive message %s", msg.message_id)
        await msg.reply_text("보관에 실패했습니다. 봇의 이력 방 접근 권한을 확인해 주세요.")


async def on_management_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if msg is None or chat is None or chat.id not in {ACTIVE_CHAT_ID, ARCHIVE_CHAT_ID}:
        return
    raw = (msg.text or "").strip()
    if not raw.startswith("!"):
        return
    command, _, argument = raw.partition(" ")
    argument = argument.strip()
    if command == "!사용":
        await request_use(msg, chat.id)
    elif command == "!복구":
        await restore_gifticon(msg, chat.id, context)
    elif command in {"!목록", "!미사용"}:
        await show_list(msg, "available" if command == "!미사용" else None)
    elif command == "!검색":
        if not argument:
            await msg.reply_text("사용법: !검색 <단어>")
        else:
            await show_list(msg, None, argument)


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


async def confirm_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    await query.answer()
    if query.data == "cancel":
        await query.edit_message_text("사용 처리를 취소했습니다.")
        return
    if not query.data or not query.data.startswith("use:"):
        return
    source_id = int(query.data.split(":", 1)[1])
    user = query.from_user
    used_by = user.full_name or user.username or str(user.id)
    if not await STORE.mark_used(source_id, used_by):
        await query.edit_message_text("이미 처리되었거나 찾을 수 없는 기프티콘입니다.")
        return
    try:
        await context.bot.delete_message(ACTIVE_CHAT_ID, source_id)
        deletion = "사용 방의 원본을 삭제했습니다."
    except BadRequest:
        deletion = "사용 처리는 기록했지만 원본 삭제에 실패했습니다. 봇에 삭제 관리자 권한이 있는지 확인하세요."
    await context.bot.send_message(
        ARCHIVE_CHAT_ID,
        f"사용 완료: {used_by} (원본 메시지 #{source_id})",
    )
    await query.edit_message_text(f"사용 완료로 기록했습니다. {deletion}")


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
    records = await STORE.list(status=status, query=query)
    if not records:
        await msg.reply_text("조건에 맞는 기프티콘이 없습니다.")
        return
    lines = [f"{len(records)}개 기프티콘"]
    for item in records[:50]:
        state = "미사용" if item.status == "available" else f"사용 ({item.used_by or '알 수 없음'})"
        lines.append(f"- [{state}] {item.description}")
    if len(records) > 50:
        lines.append(f"… 나머지 {len(records) - 50}개는 검색으로 좁혀 보세요.")
    await msg.reply_text("\n".join(lines))


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error("Set TELEGRAM_BOT_TOKEN in the environment or .env file.")
        sys.exit(1)
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("chatid", cmd_chat_id))
    app.add_handler(CallbackQueryHandler(confirm_use))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_management_message), group=0)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, archive_message), group=1)
    logger.info("Polling started for active=%s archive=%s", ACTIVE_CHAT_ID, ARCHIVE_CHAT_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
