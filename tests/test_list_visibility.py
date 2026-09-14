import asyncio
import os
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

os.environ.setdefault("GIFTICON_ACTIVE_CHAT_ID", "-1001234567890")
os.environ.setdefault("GIFTICON_ARCHIVE_CHAT_ID", "-1009876543210")

try:
    from bot import Gifticon, show_list
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
        keyboard = message.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(len(keyboard), 2)
        self.assertIn("current", keyboard[0][0].text)
        self.assertTrue(keyboard[0][0].url)
        self.assertIn("unknown", keyboard[1][0].text)


if __name__ == "__main__":
    unittest.main()
