import asyncio
import os
import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("GIFTICON_ACTIVE_CHAT_ID", "-1001234567890")
os.environ.setdefault("GIFTICON_ARCHIVE_CHAT_ID", "-1009876543210")

try:
    from bot import (
        Gifticon,
        NOTIFICATION_JOB_NAME,
        notify_expiring,
        post_init,
        schedule_expiry_job,
    )
    from storage import NotificationSettings
except ModuleNotFoundError as error:
    BOT_IMPORT_ERROR = str(error)
else:
    BOT_IMPORT_ERROR = ""


@unittest.skipIf(BOT_IMPORT_ERROR, f"bot dependencies unavailable: {BOT_IMPORT_ERROR}")
class NotificationTests(unittest.TestCase):
    def test_schedule_expiry_job_enabled(self) -> None:
        job_queue = MagicMock()
        job_queue.get_jobs_by_name.return_value = []
        settings = NotificationSettings(enabled=True, days=7, send_time="09:00")

        with patch("bot.STORE.get_notification_settings", new=AsyncMock(return_value=settings)):
            asyncio.run(schedule_expiry_job(job_queue))

        job_queue.run_daily.assert_called_once()
        _, kwargs = job_queue.run_daily.call_args
        self.assertEqual(kwargs.get("name"), NOTIFICATION_JOB_NAME)
        self.assertEqual(kwargs.get("time").hour, 9)
        self.assertEqual(kwargs.get("time").minute, 0)

    def test_schedule_expiry_job_disabled(self) -> None:
        existing_job = MagicMock()
        job_queue = MagicMock()
        job_queue.get_jobs_by_name.return_value = [existing_job]
        settings = NotificationSettings(enabled=False, days=7, send_time="09:00")

        with patch("bot.STORE.get_notification_settings", new=AsyncMock(return_value=settings)):
            asyncio.run(schedule_expiry_job(job_queue))

        existing_job.schedule_removal.assert_called_once()
        job_queue.run_daily.assert_not_called()

    def test_post_init_schedules_expiry_job(self) -> None:
        application = MagicMock()
        application.bot.set_my_commands = AsyncMock()
        application.job_queue = MagicMock()
        application.job_queue.get_jobs_by_name.return_value = []
        settings = NotificationSettings(enabled=True, days=7, send_time="09:00")

        with patch("bot.STORE.get_notification_settings", new=AsyncMock(return_value=settings)):
            asyncio.run(post_init(application))

        application.bot.set_my_commands.assert_called_once()
        application.job_queue.run_daily.assert_called_once()

    def test_notify_expiring_sends_message_for_matching_gifticons(self) -> None:
        context = MagicMock()
        context.bot.send_message = AsyncMock()
        settings = NotificationSettings(enabled=True, days=7, send_time="09:00")
        records = [
            Gifticon(1, 101, "item1", "item1", "available", None, None, "2026-09-18"),
            Gifticon(2, 102, "item2", "item2", "available", None, None, "2026-09-30"),  # beyond 7 days
        ]

        with (
            patch("bot.STORE.get_notification_settings", new=AsyncMock(return_value=settings)),
            patch("bot.STORE.list", new=AsyncMock(return_value=records)),
            patch("bot.STORE.claim_expiry_notification", new=AsyncMock(return_value=True)),
            patch("bot.kst_today", return_value=date(2026, 9, 17)),
        ):
            count = asyncio.run(notify_expiring(context))

        self.assertEqual(count, 1)
        context.bot.send_message.assert_called_once()
        sent_text = context.bot.send_message.call_args[0][1]
        self.assertIn("item1", sent_text)
        self.assertNotIn("item2", sent_text)


if __name__ == "__main__":
    unittest.main()
