import asyncio
import os
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

os.environ.setdefault("GIFTICON_ACTIVE_CHAT_ID", "-1001234567890")
os.environ.setdefault("GIFTICON_ARCHIVE_CHAT_ID", "-1009876543210")

try:
    from bot import (
        Gifticon,
        gifticon_open_button,
        gifticon_open_link,
        is_compact_metadata_update_request,
        is_use_request,
        mark_as_used,
        show_list,
    )
except ModuleNotFoundError as error:
    BOT_IMPORT_ERROR = str(error)
else:
    BOT_IMPORT_ERROR = ""


class ReplyCapture:
    def __init__(self) -> None:
        self.text = ""
        self.kwargs = {}

    async def reply_text(self, text: str, **kwargs) -> None:
        self.text = text
        self.kwargs = kwargs


@unittest.skipIf(BOT_IMPORT_ERROR, f"bot dependencies unavailable: {BOT_IMPORT_ERROR}")
class ListVisibilityTests(unittest.TestCase):
    def test_available_list_hides_expired_gifticons(self) -> None:
        records = [
            Gifticon(1, 101, "expired", "expired", "available", None, None, "2026-09-13"),
            Gifticon(2, 102, "current", "current", "available", None, None, "2026-09-14"),
            Gifticon(3, 103, "unknown", "unknown", "available", None, None, None),
        ]

        async def scenario() -> ReplyCapture:
            message = ReplyCapture()
            with (
                patch("bot.STORE.list", new=AsyncMock(return_value=records)),
                patch("bot.kst_today", return_value=date(2026, 9, 14)),
            ):
                await show_list(message, "available")
            return message

        message = asyncio.run(scenario())
        self.assertNotIn("expired", message.text)
        self.assertIn("current", message.text)
        self.assertIn("unknown", message.text)
        self.assertIn('<a href="https://t.me/c/1234567890/2">열기</a>', message.text)
        self.assertIn('<a href="https://t.me/c/1234567890/3">열기</a>', message.text)
        self.assertNotIn("reply_markup", message.kwargs)

    def test_reply_button_command_routing(self) -> None:
        class MockMsg:
            def __init__(self, text, reply_to_message=None):
                self.text = text
                self.reply_to_message = reply_to_message

        dummy_reply = object()

        # Keyboard commands starting with ! should NOT be treated as use requests
        self.assertFalse(is_use_request(MockMsg("!미사용", dummy_reply)))
        self.assertFalse(is_use_request(MockMsg("!임박", dummy_reply)))
        self.assertFalse(is_use_request(MockMsg("!삭제", dummy_reply)))
        self.assertFalse(is_use_request(MockMsg("!목록", dummy_reply)))
        self.assertFalse(is_use_request(MockMsg("미사용", dummy_reply)))

        # Explicit use commands
        self.assertTrue(is_use_request(MockMsg("!사용", dummy_reply)))
        self.assertTrue(is_use_request(MockMsg("사용", dummy_reply)))
        self.assertTrue(is_use_request(MockMsg("사용완료", dummy_reply)))
        self.assertTrue(is_use_request(MockMsg("사용 완료", dummy_reply)))

        # Without reply_to_message, is_use_request is always False
        self.assertFalse(is_use_request(MockMsg("!사용", None)))
        self.assertFalse(is_use_request(MockMsg("사용", None)))

        # Metadata update should not match ! commands or use requests
        self.assertFalse(is_compact_metadata_update_request(MockMsg("!미사용", dummy_reply)))
        self.assertFalse(is_compact_metadata_update_request(MockMsg("!삭제", dummy_reply)))
        self.assertFalse(is_compact_metadata_update_request(MockMsg("사용", dummy_reply)))
        self.assertFalse(is_compact_metadata_update_request(MockMsg("!사용", dummy_reply)))
        self.assertTrue(is_compact_metadata_update_request(MockMsg("스타벅스 10/10", dummy_reply)))

    def test_gifticon_open_link_format(self) -> None:
        item = Gifticon(101, 1, "스타벅스 카페아메리카노", "스타벅스 카페아메리카노", "available", None, None, "2026-09-14")
        link = gifticon_open_link(item)
        self.assertEqual(link, ' <a href="https://t.me/c/1234567890/101">열기</a>')

    def test_gifticon_open_button_format(self) -> None:
        item = Gifticon(101, 1, "스타벅스 카페아메리카노", "스타벅스 카페아메리카노", "available", None, None, "2026-09-14")
        button = gifticon_open_button(item, 1)
        self.assertIsNotNone(button)
        self.assertEqual(button.text, "열기 · 1. 스타벅스 카페아메리카노")
        self.assertEqual(button.url, "https://t.me/c/1234567890/101")

    def test_mark_as_used_success(self) -> None:
        from telegram.error import BadRequest

        class DummyUser:
            full_name = "홍길동"
            username = "hong"

        class DummyReplied:
            message_id = 101
            reply_to_message = None

        class DummyMsg:
            from_user = DummyUser()
            reply_to_message = DummyReplied()

            def __init__(self):
                self.replied_text = ""

            async def reply_text(self, text, **kwargs):
                self.replied_text = text

        class DummyBot:
            def __init__(self):
                self.deleted = []
                self.sent = []

            async def delete_message(self, chat_id, message_id):
                self.deleted.append((chat_id, message_id))

            async def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text))

        class DummyContext:
            def __init__(self):
                self.bot = DummyBot()

        item = Gifticon(101, 1, "스타벅스 카페아메리카노", "스타벅스 카페아메리카노", "available", None, None, "2026-09-14")
        msg = DummyMsg()
        context = DummyContext()

        with patch("bot.gifticon_for_reply", new=AsyncMock(return_value=item)), \
             patch("bot.STORE.mark_used", new=AsyncMock(return_value=True)):
            asyncio.run(mark_as_used(msg, context))

        self.assertIn("원본 메시지도 삭제했습니다", msg.replied_text)
        self.assertEqual(context.bot.deleted, [(-1001234567890, 101)])

    def test_mark_as_used_older_than_48h_error_message(self) -> None:
        from telegram.error import BadRequest

        class DummyUser:
            full_name = "홍길동"
            username = "hong"

        class DummyReplied:
            message_id = 101
            reply_to_message = None

        class DummyMsg:
            from_user = DummyUser()
            reply_to_message = DummyReplied()

            def __init__(self):
                self.replied_text = ""

            async def reply_text(self, text, **kwargs):
                self.replied_text = text

        class DummyBot:
            async def delete_message(self, chat_id, message_id):
                raise BadRequest("Message can't be deleted")

            async def send_message(self, chat_id, text, **kwargs):
                pass

        class DummyContext:
            def __init__(self):
                self.bot = DummyBot()

        item = Gifticon(101, 1, "스타벅스 카페아메리카노", "스타벅스 카페아메리카노", "available", None, None, "2026-09-14")
        msg = DummyMsg()
        context = DummyContext()

        with patch("bot.gifticon_for_reply", new=AsyncMock(return_value=item)), \
             patch("bot.STORE.mark_used", new=AsyncMock(return_value=True)):
            asyncio.run(mark_as_used(msg, context))

        self.assertIn("48시간이 지난 원본 메시지는 봇이 삭제할 수 없어", msg.replied_text)
        self.assertNotIn("봇의 관리자 권한을 확인하세요", msg.replied_text)


if __name__ == "__main__":
    unittest.main()
