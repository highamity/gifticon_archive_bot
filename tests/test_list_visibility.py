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


if __name__ == "__main__":
    unittest.main()
