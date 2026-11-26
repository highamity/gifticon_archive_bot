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
        gifticon_open_link,
        is_compact_metadata_update_request,
        is_use_request,
        mark_as_used,
        restore_gifticon,
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
        # Unused entries are the norm; no per-line "[미사용]" marker.
        self.assertNotIn("[미사용]", message.text)
        self.assertIn("1. current", message.text)

    def test_used_list_keeps_user_marker(self) -> None:
        records = [
            Gifticon(4, 104, "used item", "used item", "used", "홍길동", None, "2026-09-14"),
        ]

        async def scenario() -> ReplyCapture:
            message = ReplyCapture()
            with patch("bot.STORE.list", new=AsyncMock(return_value=records)):
                await show_list(message, "used")
            return message

        message = asyncio.run(scenario())
        self.assertIn("[사용 (홍길동)] used item", message.text)

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

    def test_mark_as_used_success(self) -> None:
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

        self.assertIn("사용 처리했습니다", msg.replied_text)
        # The original message is intentionally left in the active chat.
        self.assertEqual(context.bot.deleted, [])
        self.assertEqual(len(context.bot.sent), 1)
        log_text = context.bot.sent[0][1]
        # The archive log links to the original instead of exposing its id.
        self.assertIn('<a href="https://t.me/c/1234567890/101">원본 열기</a>', log_text)
        self.assertNotIn("#101", log_text)

    def test_mark_as_used_escapes_ocr_text_in_log(self) -> None:
        class DummyUser:
            full_name = "<홍길동>"
            username = "hong"

        class DummyMsg:
            from_user = DummyUser()
            reply_to_message = type("R", (), {"message_id": 101, "reply_to_message": None})()

            async def reply_text(self, text, **kwargs):
                pass

        class DummyBot:
            def __init__(self):
                self.sent = []

            async def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text))

        item = Gifticon(101, 1, "A&B <쿠폰>", "A&B <쿠폰>", "available", None, None, "2026-09-14")
        context = type("C", (), {"bot": DummyBot()})()

        with patch("bot.gifticon_for_reply", new=AsyncMock(return_value=item)), \
             patch("bot.STORE.mark_used", new=AsyncMock(return_value=True)):
            asyncio.run(mark_as_used(DummyMsg(), context))

        log_text = context.bot.sent[0][1]
        self.assertIn("&lt;홍길동&gt;", log_text)
        self.assertIn("A&amp;B &lt;쿠폰&gt;", log_text)

    def test_restore_from_active_original_keeps_source_message(self) -> None:
        class DummyMsg:
            from_user = type("U", (), {"full_name": "홍길동", "username": "hong"})()
            reply_to_message = type("R", (), {"message_id": 101})()

            def __init__(self):
                self.replied_text = ""

            async def reply_text(self, text, **kwargs):
                self.replied_text = text

        class DummyBot:
            def __init__(self):
                self.copied = []
                self.sent = []

            async def copy_message(self, **kwargs):
                self.copied.append(kwargs)
                return type("M", (), {"message_id": 555})()

            async def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text))

        item = Gifticon(101, 1, "스타벅스 카페아메리카노", "스타벅스 카페아메리카노", "used", "홍길동", None, "2026-09-14")
        context = type("C", (), {"bot": DummyBot()})()
        restore = AsyncMock()
        msg = DummyMsg()

        with patch("bot.STORE.get_by_source", new=AsyncMock(return_value=item)), \
             patch("bot.STORE.restore", new=restore):
            asyncio.run(restore_gifticon(msg, -1001234567890, context))

        restore.assert_awaited_once_with(101, 101)
        self.assertEqual(context.bot.copied, [])
        self.assertIn("미사용으로 되돌렸습니다", msg.replied_text)
        self.assertEqual(len(context.bot.sent), 1)
        self.assertIn("복구:", context.bot.sent[0][1])

    def test_restore_from_archive_with_original_present_skips_copy(self) -> None:
        class DummyMsg:
            from_user = type("U", (), {"full_name": "홍길동", "username": "hong"})()
            reply_to_message = type("R", (), {"message_id": 900})()

            async def reply_text(self, text, **kwargs):
                self.text = text

        class DummyBot:
            def __init__(self):
                self.copied = []
                self.deleted = []

            async def forward_message(self, **kwargs):
                return type("M", (), {"message_id": 777})()

            async def delete_message(self, chat_id, message_id):
                self.deleted.append((chat_id, message_id))

            async def copy_message(self, **kwargs):
                self.copied.append(kwargs)
                return type("M", (), {"message_id": 555})()

            async def send_message(self, chat_id, text, **kwargs):
                pass

        item = Gifticon(101, 900, "스타벅스 카페아메리카노", "스타벅스 카페아메리카노", "used", "홍길동", None, "2026-09-14")
        context = type("C", (), {"bot": DummyBot()})()
        restore = AsyncMock()

        with patch("bot.STORE.get_by_archive", new=AsyncMock(return_value=item)), \
             patch("bot.STORE.restore", new=restore):
            asyncio.run(restore_gifticon(DummyMsg(), -1009876543210, context))

        restore.assert_awaited_once_with(101, 101)
        self.assertEqual(context.bot.copied, [])
        self.assertEqual(context.bot.deleted, [(-1009876543210, 777)])

    def test_restore_from_archive_without_original_copies_message(self) -> None:
        from telegram.error import BadRequest

        class DummyMsg:
            from_user = type("U", (), {"full_name": "홍길동", "username": "hong"})()
            reply_to_message = type("R", (), {"message_id": 900})()

            async def reply_text(self, text, **kwargs):
                self.text = text

        class DummyBot:
            def __init__(self):
                self.copied = []

            async def forward_message(self, **kwargs):
                raise BadRequest("Message to forward not found")

            async def copy_message(self, **kwargs):
                self.copied.append(kwargs)
                return type("M", (), {"message_id": 555})()

            async def send_message(self, chat_id, text, **kwargs):
                pass

        item = Gifticon(101, 900, "스타벅스 카페아메리카노", "스타벅스 카페아메리카노", "used", "홍길동", None, "2026-09-14")
        context = type("C", (), {"bot": DummyBot()})()
        restore = AsyncMock()
        msg = DummyMsg()

        with patch("bot.STORE.get_by_archive", new=AsyncMock(return_value=item)), \
             patch("bot.STORE.restore", new=restore):
            asyncio.run(restore_gifticon(msg, -1009876543210, context))

        restore.assert_awaited_once_with(101, 555)
        self.assertEqual(context.bot.copied[0]["message_id"], 900)
        self.assertIn("다시 올리고 미사용으로 표시했습니다", msg.text)


    def test_expiry_sort_puts_unknown_expiry_last(self) -> None:
        records = [
            Gifticon(1, 101, "later", "later", "available", None, None, "2026-10-01"),
            Gifticon(2, 102, "sooner", "sooner", "available", None, None, "2026-09-01"),
            Gifticon(3, 103, "unknown", "unknown", "available", None, None, None),
        ]

        async def scenario() -> ReplyCapture:
            message = ReplyCapture()
            with (
                patch("bot.STORE.list", new=AsyncMock(return_value=records)),
                patch("bot.kst_today", return_value=date(2026, 8, 1)),
            ):
                await show_list(message, "available", sort_by="expiry")
            return message

        message = asyncio.run(scenario())
        text = message.text
        self.assertIn("1. sooner", text)
        self.assertIn("2. later", text)
        self.assertIn("3. unknown", text)
        sooner_idx = text.index("1. sooner")
        later_idx = text.index("2. later")
        unknown_idx = text.index("3. unknown")
        self.assertLess(sooner_idx, later_idx)
        self.assertLess(later_idx, unknown_idx)

    def test_name_sort_alphabetical(self) -> None:
        # 가나다순: 가온 < 나래 < 다솜
        records = [
            Gifticon(1, 101, "나래마트 3천원권", "나래마트 3천원권", "available", None, None, "2026-10-01"),
            Gifticon(2, 102, "가온카페 아메리카노", "가온카페 아메리카노", "available", None, None, "2026-09-01"),
            Gifticon(3, 103, "다솜베이커리 세트", "다솜베이커리 세트", "available", None, None, None),
        ]

        async def scenario() -> ReplyCapture:
            message = ReplyCapture()
            with (
                patch("bot.STORE.list", new=AsyncMock(return_value=records)),
                patch("bot.kst_today", return_value=date(2026, 8, 1)),
            ):
                await show_list(message, "available", sort_by="name")
            return message

        message = asyncio.run(scenario())
        text = message.text
        self.assertIn("1. 가온카페", text)
        self.assertIn("2. 나래마트", text)
        self.assertIn("3. 다솜베이커리", text)
        self.assertLess(text.index("1. 가온카페"), text.index("2. 나래마트"))
        self.assertLess(text.index("2. 나래마트"), text.index("3. 다솜베이커리"))


if __name__ == "__main__":
    unittest.main()
